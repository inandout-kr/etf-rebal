import sqlite3
import unittest
from datetime import date, timedelta

import analyze
from calendar_events import is_bday
from ipo_eligibility import assess_first15, first_ipo_window


class IpoFirst15Tests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.execute("CREATE TABLE daily(code TEXT,date TEXT,close REAL,shares REAL,trdval REAL)")
        self.days = []
        day = date(2026, 7, 1)
        while len(self.days) < 30:
            if is_bday(day):
                self.days.append(day.strftime("%Y%m%d"))
            day += timedelta(days=1)
        self.candidate = {"code": "0013Y0", "name": "신규상장", "market": "KOSPI", "is_common": 1,
                          "listing_date": "2026-07-01", "mktcap": 1, "fif": 1.0}
        self.market = [self.candidate]
        for i in range(59):
            self.market.append({"code": f"{1000+i:06d}", "name": f"기존{i}", "market": "KOSPI",
                                "is_common": 1, "listing_date": "2020-01-01", "mktcap": 100})
        for stock in self.market:
            self.con.executemany("INSERT INTO daily VALUES(?,?,?,?,?)", [
                (stock["code"], day, (1000 if i < 15 else 1) if stock is self.candidate else 100, 1, 100)
                for i, day in enumerate(self.days)])
        self.formal = {self.candidate["code"]: {"verified": True, "universe_verified": True, "source": "KRX test evidence"}}

    def tearDown(self):
        self.con.close()

    def floats(self, value=50, official=True):
        return {self.candidate["code"]: {"official": official, "source": "KRX test official float",
                                         "observations": {day: value for day in self.days[:15]}}}

    def assess(self, **kwargs):
        return assess_first15(self.con, self.candidate, self.market, self.days[-1], 50, **kwargs)

    def test_uses_first_fifteen_not_recent_fifteen_or_current_market_cap(self):
        result = self.assess(official_float_history=self.floats(), formal_checks=self.formal)
        self.assertEqual(result["avg_mcap15"], 1000)
        self.assertEqual(result["rank15"], 1)
        self.assertEqual(result["first15_end"], self.days[14])
        self.assertEqual(result["status"], "ELIGIBLE")
        self.assertFalse(result["official_result"])

    def test_fewer_than_fifteen_completed_sessions_is_hold(self):
        result = assess_first15(self.con, self.candidate, self.market, self.days[13], 50)
        self.assertEqual(result["first15_market_sessions"], 14)
        self.assertEqual(result["status"], "HOLD")
        self.assertIsNone(result["rank15"])
        self.assertFalse(result["eligible"])

    def test_missing_candidate_day_does_not_slide_window_forward(self):
        self.con.execute("DELETE FROM daily WHERE code=? AND date=?", (self.candidate["code"], self.days[2]))
        result = self.assess()
        self.assertEqual(result["first15_n_days"], 14)
        self.assertEqual(result["status"], "HOLD")
        self.assertIsNone(result["avg_mcap15"])

    def test_etf_implied_fif_cannot_replace_official_float(self):
        result = self.assess(official_float_history=self.floats(official=False), formal_checks=self.formal)
        self.assertEqual(result["total_cap_condition"], True)
        self.assertEqual(result["official_float_status"], "HOLD")
        self.assertEqual(result["status"], "HOLD")
        self.assertFalse(result["eligible"])

    def test_float_threshold_includes_equality_and_rejects_zero(self):
        for value, expected in ((50, "ELIGIBLE"), (49.99, "NOT_ELIGIBLE"), (0, "NOT_ELIGIBLE")):
            with self.subTest(value=value):
                result = self.assess(official_float_history=self.floats(value), formal_checks=self.formal)
                self.assertEqual(result["required_float_mcap"], 50)
                self.assertEqual(result["status"], expected)

    def test_official_size_pass_without_formal_universe_evidence_stays_hold(self):
        result = self.assess(official_float_history=self.floats())
        self.assertEqual(result["status"], "HOLD")
        self.assertEqual(result["formal_checks_status"], "HOLD")

    def test_missing_comparator_observation_holds_rank_decision(self):
        self.con.execute("DELETE FROM daily WHERE code=? AND date=?", (self.market[1]["code"], self.days[4]))
        result = self.assess(official_float_history=self.floats(), formal_checks=self.formal)
        self.assertEqual(result["status"], "HOLD")
        self.assertIsNone(result["total_cap_condition"])
        self.assertIn(self.market[1]["code"], result["comparison_missing_codes"])

    def test_whole_market_zero_holds_fixed_window_and_weekend_is_not_a_session(self):
        self.con.execute("UPDATE daily SET trdval=0 WHERE date=?", (self.days[0],))
        self.con.executemany("INSERT INTO daily VALUES(?,?,?,?,?)", [(s["code"], "20260704", 100, 1, 100) for s in self.market])
        window = first_ipo_window(self.con, "2026-07-01", self.days[-1])
        self.assertIn(self.days[0], window)
        self.assertNotIn("20260704", window)
        self.assertEqual(window[-1], self.days[14])
        result = self.assess()
        self.assertEqual(result["status"], "HOLD")
        self.assertEqual(result["first15_observed_market_sessions"], 14)
        self.assertIsNone(result["avg_mcap15"])

    def test_missing_whole_market_day_is_not_replaced_by_sixteenth_day(self):
        self.con.execute("DELETE FROM daily WHERE date=?", (self.days[2],))
        result = self.assess()
        self.assertEqual(result["first15_end"], self.days[14])
        self.assertEqual(result["first15_observed_market_sessions"], 14)
        self.assertEqual(result["status"], "HOLD")

    def test_individual_trading_halt_zero_is_a_completed_market_session(self):
        self.con.execute("UPDATE daily SET trdval=0 WHERE code=? AND date=?", (self.candidate["code"], self.days[0]))
        result = self.assess()
        self.assertEqual(result["first15_n_days"], 15)
        self.assertEqual(result["avg_mcap15"], 1000)

    def test_monitor_uses_50_and_30_and_preserves_mixed_code(self):
        for market, expected in (("KOSPI", 50), ("KOSDAQ", 30)):
            with self.subTest(market=market):
                for stock in self.market:
                    stock["market"] = market
                out = analyze.ipo_monitor(self.con, {s["code"]: s for s in self.market}, self.days[-1])
                self.assertEqual(len(out), 1)
                self.assertEqual(out[0]["code"], "0013Y0")
                self.assertEqual(out[0]["threshold_rank"], expected)
                self.assertEqual(out[0]["status"], "HOLD")
                self.assertFalse(out[0]["eligible"])


if __name__ == "__main__":
    unittest.main()
