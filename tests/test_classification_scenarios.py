import copy
import json
import unittest
from pathlib import Path

import analyze
from classification_scenarios import DERIVED_FIELDS, classification_comparison, load_mapping, stable_hash

ROOT = Path(__file__).resolve().parents[1]


class FrozenClassificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.analysis = json.loads((ROOT / "data/analysis.json").read_text(encoding="utf-8"))
        cls.payload, error = load_mapping(ROOT / "data/krics_mapping.json")
        assert error is None, error
        cls.configs = {
            "kospi200": dict(name="KOSPI 200", market="KOSPI", N=200, cum=.85, liq=.85,
                             keep_buf=1.10, new_buf=.90, rules=cls.analysis["kospi200"]["rules"]),
            "kosdaq150": dict(name="KOSDAQ 150", market="KOSDAQ", N=150, cum=.60, liq=.80,
                              keep_buf=1.20, new_buf=.80, sector_min_share=.01, small_cap_exclusion=True,
                              rules=cls.analysis["kosdaq150"]["rules"]),
        }

    def base(self, key):
        return {k: copy.deepcopy(v) for k, v in self.analysis[key].items() if k != "classification_scenarios"}

    def compare(self, key, payload=None):
        return classification_comparison(self.base(key), self.configs[key], payload or self.payload, analyze.select_krx_rows)

    def test_gics_reselection_preserves_every_saved_row(self):
        for key, cfg in self.configs.items():
            with self.subTest(key=key):
                base = self.base(key)
                rows = copy.deepcopy(base["all"])
                for row in rows:
                    for field in DERIVED_FIELDS:
                        row.pop(field, None)
                current = {r["code"] for r in rows if r["is_cur"]}
                actual = analyze.select_krx_rows(rows, current, cfg)
                self.assertEqual(actual["all"], base["all"])
                self.assertEqual(actual["adds"], base["adds"])
                self.assertEqual(actual["dels"], base["dels"])

    def test_mapping_join_and_scenarios_recompute_without_mutating_baseline(self):
        for key, cfg in self.configs.items():
            with self.subTest(key=key):
                base = self.base(key)
                before = stable_hash(base)
                result = classification_comparison(base, cfg, self.payload, analyze.select_krx_rows)
                self.assertEqual(result["status"], "ready")
                self.assertEqual(result["coverage"]["review_rows"], result["coverage"]["mapped_rows"])
                self.assertEqual(result["coverage"]["sector_count"], 9)
                self.assertEqual(result["effective_date_status"], "HOLD")
                self.assertFalse(result["official_result"])
                self.assertEqual(stable_hash(base), before)
                for label, idx in result["scenarios"].items():
                    self.assertEqual(idx["n_selected"], cfg["N"])
                    self.assertEqual(idx["n_current"] + len(idx["adds"]) - len(idx["dels"]), idx["n_selected"])
                    self.assertEqual(idx["comparison_input_hash"], result["input_hash"])
                    self.assertEqual(idx["window"], base["window"])
                    self.assertEqual(len(idx["sectors"]), 9)
                    self.assertNotIn("classification_scenarios", idx)
                    for row in idx["all"]:
                        self.assertIn("gics_baseline_sector", row)
                    self.assertIn(label, ("A", "B"))
                self.assertEqual(result["scenarios"]["A"]["interpretation"], "primary_and_new_buffer")

    def test_missing_mapping_suppresses_comparison_preserving_gics(self):
        key = "kospi200"
        base = self.base(key)
        code = base["all"][0]["code"]
        payload = copy.deepcopy(self.payload)
        payload["sources"][key]["mapping"] = [r for r in payload["sources"][key]["mapping"] if r[0] != code]
        result = self.compare(key, payload)
        self.assertEqual(result["status"], "HOLD")
        self.assertIn(code, result["coverage"]["missing_codes"])
        self.assertEqual(result["scenarios"], {})

    def test_duplicate_even_identical_mapping_is_not_silently_accepted(self):
        payload = copy.deepcopy(self.payload)
        source = payload["sources"]["kosdaq150"]
        source["mapping"].append(copy.deepcopy(source["mapping"][0]))
        result = self.compare("kosdaq150", payload)
        self.assertEqual(result["status"], "HOLD")
        self.assertTrue(result["coverage"]["duplicate_mapping_codes"])
        self.assertEqual(result["scenarios"], {})

    def test_invalid_sector_or_source_or_date_fails_closed(self):
        for mutation in ("sector", "source", "date", "sources_type"):
            with self.subTest(mutation=mutation):
                payload = copy.deepcopy(self.payload)
                if mutation == "sector":
                    payload["sources"]["kospi200"]["mapping"][0][1] = ["잘못된 분류"]
                elif mutation == "source":
                    del payload["sources"]["kospi200"]["source_sha256"]
                elif mutation == "date":
                    payload["frozen_as_of"] = "20261022"
                else:
                    payload["sources"] = []
                self.assertEqual(self.compare("kospi200", payload)["status"], "HOLD")

    def test_alphanumeric_and_leading_zero_codes_remain_strings(self):
        kp = dict(self.payload["sources"]["kospi200"]["mapping"])
        kq = dict(self.payload["sources"]["kosdaq150"]["mapping"])
        self.assertIn("0220W0", kp)
        self.assertIn("0013Y0", kq)
        self.assertIn("005930", kp)
        self.assertEqual(len(kp), 826)
        self.assertEqual(len(kq), 1733)

    def test_unaccounted_current_constituent_holds_comparison(self):
        base = self.base("kospi200")
        next(row for row in base["all"] if row["is_cur"])["is_cur"] = False
        result = classification_comparison(base, self.configs["kospi200"], self.payload, analyze.select_krx_rows)
        self.assertEqual(result["status"], "HOLD")
        self.assertEqual(result["scenarios"], {})


class SelectionBoundaryTests(unittest.TestCase):
    @staticmethod
    def row(code, cap, current=False, sector="산업재"):
        return {"code": code, "name": code, "sector": sector, "avg_mcap": cap,
                "avg_trdval": cap, "is_cur": current}

    def test_default_a_requires_primary_and_b_is_separate_sensitivity(self):
        rows = [self.row("000001", 1000, True), self.row("000002", 10, True),
                self.row("000003", 9), self.row("000004", 8, True),
                self.row("000005", 7, True), self.row("000006", 6, True)]
        args = dict(N=4, cum_pct=.85, liq_pct=1, keep_buf=1.1, new_buf=.9)
        a, _ = analyze.simulate(copy.deepcopy(rows), set(), **args)
        b, _ = analyze.simulate(copy.deepcopy(rows), set(), **args, new_requires_primary=False)
        self.assertNotIn("000003", a)
        self.assertIn("000003", b)

    def test_industry_one_percent_exclusion_is_conditional_and_not_refilled(self):
        rows = [self.row("000001", 99.01), self.row("000002", .99, sector="에너지화학")]
        selected, dropped = analyze.simulate(rows, set(), 2, .85, 1, 1.1, .9, sector_min_share=.01)
        self.assertEqual(dropped, ["에너지화학"])
        self.assertNotIn("000002", selected)
        rows = [self.row("000001", 99), self.row("000002", 1, sector="에너지화학")]
        selected, dropped = analyze.simulate(rows, set(), 2, .85, 1, 1.1, .9, sector_min_share=.01)
        self.assertEqual(dropped, [])
        self.assertIn("000002", selected)

    def test_under_three_existing_sector_exception_preserves_first_selection(self):
        rows = [self.row("000001", 3, True), self.row("000002", 2, True), self.row("000003", 1)]
        selected, _ = analyze.simulate(rows, set(), 3, 1, 1, 1.1, .9)
        self.assertIn("000003", selected)

    def test_liquidity_rank_fraction_is_floored(self):
        rows = [self.row("000001", 100)]
        selected, _ = analyze.simulate(rows, set(), 1, .85, .85, 1.1, .9)
        self.assertEqual(selected, set())


if __name__ == "__main__":
    unittest.main()
