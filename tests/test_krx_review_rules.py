import sqlite3
import unittest
from datetime import date, timedelta
from unittest.mock import patch

import analyze
from calendar_events import is_bday


class EligibilityTests(unittest.TestCase):
    def eligible(self, listing_date, ref_date, is_cur=False):
        return analyze.eligible({"code": "123450", "name": "일반사업회사", "listing_date": listing_date},
                                ref_date, is_cur=is_cur)[0]

    def test_six_calendar_months_instead_of_183_days(self):
        self.assertTrue(self.eligible("2025-10-30", "20260430"))
        self.assertFalse(self.eligible("2025-10-30", "20260429"))
        self.assertTrue(self.eligible("2026-04-30", "20261030"))
        self.assertFalse(self.eligible("2026-04-30", "20261029"))

    def test_month_end_and_leap_day_anniversaries(self):
        for listed, anniversary, previous in (("2025-08-31", "20260228", "20260227"),
                                               ("2023-08-31", "20240229", "20240228"),
                                               ("2024-02-29", "20240829", "20240828"),
                                               ("2025-10-31", "20260430", "20260429")):
            with self.subTest(listed=listed):
                self.assertTrue(self.eligible(listed, anniversary))
                self.assertFalse(self.eligible(listed, previous))

    def test_existing_special_admissions_keep_listing_exception(self):
        self.assertTrue(self.eligible("2026-10-01", "20261030", is_cur=True))
        self.assertFalse(self.eligible("2026-10-01", "20261030"))

    def test_infrastructure_operating_companies_are_not_investment_vehicles(self):
        for name in ("NICE인프라", "바이오인프라"):
            self.assertFalse(analyze.is_excluded_name(name), name)
        for name in ("맥쿼리인프라", "KB발해인프라", "한화리츠", "코람코라이프인프라리츠", "삼성스팩12호", "코리아선박투자"):
            self.assertTrue(analyze.is_excluded_name(name), name)


class ReviewPeriodTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.execute("CREATE TABLE daily(code TEXT, date TEXT, close REAL, shares REAL, trdval REAL)")
        self.con.executemany("INSERT INTO daily VALUES(?,?,?,?,?)", [
            ("A", "20260824", 100, 10, 100), ("B", "20260824", 10, 10, 100),
            ("A", "20260825", 20, 5, 200), ("B", "20260825", 10, 10, 100),
            ("A", "20260826", 30, 5, 300), ("B", "20260826", 10, 10, 100),
            ("A", "20260827", 999, 5, 0), ("B", "20260827", 999, 10, 0),
        ])
        self.event = {"code": "A", "name": "A", "type": "split", "effective_date": "20260825",
                      "published_date": "20260820", "source_url": "https://kind.krx.co.kr/example", "note": "분할 변경상장"}

    def tearDown(self):
        self.con.close()

    def stats(self, events=None, as_of="20260826"):
        return analyze.period_stats(self.con, "20260801", "20261030", as_of, review_events=events)

    def test_event_resets_only_affected_stock_and_all_metrics(self):
        original = self.stats()
        adjusted = self.stats([self.event])
        self.assertEqual(adjusted["A"]["n"], 2)
        self.assertEqual(adjusted["A"]["first"], "20260825")
        self.assertEqual(adjusted["A"]["avg_mcap"], 125)
        self.assertEqual(adjusted["A"]["avg_trdval"], 250)
        self.assertEqual(adjusted["A"]["ret_period"], .5)
        self.assertEqual(adjusted["A"]["review_events"], [self.event])
        self.assertEqual(original["B"], adjusted["B"])
        self.assertEqual(original["A"]["n"], 3)
        self.assertNotIn("review_events", original["A"])

    def test_future_effective_and_unpublished_events_do_not_leak(self):
        original = self.stats()
        future_event = dict(self.event, effective_date="20260828")
        unpublished = dict(self.event, published_date="20260827")
        self.assertEqual(self.stats([future_event, unpublished]), original)
        self.assertEqual(self.stats([self.event], "20260824"), self.stats(as_of="20260824"))

    def test_historical_review_cannot_use_later_events(self):
        self.con.execute("INSERT INTO daily VALUES('A','20260430',50,10,100)")
        original = analyze.period_stats(self.con, "20251101", "20260430", "20260430")
        historical = analyze.period_stats(self.con, "20251101", "20260430", "20260430", [self.event])
        self.assertEqual(historical, original)
        self.assertEqual(historical["A"]["avg_mcap"], 500)

    def test_latest_confirmed_event_wins_and_missing_post_event_prices_do_not_reuse_old_data(self):
        later = dict(self.event, effective_date="20260826", type="trading_resumption")
        self.assertEqual(self.stats([later, self.event])["A"]["n"], 1)
        no_session = dict(self.event, effective_date="20260827")
        self.assertNotIn("A", self.stats([no_session], "20260827"))

    def test_recent_average_uses_completed_market_sessions_and_keeps_real_halt_zero(self):
        # Individual trading halt is still a real market session; whole-market zero is not.
        self.con.execute("UPDATE daily SET trdval=0 WHERE code='A' AND date='20260826'")
        values = analyze.recent_mcap_stats(self.con, 2, "20260827")
        self.assertEqual(values["A"], {"avg_mcap": 125, "n": 2})
        self.assertEqual(analyze.recent_avg_mcap(self.con, "A", 2, "20260825"), 550)

    def test_whole_market_rank_uses_event_adjusted_statistics(self):
        stocks = {c: {"market": "KOSPI", "is_common": 1} for c in ("A", "B")}
        self.con.execute("UPDATE daily SET close=20 WHERE code='B'")
        self.assertEqual(analyze.market_common_ranks(stocks, self.stats(), "KOSPI")["A"], 1)
        self.assertEqual(analyze.market_common_ranks(stocks, self.stats([self.event]), "KOSPI")["A"], 2)

    def test_published_hanwha_event_has_explicit_source_and_dates(self):
        event = next(e for e in analyze.load_review_events() if e["code"] == "000880")
        self.assertEqual(event["effective_date"], "20260825")
        self.assertEqual(event["published_date"], "20260820")
        self.assertEqual(event["type"], "split")
        self.assertIn("kind.krx.co.kr/", event["source_url"])


class SmallCapScenarioTests(unittest.TestCase):
    def row(self, code, cap, rank, current=False, liquid=True, status=None):
        return {"code": code, "name": code, "avg_mcap": cap, "market_mcap_rank": rank,
                "is_cur": current, "liq_ok": liquid, "status": status or ("유지" if current else "미선정")}

    def test_full_market_rank_includes_new_listings_and_spacs_before_review_filter(self):
        stocks = {
            "A": {"market": "KOSDAQ", "is_common": 1, "name": "신규상장", "listing_date": "20261001"},
            "B": {"market": "KOSDAQ", "is_common": 1, "name": "스팩"},
            "C": {"market": "KOSDAQ", "is_common": 1, "name": "기존종목"},
            "D": {"market": "KOSDAQ", "is_common": 1, "universe_active": False},
            "E": {"market": "KOSDAQ", "is_common": 0},
            "F": {"market": "KOSPI", "is_common": 1},
        }
        stats = {c: {"avg_mcap": n} for c, n in zip(stocks, (300, 200, 100, 900, 800, 700))}
        self.assertEqual(analyze.market_common_ranks(stocks, stats, "KOSDAQ"), {"A": 1, "B": 2, "C": 3})

    def test_replacement_is_highest_liquid_unused_eligible_stock_and_size_is_preserved(self):
        rows = [self.row("A", 100, 301, current=True), self.row("B", 300, 100, current=True),
                self.row("C", 200, 150), self.row("D", 290, 101, liquid=False),
                self.row("E", 280, 102, status="산업군제외")]
        original = {"A", "B"}
        selected, info = analyze.apply_small_cap_exclusion(rows, original)
        self.assertEqual(selected, {"B", "C"})
        self.assertEqual(original, {"A", "B"})
        self.assertEqual(len(selected), info["base_n_selected"])
        self.assertEqual((info["base_adds"], info["base_dels"]), (0, 0))
        self.assertEqual(info["removed"][0]["market_mcap_rank"], 301)
        self.assertEqual(info["replacements"][0]["code"], "C")
        self.assertTrue(info["discretionary"])
        self.assertIn("재량제외", rows[0]["status"])
        self.assertIn("재량대체", rows[2]["status"])

    def test_rank_300_is_retained_and_insufficient_candidates_do_not_shrink_index(self):
        rows = [self.row("A", 100, 300, current=True), self.row("B", 90, 301, current=True),
                self.row("C", 80, 302)]
        selected, info = analyze.apply_small_cap_exclusion(rows, {"A", "B"})
        self.assertEqual(selected, {"A", "B"})
        self.assertEqual(info["removed"], [])
        self.assertEqual([r["code"] for r in info["unreplaced"]], ["B"])

    def test_two_exclusions_use_distinct_replacements(self):
        rows = [self.row("A", 80, 400, current=True), self.row("B", 100, 310, current=True),
                self.row("C", 300, 100), self.row("D", 200, 200)]
        selected, info = analyze.apply_small_cap_exclusion(rows, {"A", "B"})
        self.assertEqual(selected, {"C", "D"})
        self.assertEqual(len(info["removed"]), 2)
        self.assertEqual(len(info["replacements"]), 2)


class RecentLargeCapIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.execute("CREATE TABLE daily(code TEXT, date TEXT, close REAL, shares REAL, trdval REAL)")
        self.days = []
        day = date(2026, 5, 4)
        while len(self.days) < 80:
            if is_bday(day):
                self.days.append(day.strftime("%Y%m%d"))
            day += timedelta(days=1)
        self.stocks = {}
        self.holdings = {"kospi200": {"etf_code": "TEST", "date": self.days[-1], "rows": []}}
        for i in range(51):
            code = f"{i + 1:06d}"
            self.stocks[code] = {"code": code, "name": code, "market": "KOSPI", "is_common": 1,
                                 "listing_date": "2020-01-01", "gics_sec": "45", "mktcap": 100 if i < 50 else .1,
                                 "close": 100 if i < 50 else .1}
            if i < 50:
                self.holdings["kospi200"]["rows"].append({"code": code, "weight": 2})
            self.con.executemany("INSERT INTO daily VALUES(?,?,?,?,?)", [
                (code, dt, (100 if i < 50 else 500 if 65 <= j < 79 else .1), 1, 1000 - i)
                for j, dt in enumerate(self.days)])
        self.cfg = dict(name="test", proxy="kospi200", market="KOSPI", N=50, cum=.6, liq=1,
                        keep_buf=1.2, new_buf=.8, rules="test", small_cap_exclusion=False)
        self.window = {"period_start": self.days[0], "period_end": self.days[-1], "ref_date": self.days[-1],
                       "apply": "2026-12-11"}

    def tearDown(self):
        self.con.close()

    def run_index(self):
        with patch.object(analyze, "load_review_events", return_value=[]):
            return analyze.run_krx_index(self.con, self.stocks, self.holdings, "kospi200", self.cfg, {},
                                         self.days[-1], self.window)

    def test_recent_top50_candidate_is_found_even_outside_current_market_cap_top50(self):
        result = self.run_index()
        candidate = next(u for u in result["all"] if u["code"] == "000051")
        self.assertEqual(candidate["mcap15_rank"], 1)
        self.assertEqual(candidate["mcap15_n_days"], 15)
        self.assertIn("000051", {u["code"] for u in result["largecap_candidates"]})
        self.assertNotIn("000051", {u["code"] for u in result["adds"]})
        self.assertEqual(result["classification"]["status"], "reference_pending_krics")

    def test_incomplete_recent_window_has_no_comparable_rank(self):
        self.con.execute("DELETE FROM daily WHERE code='000051' AND date=?", (self.days[-2],))
        result = self.run_index()
        candidate = next(u for u in result["all"] if u["code"] == "000051")
        self.assertEqual(candidate["mcap15_n_days"], 14)
        self.assertIsNone(candidate["mcap15_rank"])
        self.assertNotIn("000051", {u["code"] for u in result["largecap_candidates"]})

    def test_applied_small_cap_scenario_has_base_counts_and_discretionary_confidence(self):
        self.cfg["small_cap_exclusion"] = True
        ranks = {code: i + 1 for i, code in enumerate(self.stocks)}
        ranks["000001"] = 301
        with patch.object(analyze, "market_common_ranks", return_value=ranks):
            result = self.run_index()
        self.assertEqual(result["n_selected"], 50)
        self.assertEqual((result["smallcap_rule"]["base_adds"], result["smallcap_rule"]["base_dels"]), (0, 0))
        self.assertEqual([u["code"] for u in result["adds"]], ["000051"])
        self.assertEqual([u["code"] for u in result["dels"]], ["000001"])
        for row in result["adds"] + result["dels"]:
            self.assertEqual(row["confidence"], "低")
            self.assertIn("재량", row["conf_note"])
            self.assertTrue(row["discretionary_action"])


if __name__ == "__main__":
    unittest.main()
