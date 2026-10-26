"""Document-backed metadata contracts; no market fetch, pipeline or deployment."""
import json
from pathlib import Path
import unittest

import methodology_kb


class MethodologyMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kb = methodology_kb.build()
        cls.indexes = cls.kb["indexes"]
        cls.review = cls.kb["methodology_review"]

    def test_public_output_keeps_existing_maps_and_adds_review(self):
        self.assertTrue({"indexes", "etf_index_key", "methodology_review"}.issubset(self.kb))
        self.assertEqual(self.kb["etf_index_key"]["코스피 100"], "kospi100")
        self.assertEqual(self.kb["etf_index_key"]["FnGuide 배당주 지수"], "fn_dividend")
        json.dumps(self.kb, ensure_ascii=False, allow_nan=False)

    def test_review_completion_does_not_confirm_effective_date_or_official_results(self):
        self.assertEqual(self.review["status"], "reviewed")
        self.assertEqual(self.review["effective_date_status"], "HOLD")
        self.assertIsNone(self.review["effective_date"])
        self.assertIs(self.review["official_result"], False)
        self.assertIs(self.review["automatic_regime_switch"], False)
        for key in ("kospi200", "kosdaq150", "kospi100", "k200_sector", "krx_sector"):
            with self.subTest(index=key):
                status = self.indexes[key]["amendment_review"]
                self.assertEqual(status["effective_date_status"], "HOLD")
                self.assertIsNone(status["effective_date"])
                self.assertIs(status["official_result"], False)
                self.assertIs(status["automatic_regime_switch"], False)

    def test_filename_and_external_notice_dates_are_not_effective_dates(self):
        for source in self.review["sources"]:
            with self.subTest(source=source["id"]):
                self.assertIsNone(source["effective_date"])
                self.assertEqual(source["effective_date_status"], "HOLD")
        for key in ("kospi200", "kosdaq150"):
            transition = self.indexes[key]["classification_transition"]
            self.assertIsNone(transition["effective_date"])
            self.assertEqual(transition["effective_date_status"], "HOLD")
            self.assertIn("external_notice_expected_date", transition)

    def test_three_comparison_results_share_frozen_input_with_two_krics_interpretations(self):
        comparison = self.review["classification_comparison"]
        self.assertEqual(comparison["data_basis"], "same_frozen_review_rows")
        self.assertEqual(comparison["result_count"], 3)
        self.assertEqual(comparison["baseline"]["id"], "GICS")
        self.assertEqual(comparison["baseline"]["classification"], "GICS")
        self.assertEqual(comparison["baseline"]["ruleset"], "legacy_reference")
        for key in ("A", "B"):
            self.assertEqual(comparison[key]["id"], f"KRICS-{key}")
            self.assertEqual(comparison[key]["classification"], "KRICS")
            self.assertEqual(comparison[key]["ruleset"], "amended_document_scenario")
        self.assertEqual(comparison["A"]["interpretation"], "primary_and_new_buffer")
        self.assertEqual(comparison["B"]["interpretation"], "liquid_and_new_buffer")

    def test_amended_reserve_and_ipo_removal_are_separate_from_legacy(self):
        for key in ("kospi200", "kosdaq150", "kospi100"):
            with self.subTest(index=key):
                index = self.indexes[key]
                self.assertEqual(index["reserve_policy"]["amended"], "none")
                self.assertIs(index["reserve_policy"]["automatic_replacement"], False)
                self.assertEqual(index["ipo_removal_policy"]["amended"], "no_automatic_smallest_removal")
                self.assertEqual(index["legacy_reference"]["ruleset"], "legacy_reference")
                self.assertTrue(index["legacy_reference"]["reserve_policy"])
                self.assertTrue(index["legacy_reference"]["ipo_removal_policy"])

    def test_regular_largecap_smallest_removal_is_preserved(self):
        # Deleting the IPO clause must not delete the distinct 6.4.4 regular-review rule.
        for key in ("kospi200", "kosdaq150"):
            with self.subTest(index=key):
                self.assertIn("6.4.4", self.indexes[key]["ipo_removal_policy"]["regular_largecap_rule"])
                self.assertTrue(any("6.4.4" in text and "최소" in text for text in self.indexes[key]["selection"]))

    def test_both_ipo_gates_require_official_fif_and_half_cutoff_mcap(self):
        # KQ150 formerly described only rank30 and silently omitted the float gate.
        for key, limit in (("kospi200", 50), ("kosdaq150", 30)):
            with self.subTest(index=key):
                condition = self.indexes[key]["ipo_conditions"]
                self.assertEqual(condition["market_common_rank_limit"], limit)
                self.assertEqual(condition["average_sessions"], 15)
                self.assertEqual(condition["official_average_float_to_cutoff_full_mcap_min"], 0.5)
                self.assertIs(condition["official_float_required"], True)
                self.assertIs(condition["admission_discretionary"], True)

    def test_kospi100_cap_matches_etf_display_and_document(self):
        self.assertEqual(self.indexes["kospi100"]["cap_level"], 0.30)
        self.assertIn("30%", self.indexes["kospi100"]["cap"])
        root = Path(methodology_kb.__file__).parent
        rows = json.loads((root / "data" / "etf_master.json").read_text(encoding="utf-8-sig"))
        row = next(item for item in rows if item["code"] == "237350")
        self.assertEqual(row["bm"], "코스피 100")
        self.assertIn("30%", row["cap"])
        self.assertNotIn("없음", row["cap"])
        self.assertIn("P01", row["cap_check"])
        self.assertIn("6·12월", row["cap_check"])

    def test_universe_cap_and_final_sector_cap_are_not_conflated(self):
        semi = self.indexes["krx_sector"]
        self.assertTrue(any("10%" in text and "유니버스" in text for text in semi["selection"]))
        self.assertIn("20%", semi["cap"])
        self.assertIn("5종목 미만", semi["cap"])

    def test_review_of_18_unsupported_documents_adds_no_index_implementation(self):
        scope = self.review["document_scope"]
        self.assertEqual(scope["local_pdf_count"], 24)
        self.assertEqual(scope["unsupported_count"], 18)
        self.assertEqual(len(set(scope["unsupported_document_ids"])), 18)
        self.assertEqual(len(scope["documents_related_to_current_scope"]), 6)
        self.assertFalse(set(scope["unsupported_document_ids"]) & set(scope["documents_related_to_current_scope"]))
        self.assertIs(scope["unsupported_index_implementations_added"], False)
        self.assertTrue(set(self.indexes).issubset(set(methodology_kb.INDEX_KB) | set(methodology_kb.FN_RULE_KEY)))
        for name in ("코스닥 TOP 10", "코스닥 TOP 15", "코스피 200 금융 고배당 TOP 10"):
            self.assertNotIn(name, self.kb["etf_index_key"])

    def test_reserve_abolition_does_not_spread_to_top_series(self):
        policies = self.review["document_scope"]["reserve_policy_by_document"]
        for ident in ("P06", "P11", "P13", "P15", "P18", "P19", "P21"):
            with self.subTest(document=ident):
                self.assertEqual(policies[ident], "retained")
        for ident in ("P01", "P03", "P04", "P05"):
            self.assertEqual(policies[ident], "abolished")

    def test_top15_full_and_float_conflict_is_explicit_hold(self):
        issue = next(row for row in self.review["issues"] if row["id"] == "kosdaq_top15_size_basis")
        self.assertEqual(issue["status"], "HOLD")
        self.assertEqual(issue["implementation_status"], "unsupported")
        self.assertIn("일평균시가총액", issue["standalone"])
        self.assertIn("일평균유동시가총액", issue["amendment"])

    def test_mapping_provenance_is_public_and_not_an_official_result(self):
        sources = self.review["mapping_sources"]
        self.assertIs(sources["official_result"], False)
        self.assertEqual(sources["kospi200"]["expected_records"], 826)
        self.assertEqual(sources["kosdaq150"]["expected_records"], 1733)
        for key in ("kospi200", "kosdaq150"):
            with self.subTest(index=key):
                self.assertIs(sources[key]["original_workbook_published"], False)
                self.assertTrue(sources[key]["source_id"].startswith("user_krics_"))
                self.assertNotIn("library_file_id", sources[key])
        self.assertIn("전체", sources["kosdaq150"]["access_basis"])

    def test_consumers_cannot_mutate_the_next_build_review(self):
        first = methodology_kb.build()
        first["methodology_review"]["effective_date_status"] = "confirmed"
        first["indexes"]["kospi100"]["reserve_policy"]["automatic_replacement"] = True
        first["etf_index_key"]["코스피 100"] = "wrong"
        second = methodology_kb.build()
        self.assertEqual(second["methodology_review"]["effective_date_status"], "HOLD")
        self.assertIs(second["indexes"]["kospi100"]["reserve_policy"]["automatic_replacement"], False)
        self.assertEqual(second["etf_index_key"]["코스피 100"], "kospi100")


if __name__ == "__main__":
    unittest.main()
