import math
import contextlib
import io
import json
import sqlite3
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

import analyze
import archive
import flow_engine
import rebal_flow
from calendar_events import events_upcoming, next_cycle, review_windows
from flow_engine import build_configs, event_has_passed, tiered_ceiling, water_fill
from market_dates import anchor_stocks, completed_adv, completed_session_date, latest_completed_date


class CompletedSessionTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.execute("CREATE TABLE daily(code TEXT, date TEXT, close REAL, shares REAL, trdval REAL)")
        self.con.executemany("INSERT INTO daily VALUES(?,?,?,?,?)", [
            ("A", "20260909", 10, 100, 100), ("B", "20260909", 20, 100, 80),
            ("A", "20260910", 12, 100, 300), ("B", "20260910", 20, 100, 0),
            ("A", "20260911", 99, 100, 0), ("B", "20260911", 99, 100, 0),
        ])

    def tearDown(self):
        self.con.close()

    def test_preopen_and_intraday_use_previous_close(self):
        for hour in (8, 10, 15):
            self.assertEqual(completed_session_date(datetime(2026, 9, 11, hour)), date(2026, 9, 10))

    def test_after_close_and_utc_conversion(self):
        self.assertEqual(completed_session_date(datetime(2026, 9, 11, 15, 30)), date(2026, 9, 11))
        self.assertEqual(completed_session_date(datetime(2026, 9, 11, 7, tzinfo=timezone.utc)), date(2026, 9, 11))

    def test_weekend_and_year_rollover(self):
        self.assertEqual(completed_session_date(datetime(2026, 9, 12, 16)), date(2026, 9, 11))
        self.assertEqual(completed_session_date(datetime(2027, 1, 1, 16)), date(2026, 12, 30))

    def test_all_market_zero_is_still_excluded_after_close(self):
        self.assertEqual(latest_completed_date(self.con, datetime(2026, 9, 11, 16)), "20260910")

    def test_real_completed_zero_for_halted_stock_remains_in_adv(self):
        adv = completed_adv(self.con, "20260910", 20)
        self.assertEqual(adv["A"], 200)
        self.assertEqual(adv["B"], 40)
        self.assertEqual(completed_adv(self.con, "20260911", 20), adv)

    def test_period_statistics_and_current_prices_share_anchor(self):
        stats = analyze.period_stats(self.con, "20260901", "20260930", "20260910")
        self.assertEqual(stats["A"]["avg_mcap"], 1100)
        self.assertEqual(stats["B"]["avg_trdval"], 40)
        self.assertEqual(stats["A"]["last"], "20260910")
        self.assertEqual(analyze.recent_avg_mcap(self.con, "A", 15, "20260910"), 1100)
        stocks = anchor_stocks(self.con, {"A": {"close": 99, "mktcap": 9900}}, "20260910")
        self.assertEqual(stocks["A"]["close"], 12)
        self.assertEqual(stocks["A"]["mktcap"], 1200)


class CalendarTests(unittest.TestCase):
    def test_current_review_and_year_rollover(self):
        self.assertEqual(review_windows(date(2026, 9, 11))["kospi200"]["period_start"], "20260501")
        cycle = next_cycle("kospi200", date(2026, 12, 12))
        self.assertEqual(cycle["apply"], "2027-06-11")
        self.assertEqual(cycle["period_start"], "20261101")
        self.assertEqual(next_cycle("fn_aitop2", date(2026, 12, 20))["ref_date"], "20261230")

    def test_fix_date_accounts_for_korean_holiday(self):
        self.assertEqual(next_cycle("fn_semitop10", date(2026, 9, 11))["fix_date"], "2026-10-07")

    def test_today_remains_active_until_final_trade_close(self):
        cycle = next_cycle("fn_top10", date(2026, 9, 11))
        self.assertFalse(event_has_passed(cycle, datetime(2026, 9, 11, 10)))
        self.assertTrue(event_has_passed(cycle, datetime(2026, 9, 11, 16)))
        semi = next_cycle("krx_semi", date(2026, 9, 11))
        self.assertTrue(event_has_passed(semi, datetime(2026, 9, 11, 10)))

    def test_split_rebalance_stays_active_after_first_trade(self):
        cycle = next_cycle("fn_battery", date(2026, 9, 15))
        self.assertEqual(cycle["apply"], "2026-09-14")
        self.assertEqual(cycle["apply_end"], "2026-09-16")
        self.assertFalse(event_has_passed(cycle, datetime(2026, 9, 15, 10)))
        self.assertTrue(event_has_passed(cycle, datetime(2026, 9, 15, 16)))

    def test_uncovered_years_are_marked_and_configs_roll(self):
        self.assertEqual(next_cycle("kospi200", date(2028, 1, 1))["calendar_coverage"], "partial")
        self.assertTrue(all(c["apply"].startswith("2027") for c in build_configs(date(2027, 1, 1))))
        events = events_upcoming(date(2026, 9, 11))
        self.assertTrue(all(e["date_status"] == "provisional" for e in events if "예시" in e["note"]))


class FxTests(unittest.TestCase):
    def fetch(self, previous, response=None):
        request = SimpleNamespace(get=lambda *a, **kw: response)
        if response is None:
            request.get = lambda *a, **kw: (_ for _ in ()).throw(OSError("offline"))
        with patch.dict("sys.modules", {"requests": request}):
            return analyze.fetch_usdkrw(previous, datetime(2026, 9, 11, 10))

    def test_fallback_preserves_observed_value_and_timestamp(self):
        prior = {"usdkrw": 1421.2, "data_quality": {"fx": {"status": "ok", "as_of": "2026-09-10T15:30:00+09:00", "source": "Naver FX_USDKRW"}}}
        value, quality = self.fetch(prior)
        self.assertEqual(value, 1421.2)
        self.assertEqual(quality["status"], "fallback")
        self.assertEqual(quality["as_of"], "2026-09-10T15:30:00+09:00")

    def test_legacy_value_stays_unverified_across_failures(self):
        value, quality = self.fetch({"usdkrw": 1380})
        self.assertEqual(quality["status"], "unverified_fallback")
        self.assertIsNone(quality["as_of"])
        self.assertIsNone(quality["fetched_at"])
        _, repeated = self.fetch({"usdkrw": value, "data_quality": {"fx": quality}})
        self.assertEqual(repeated["status"], "unverified_fallback")

    def test_no_prior_valid_fx_leaves_domestic_analysis_available(self):
        for value in (None, 0, -1, math.nan):
            actual, quality = self.fetch({"usdkrw": value})
            self.assertIsNone(actual)
            self.assertEqual(quality["status"], "unavailable")

    def test_success_separates_source_time_from_fetch_time(self):
        response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"closePrice": "1,420.3", "localTradedAt": "2026-09-10T15:30:00+09:00"})
        value, quality = self.fetch({}, response)
        self.assertEqual(value, 1420.3)
        self.assertEqual(quality["status"], "ok")
        self.assertEqual(quality["as_of"], "2026-09-10T15:30:00+09:00")
        self.assertIn("+09:00", quality["fetched_at"])


class ArchiveDateTests(unittest.TestCase):
    def test_full_iso_range_uses_complete_end_date(self):
        self.assertEqual(archive.parse_apply("2026-09-14~2026-09-16 (분할 적용)"), ("2026-09-14", "2026-09-16"))
        self.assertEqual(archive.event_dates("2026-09-14~2026-09-16 (분할 적용)"), ("2026-09-14", "2026-09-11", "2026-09-15"))

    def test_legacy_short_range_is_preserved(self):
        self.assertEqual(archive.parse_apply("2026-09-14~16 (3영업일 분할)"), ("2026-09-14", "2026-09-16"))

    def test_cross_month_and_year_ranges(self):
        self.assertEqual(archive.parse_apply("2026-09-30~2026-10-02"), ("2026-09-30", "2026-10-02"))
        self.assertEqual(archive.parse_apply("2026-12-30~2027-01-04"), ("2026-12-30", "2027-01-04"))

    def test_single_day_remains_unchanged(self):
        self.assertEqual(archive.parse_apply("2026-09-14"), ("2026-09-14", "2026-09-14"))

    def test_invalid_ranges_fail_instead_of_archiving_wrong_dates(self):
        for value in ("", "not a date", "2026-09-31", "2026-09-14~13", "2026-09-14~2026-09-10", "2026-09-14~2026-09", "2026-09-14~garbage"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                archive.parse_apply(value)
        with self.assertRaises(ValueError):
            archive.event_dates("2026-09-14", "2026-09-13")

    def test_flow_structured_end_date_overrides_display_only_start(self):
        ev = {"key": "test", "name": "test", "apply": "2026-09-14", "apply_end": "2026-09-16", "proxy_etf": "123456",
              "target_src": "test", "etfs": [], "notional_total": 0, "adds": [], "dels": [], "total_buy": 0, "total_sell": 0, "rows": []}
        holdings = {"test": {"etf_code": "123456", "date": "2026-09-10", "rows": []}}
        self.assertEqual(archive.from_flow(ev, holdings, "2026-09-11")["settle_date"], "2026-09-15")
        ev["window"] = {"apply_end": ev.pop("apply_end")}
        self.assertEqual(archive.from_flow(ev, holdings, "2026-09-11")["settle_date"], "2026-09-15")


class PendingReviewTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.execute("CREATE TABLE daily(code TEXT, date TEXT, close REAL, shares REAL, trdval REAL)")
        self.con.execute("CREATE TABLE stock(code TEXT, name TEXT, market TEXT, is_common INTEGER, listing_date TEXT, close REAL, mktcap REAL, gics_ig TEXT)")
        self.con.execute("CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)")
        self.rows = []
        for i in range(10):
            code = f"{100000+i:06d}"
            self.con.execute("INSERT INTO stock VALUES(?,?,?,?,?,?,?,?)", (code, code, "KOSPI", 1, "2020-01-01", 100, 10000, "4530"))
            self.con.execute("INSERT INTO daily VALUES(?,?,?,?,?)", (code, "20260910", 100, 100, 1000))
            self.rows.append({"code": code, "name": code, "weight": 10})
        self.con.execute("INSERT INTO meta VALUES('current_universe',?)", (json.dumps({"version": 1, "codes": [r["code"] for r in self.rows],
                         "observed_at": "2026-09-11 08:10", "source": "Naver marketValue KOSPI/KOSDAQ"}),))
        self.payloads = {}
        self.writes = {}

    def tearDown(self):
        self.con.close()

    def read_json(self, path, **kwargs):
        return json.dumps(self.payloads[path.name])

    def capture_json(self, path, text, **kwargs):
        self.writes[path.name] = json.loads(text)
        return len(text)

    def test_topn_rollover_does_not_predict_all_current_members_removed(self):
        result = analyze.run_topn(self.con, {}, {"fn_top10": {"etf_code": "292150", "date": "2026-09-10", "rows": self.rows}},
                                  "fn_top10", "TOP10", 10, lambda s: True, "20270201", "20270226", "", as_of="20260910")
        self.assertEqual(result["availability"], "pending_review")
        self.assertEqual(result["adds"], [])
        self.assertEqual(result["dels"], [])
        self.assertEqual(result["cur_not_in_universe"], [])

    def test_annual_semi_rollover_calculates_only_current_membership_cap(self):
        self.payloads = {
            "holdings.json": {"krx_semi": {"etf_code": "091160", "date": "2026-09-10", "rows": self.rows}},
            "etf_master.json": [{"code": "091160", "name": "반도체", "aum_eok": 1000}],
            "analysis.json": {"last_daily": "20260910", "fif": {}},
        }
        with patch.object(rebal_flow.sqlite3, "connect", return_value=self.con), \
             patch.object(rebal_flow, "korea_now", return_value=datetime(2026, 9, 12, 10)), \
             patch.object(Path, "read_text", lambda p, **kw: self.read_json(p, **kw)), \
             patch.object(Path, "write_text", lambda p, t, **kw: self.capture_json(p, t, **kw)), \
             contextlib.redirect_stdout(io.StringIO()):
            rebal_flow.main()
        result = self.writes["krx_semi_rebal.json"]
        self.assertEqual(result["availability"], "pending_review")
        self.assertEqual(result["scenarios"]["current"]["n_selected"], 10)
        self.assertEqual(result["scenarios"]["current"]["adds"], [])
        self.assertTrue(all(r["avg_mcap"] is None for r in result["scenarios"]["current"]["rows"]))
        for key in ("cap10", "literal"):
            self.assertTrue(result["scenarios"][key]["unavailable"])
            self.assertIsNone(result["scenarios"][key]["n_selected"])
            self.assertEqual(result["scenarios"][key]["rows"], [])

    def test_topn_flow_rollover_uses_explicit_current_membership_assumption(self):
        self.payloads = {
            "holdings.json": {"fn_top10": {"etf_code": "292150", "date": "2026-09-10", "rows": self.rows}},
            "etf_master.json": [{"code": "292150", "name": "TOP10", "aum_eok": 1000}],
            "analysis.json": {"generated": "2026-09-15 10:00", "last_daily": "20260910", "fn_top10": {"availability": "pending_review"}},
        }
        with patch.object(flow_engine.sqlite3, "connect", return_value=self.con), \
             patch.object(flow_engine, "korea_now", return_value=datetime(2026, 9, 15, 10)), \
             patch.object(flow_engine, "CONFIGS", [flow_engine.CONFIGS[0]]), \
             patch.object(Path, "read_text", lambda p, **kw: self.read_json(p, **kw)), \
             patch.object(Path, "write_text", lambda p, t, **kw: self.capture_json(p, t, **kw)), \
             patch.object(Path, "exists", return_value=False), \
             contextlib.redirect_stdout(io.StringIO()):
            flow_engine.main()
        result = self.writes["flows.json"]["events"][0]
        self.assertEqual(result["availability"], "pending_review")
        self.assertIn("편출입 예측 없음", result["target_src"])
        self.assertEqual(result["adds"], [])
        self.assertEqual(result["dels"], [])
        self.assertAlmostEqual(sum(r["w_target"] for r in result["rows"]), 100)


class WeightTests(unittest.TestCase):
    def test_caps_preserve_total(self):
        weights, _ = water_fill({"A": 99, **{str(i): 1 for i in range(9)}}, 0.20)
        self.assertAlmostEqual(sum(weights.values()), 1)
        self.assertLessEqual(max(weights.values()), 0.20 + 1e-12)
        tiered = tiered_ceiling({"A": 99, **{str(i): 1 for i in range(9)}}, 0.20, 0.15)
        self.assertAlmostEqual(sum(tiered.values()), 1)
        self.assertAlmostEqual(tiered["A"], 0.20)

    def test_infeasible_and_nonfinite_weights_fail(self):
        for weights, cap in (({"A": 1, "B": 1}, .2), ({"A": math.nan}, 1), ({"A": 0}, 1), ({}, 1)):
            with self.assertRaises(ValueError):
                water_fill(weights, cap)


if __name__ == "__main__":
    unittest.main()
