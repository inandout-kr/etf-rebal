"""Offline regressions: current membership, stale prices and FX provenance boundaries."""
import json
import math
import sqlite3
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import analyze
import flow_engine
import rebal_flow
from fetch_market import store_universe
from market_dates import anchor_stocks, current_universe, require_current_prices, usable_fx
from test_pipeline import NOW, artifacts
from validate_data import DataQualityError, validate_artifacts


class CurrentDataTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.executescript("""
            CREATE TABLE stock(code TEXT PRIMARY KEY, name TEXT, market TEXT, is_common INT,
              close REAL, mktcap REAL, trdval REAL, volume REAL, updated TEXT, gics_sec TEXT);
            CREATE TABLE daily(code TEXT, date TEXT, close REAL, shares REAL, trdval REAL);
            CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
        """)
        for c in ("A", "B", "C", "D"):
            self.con.execute("INSERT INTO stock VALUES(?,?,?,?,?,?,?,?,?,?)",
                             (c, c, "KOSPI", 1, 99, 9900, 0, 0, "2026-09-11 08:10" if c != "D" else "2026-09-09 08:10", "10"))
        self.con.executemany("INSERT INTO daily VALUES(?,?,?,?,?)", [
            ("A", "20260909", 10, 100, 10), ("A", "20260910", 12, 100, 100),
            ("B", "20260909", 20, 100, 10), ("B", "20260910", 20, 100, 0),
            ("C", "20260909", 30, 100, 10), ("D", "20260909", 40, 100, 10)])
        self.stocks = {r["code"]: dict(r) for r in self.con.execute("SELECT * FROM stock")}
        self.snapshot = {"version": 1, "codes": ["A", "B", "C"], "observed_at": "2026-09-11 08:10",
                         "source": "Naver marketValue KOSPI/KOSDAQ"}
        self.con.execute("INSERT INTO meta VALUES('current_universe',?)", (json.dumps(self.snapshot),))

    def tearDown(self):
        self.con.close()

    def test_departed_and_active_missing_are_distinct_and_history_is_unchanged(self):
        before = list(self.con.execute("SELECT * FROM daily"))
        stocks = anchor_stocks(self.con, self.stocks, "20260910", current=True)
        self.assertTrue(stocks["A"]["review_ready"])
        self.assertEqual(stocks["A"]["price_as_of"], "20260910")
        self.assertTrue(stocks["C"]["universe_active"])
        self.assertFalse(stocks["C"]["review_ready"])
        self.assertFalse(stocks["D"]["universe_active"])
        for c in ("C", "D"):
            self.assertIsNone(stocks[c]["close"])
            self.assertIsNone(stocks[c]["mktcap"])
            self.assertEqual(stocks[c]["price_as_of"], "20260909")
            self.assertEqual(stocks[c]["last_close"], 30 if c == "C" else 40)
        self.assertEqual(before, list(self.con.execute("SELECT * FROM daily")))
        self.assertEqual(self.stocks["C"]["close"], 99)

    def test_halted_zero_row_is_a_current_observation(self):
        stocks = anchor_stocks(self.con, self.stocks, "20260910", current=True)
        self.assertTrue(stocks["B"]["review_ready"])
        self.assertEqual(stocks["B"]["mktcap"], 2000)
        require_current_prices(stocks, ["A", "B"], "ETF")

    def test_historical_mode_retains_departed_prices(self):
        stocks = anchor_stocks(self.con, self.stocks, "20260909")
        self.assertEqual(stocks["D"]["close"], 40)
        self.assertNotIn("review_ready", stocks["D"])

    def test_departure_after_final_trade_is_not_a_historical_universe_reconstruction(self):
        self.con.execute("INSERT INTO daily VALUES('D','20260930',41,100,100)")
        self.con.execute("UPDATE meta SET v=?", (json.dumps(dict(self.snapshot, observed_at="2026-10-01 08:10")),))
        current = anchor_stocks(self.con, self.stocks, "20260930", current=True)
        self.assertFalse(current["D"]["universe_active"])
        self.assertFalse(current["D"]["review_ready"])
        self.assertIsNone(current["D"]["close"])
        self.assertEqual(current["D"]["last_close"], 41)
        self.assertEqual(current["D"]["price_as_of"], "20260930")
        historic = anchor_stocks(self.con, self.stocks, "20260930")
        self.assertEqual(historic["D"]["close"], 41)
        self.assertEqual(historic["D"]["price_as_of"], "20260930")

    def test_missing_or_departed_holding_blocks_instead_of_zero_target(self):
        stocks = anchor_stocks(self.con, self.stocks, "20260910", current=True)
        for code in ("C", "D", "UNKNOWN"):
            with self.assertRaisesRegex(ValueError, code):
                require_current_prices(stocks, ["A", code], "ETF")

    def test_legacy_snapshot_inference_requires_matching_collection(self):
        self.con.execute("DELETE FROM meta")
        state = {"attempted_at": "2026-09-11 08:10", "requested": 3}
        meta = {"updated": state["attempted_at"], "market_collection": json.dumps(state)}
        codes, quality = current_universe(self.con, self.stocks, meta)
        self.assertEqual(codes, {"A", "B", "C"})
        self.assertEqual(quality["status"], "legacy_inferred")
        for field, value in (("requested", 4), ("attempted_at", "2026-09-10 08:10")):
            broken = dict(state, **{field: value})
            self.assertEqual(current_universe(self.con, self.stocks, dict(meta, market_collection=json.dumps(broken)))[1]["status"], "unavailable")
        self.assertEqual(current_universe(self.con, self.stocks, {})[1]["status"], "unavailable")

    def test_invalid_snapshot_never_restores_all_historical_codes(self):
        for codes in ([], ["A", "A"], ["UNKNOWN"]):
            self.con.execute("UPDATE meta SET v=?", (json.dumps(dict(self.snapshot, codes=codes)),))
            self.assertEqual(current_universe(self.con, self.stocks)[0], set())

    def test_unknown_universe_stops_analysis_before_fetch_or_write(self):
        with patch.object(analyze, "load", return_value=(self.con, self.stocks, {}, {}, [])), \
             patch.object(analyze, "korea_now", return_value=datetime(2026, 9, 11, 8)), \
             patch.object(analyze, "fetch_usdkrw") as fetch, patch.object(Path, "write_text") as write:
            with self.assertRaisesRegex(ValueError, "현재 유니버스"):
                analyze.main()
            fetch.assert_not_called()
            write.assert_not_called()

    def test_invalid_collection_preserves_prior_snapshot_and_rows(self):
        before = self.con.execute("SELECT v FROM meta").fetchone()[0]
        with self.assertRaises(ValueError):
            store_universe(self.con, [], "2026-09-12 08:10")
        self.assertEqual(self.con.execute("SELECT v FROM meta").fetchone()[0], before)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM stock").fetchone()[0], 4)

    def test_valid_collection_persists_membership_without_deleting_history(self):
        self.con.execute("DELETE FROM stock")
        rows = [{"code": f"{i:05}0", "name": f"stock{i}", "market": "KOSPI" if i < 5 else "KOSDAQ",
                 "close": 10, "mktcap": 1000, "trdval": 100, "volume": 10} for i in range(10)]
        store_universe(self.con, rows, "2026-09-11 08:10")
        store_universe(self.con, rows[:-1], "2026-09-12 08:10")
        snapshot = json.loads(self.con.execute("SELECT v FROM meta WHERE k='current_universe'").fetchone()[0])
        self.assertEqual(len(snapshot["codes"]), 9)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM stock").fetchone()[0], 10)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM daily").fetchone()[0], 6)
        for i in range(20):
            self.con.execute("INSERT INTO stock(code,name,market,is_common,updated) VALUES(?,?,?,?,?)",
                             (f"old{i}", f"old{i}", "KOSPI", 1, "2025-01-01 08:10"))
        self.con.commit()
        store_universe(self.con, rows[:-1], "2026-09-13 08:10")
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM stock").fetchone()[0], 30)

    def test_krx_review_does_not_admit_departed_or_active_stale_prices(self):
        stocks = anchor_stocks(self.con, self.stocks, "20260910", current=True)
        cfg = {"proxy": "k", "market": "KOSPI", "N": 1, "cum": .85, "liq": 1,
               "keep_buf": 1.1, "new_buf": .9, "name": "test", "rules": "test"}
        hold = {"k": {"rows": [{"code": "A", "weight": 100}], "etf_code": "ETF", "date": "2026-09-10"}}
        result = analyze.run_krx_index(self.con, stocks, hold, "kospi200", cfg, {}, "20260910",
                                       {"period_start": "20260901", "period_end": "20260930", "ref_date": "20260930"})
        self.assertEqual({r["code"] for r in result["all"]}, {"A", "B"})
        self.assertTrue(all(r["price_as_of"] == "20260910" for r in result["all"]))

    def test_flow_engine_missing_holding_preserves_previous_output(self):
        payloads = {"holdings.json": {"fn_top10": {"etf_code": "292150", "date": "2026-09-10", "rows": [{"code": "C", "weight": 100}]}},
                    "etf_master.json": [{"code": "292150", "aum_eok": 100}],
                    "analysis.json": {"generated": "2026-09-11 08:10", "last_daily": "20260910"}}
        with patch.object(flow_engine.sqlite3, "connect", return_value=self.con), \
             patch.object(flow_engine, "korea_now", return_value=datetime(2026, 9, 11, 8)), \
             patch.object(flow_engine, "build_configs", return_value=[flow_engine.CONFIGS[0]]), \
             patch.object(Path, "read_text", lambda p, **kw: json.dumps(payloads[p.name])), \
             patch.object(Path, "write_text") as write:
            with self.assertRaisesRegex(ValueError, "C"):
                flow_engine.main()
            write.assert_not_called()

    def test_semiconductor_missing_holding_preserves_previous_output(self):
        payloads = {"holdings.json": {"krx_semi": {"rows": [{"code": "D", "weight": 100}]}},
                    "etf_master.json": [], "analysis.json": {"last_daily": "20260910"}}
        with patch.object(rebal_flow.sqlite3, "connect", return_value=self.con), \
             patch.object(rebal_flow, "korea_now", return_value=datetime(2026, 9, 11, 8)), \
             patch.object(Path, "read_text", lambda p, **kw: json.dumps(payloads[p.name])), \
             patch.object(Path, "write_text") as write:
            with self.assertRaisesRegex(ValueError, "D"):
                rebal_flow.main()
            write.assert_not_called()


class FxBoundaryTests(unittest.TestCase):
    now = datetime(2026, 9, 11, 8, 30)
    quality = {"status": "ok", "source": "Naver FX_USDKRW", "as_of": "2026-09-10T15:30:00+09:00"}

    def test_known_source_observation_and_cached_fallback_are_usable(self):
        for status in ("ok", "fallback"):
            self.assertTrue(usable_fx(1400, dict(self.quality, status=status), self.now))
        self.assertTrue(usable_fx(1400, dict(self.quality, as_of="2026-09-10 15:30:00"), self.now))

    def test_unknown_source_unverified_status_and_bad_timestamp_are_unusable(self):
        for quality in (None, {}, dict(self.quality, source="legacy analysis (unverified)"),
                        dict(self.quality, status="unverified_fallback"), dict(self.quality, as_of=None),
                        dict(self.quality, as_of="2026-09-10"), dict(self.quality, as_of="bad"),
                        dict(self.quality, as_of="2026-02-30T12:00:00"), dict(self.quality, as_of="2026-09-11T08:30:01+09:00"),
                        {"status": "ok", "source": "Naver FX_USDKRW", "fetched_at": "2026-09-11T08:30:00+09:00"}):
            self.assertFalse(usable_fx(1400, quality, self.now), quality)
        for value in (None, True, 0, -1, math.nan, math.inf, "1400"):
            self.assertFalse(usable_fx(value, self.quality, self.now))

    def test_msci_direct_call_requires_verified_provenance(self):
        stocks = {"A": {"code": "A", "name": "A", "market": "KOSPI", "is_common": 1, "mktcap": 10e12}}
        hold = {"msci_korea": {"rows": [], "date": "2026-09-10"}}
        for quality in (None, dict(self.quality, as_of=None)):
            result = analyze.msci_watch(stocks, hold, 1400, fx_quality=quality, now=self.now)
            self.assertEqual(result["availability"], "unavailable")
            self.assertEqual(result["candidates"], [])
            self.assertIsNone(result["usdkrw"])
        self.assertEqual(len(analyze.msci_watch(stocks, hold, 1400, fx_quality=self.quality, now=self.now)["candidates"]), 1)

    def test_live_missing_timestamp_does_not_become_verified(self):
        response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"closePrice": "1,400"})
        with patch.dict("sys.modules", {"requests": SimpleNamespace(get=lambda *a, **kw: response)}):
            value, quality = analyze.fetch_usdkrw({}, self.now)
        self.assertIsNone(value)
        self.assertEqual(quality["status"], "unavailable")
        prior = {"usdkrw": 1399, "data_quality": {"fx": self.quality}}
        with patch.dict("sys.modules", {"requests": SimpleNamespace(get=lambda *a, **kw: response)}):
            value, quality = analyze.fetch_usdkrw(prior, self.now)
        self.assertEqual(value, 1399)
        self.assertEqual(quality["status"], "fallback")
        self.assertEqual(quality["as_of"], self.quality["as_of"])

    def test_quality_gate_blocks_old_unverified_msci_artifact(self):
        data = artifacts()
        data["analysis"].update(usdkrw=1380, msci={"candidates": [{"code": "A"}], "smallest_current": []})
        with self.assertRaisesRegex(DataQualityError, "미검증 환율"):
            validate_artifacts(data, NOW)
        data["analysis"]["msci"] = {"availability": "unavailable", "reason": "FX unavailable", "candidates": [], "smallest_current": [], "usdkrw": None}
        validate_artifacts(data, NOW)


if __name__ == "__main__":
    unittest.main()
