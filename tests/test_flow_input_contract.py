"""Financial input boundaries and shared-flow API regressions (offline, synthetic)."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import flow_calculation as fc
import flow_engine
import rebal_flow


class FlowInputContractTests(unittest.TestCase):
    def setUp(self):
        self.codes = ["H", "A", "B", "C", "D", "E"]
        self.stocks = {c: {"name": c, "mktcap": 100, "close": 10} for c in self.codes}
        self.holding = {"etf_code": "ETF", "date": "2026-12-10",
                        "rows": [{"code": c, "name": c, "weight": 100 / 6} for c in self.codes]}
        self.cfg = {"key": "kospi200_it", "name": "IT", "proxy": "it", "etfs": [("ETF", 1)],
                    "weighting": {"type": "float", "cap": .2}}
        self.cycle = {"apply": "2026-12-11", "trade_date": "2026-12-10", "trade_end": "2026-12-10"}
        self.analysis = {"last_daily": "20261210", "fif": {c: 1 for c in self.codes}}
        self.membership_input = {"status": "official", "source_url": "https://example.test/official-krics",
            "as_of": "20261130", "rule_id": "P17_appendix1_kospi200_it", "classifications": {
                c: {"krics1": "정보기술", "krics2": "반도체및디스플레이", "is_holding_company": False} for c in self.codes}}
        self.membership_input["classifications"]["H"] = {"krics1": "산업재", "krics2": "복합기업",
            "is_holding_company": True, "major_subsidiary": {"code": "A", "verified": True,
                "source_url": "https://example.test/major-subsidiary", "krics1": "정보기술", "krics2": "반도체및디스플레이"}}
        self.inputs = {"official_ffr": {"status": "official", "source_url": "https://example.test/ffr", "as_of": "20261130",
                        "values": {c: 1 for c in self.codes}},
                       "official_cap_factors": {"status": "official", "source_url": "https://example.test/cf", "as_of": "20261210",
                        "valid_from": "2026-12-11", "valid_to": "2027-06-10",
                        "reference_period": {"start": "2026-09-01", "end": "2026-11-30"},
                        "values": {c: 1 for c in self.codes}}}

    def membership(self):
        return fc.sector_membership("kospi200_it", self.codes, self.membership_input, "20261210")

    def event(self, inputs=None, membership=None):
        return fc.calculate_event(self.cfg, membership or self.membership(), self.stocks, {"it": self.holding},
            {"ETF": {"name": "ETF", "aum_eok": 1000}}, self.analysis, {c: 100000000 for c in self.codes},
            lambda day: {c: 10 for c in self.codes}, self.cycle, inputs)

    def test_holding_company_uses_verified_subsidiary_instead_of_parent_label(self):
        member = self.membership()
        self.assertEqual(member["status"], "ready")
        self.assertIn("H", member["codes"])
        event = self.event(self.inputs)
        self.assertEqual(event["availability"], "estimated")
        self.assertEqual(event["n_target"], 6)
        self.assertAlmostEqual(sum(r["w_target_raw"] for r in event["rows"]), 100)
        self.assertEqual(event["membership_evidence"]["rule"]["rule_id"], "P17_appendix1_kospi200_it")

    def test_missing_holding_exception_holds_whole_basket(self):
        del self.membership_input["classifications"]["H"]["major_subsidiary"]
        member = self.membership()
        self.assertIsNone(member["codes"])
        self.assertIn("holding_company_exception_unverified", [r["code"] for r in member["hold_reasons"]])
        event = self.event(self.inputs, member)
        self.assertIsNone(next(r for r in event["rows"] if r["code"] == "H")["flow_total"])

    def test_missing_classification_is_not_treated_as_exclusion(self):
        del self.membership_input["classifications"]["E"]
        member = self.membership()
        self.assertEqual(member["status"], "hold")
        self.assertIsNone(member["codes"])

    def test_capped_pdf_drift_never_becomes_ffr(self):
        self.holding["rows"][0]["weight"] = 18.6
        event = self.event({})
        self.assertEqual(event["availability"], "hold")
        self.assertIsNone(event["adds"])
        self.assertIsNone(event["dels"])
        self.assertIsNone(event["total_buy"])
        for row in event["rows"]:
            for field in ("w_target", "w_target_raw", "delta", "flow_total", "adv_mult", "relative_coefficient", "fif"):
                self.assertIsNone(row[field])
        self.assertIn("official_ffr_missing", [r["code"] for r in event["hold_reasons"]])

    def test_ffr_without_cap_factor_still_holds(self):
        del self.inputs["official_cap_factors"]
        event = self.event(self.inputs)
        self.assertEqual(event["availability"], "hold")
        self.assertIn("official_cap_factor_missing", [r["code"] for r in event["hold_reasons"]])

    def test_cap_factor_fixed_interval_allows_price_drift_above_cap(self):
        self.stocks["H"]["mktcap"] = 1000
        self.inputs["official_cap_factors"]["values"]["H"] = .125
        before = self.event(self.inputs)
        self.assertAlmostEqual(next(r for r in before["rows"] if r["code"] == "H")["w_target_raw"], 20)
        self.stocks["H"]["mktcap"] = 2000
        after = self.event(self.inputs)
        row = next(r for r in after["rows"] if r["code"] == "H")
        self.assertGreater(row["w_target_raw"], 20)
        self.assertEqual(row["cap_factor"], .125)
        self.assertEqual(row["official_ffr"], 1)
        self.assertIsNone(row["relative_coefficient"])

    def test_factor_precision_and_future_reference_period_hold(self):
        self.inputs["official_cap_factors"]["values"]["H"] = .12345
        self.assertEqual(self.event(self.inputs)["availability"], "hold")
        self.inputs["official_cap_factors"]["values"]["H"] = 1
        self.analysis["last_daily"] = "20261023"
        self.assertEqual(self.event(self.inputs)["availability"], "hold")

    def test_under_five_krx_sector_members_need_ffr_but_no_cap_factor(self):
        member = self.membership()
        member["codes"] = self.codes[:4]
        del self.inputs["official_cap_factors"]
        event = self.event(self.inputs, member)
        self.assertNotEqual(event["availability"], "hold")
        self.assertAlmostEqual(max(r["w_target_raw"] for r in event["rows"]), 25)

    def test_uncapped_relative_model_is_explicit_and_separate_from_ffr(self):
        self.cfg.update(key="kospi200", weighting={"type": "float", "cap": None})
        event = self.event({})
        self.assertEqual(event["weight_basis"], "uncapped_relative_coefficient_estimate")
        self.assertEqual(event["adv_basis"]["sessions"], 20)
        self.assertTrue(all(r["relative_coefficient"] is not None and r["official_ffr"] is None and r["cap_factor"] is None for r in event["rows"]))

    def test_observed_zero_weight_keeps_existing_uncapped_normalization(self):
        values = fc.relative_coefficients(["A", "B"], {"A": 0, "B": 100},
            {"A": {"mktcap": 100}, "B": {"mktcap": 100}})
        self.assertEqual(values["A"][0], .05)
        self.assertEqual(values["B"][0], 1)

    def test_notional_deducts_only_underlying_etfs_included_in_aum(self):
        rows = [{"name": "KODEX 코스닥150", "weight": 27.72}, {"name": "RISE 코스닥150", "weight": 1.2},
                {"name": "SOL 코스닥150", "weight": .37}, {"name": "KIWOOM 코스닥150", "weight": .08}]
        actual = fc.notional_metadata(35370, 2, rows, ["KODEX 코스닥150", "TIGER 코스닥150", "KODEX 코스닥150레버리지"])
        self.assertAlmostEqual(actual["notional_eok"], 60935.436)
        self.assertAlmostEqual(actual["deduction_eok"], 9804.564)
        self.assertEqual(len(actual["unmatched_holdings_not_deducted"]), 3)

    def test_held_event_never_becomes_zero_or_a_complete_total(self):
        held = self.event({})
        known = {"key": "known", "name": "known", "apply": "2026-12-11", "rows": [
            {"code": "H", "name": "H", "flow_total": 5, "status": "유지"}]}
        total = fc.aggregate_events([held, known])
        self.assertEqual(total["H"]["known_total"], 5)
        self.assertEqual(total["H"]["held_count"], 1)
        self.assertFalse(total["H"]["complete"])
        self.assertIsNone(total["A"]["total"])

    def test_other_etf_only_holding_outside_target_is_sale_not_addition(self):
        from validate_data import validate_flow_rows
        self.stocks["Z"] = {"name": "Z", "mktcap": 100, "close": 10}
        self.cfg["etfs"].append(("OTHER", 1))
        other = {"etf_code": "OTHER", "date": "2026-12-10",
                 "rows": [{"code": "Z", "name": "Z", "weight": 100}]}
        event = fc.calculate_event(self.cfg, self.membership(), self.stocks,
            {"it": self.holding, "other": other},
            {"ETF": {"name": "ETF", "aum_eok": 1000}, "OTHER": {"name": "OTHER", "aum_eok": 100}},
            self.analysis, {c: 100000000 for c in self.stocks},
            lambda day: {c: 10 for c in self.stocks}, self.cycle, self.inputs)
        row = next(r for r in event["rows"] if r["code"] == "Z")
        self.assertEqual(row["status"], "편출")
        self.assertEqual(row["w_target_raw"], 0)
        self.assertEqual(row["flow_total"], -100)
        self.assertEqual(row["flow_by_etf"]["OTHER"], -100)
        self.assertIn("Z", event["dels"])
        self.assertNotIn("Z", event["adds"])
        validate_flow_rows(event["rows"], event["key"], event["n_target"], event["total_buy"], event["total_sell"])

    def test_invalid_calendar_dates_hold_provenance_and_cap_intervals(self):
        for invalid in ("2026-00-00", "20260230", "2026-12-10junk"):
            with self.subTest(date=invalid):
                inputs = copy.deepcopy(self.inputs)
                inputs["official_ffr"]["as_of"] = invalid
                event = self.event(inputs)
                self.assertEqual(event["availability"], "hold")
                self.assertIn("official_ffr_input_invalid", [r["code"] for r in event["hold_reasons"]])
                mapping = copy.deepcopy(self.membership_input)
                mapping["as_of"] = invalid
                self.assertIsNone(fc.sector_membership("kospi200_it", self.codes, mapping, "20261210")["codes"])
        for field, invalid in (("valid_from", "2026-00-00"), ("valid_to", "2026-12-01"),
                               ("reference_period", {"start": "2026-09-01", "end": "2026-11-31"})):
            with self.subTest(cap_field=field):
                inputs = copy.deepcopy(self.inputs)
                inputs["official_cap_factors"][field] = invalid
                event = self.event(inputs)
                self.assertEqual(event["availability"], "hold")
                self.assertIn("official_cap_factor_input_invalid", [r["code"] for r in event["hold_reasons"]])
        tmi = {"status": "official", "source_url": "https://example.test/tmi", "as_of": "2026-00-00",
               "review_ref": "20260731", "eligibility_verified": True, "event_coverage_verified": True,
               "eligible_codes": ["S"], "previous_midlarge_codes": ["S"]}
        self.assertEqual(rebal_flow.resolve_tmi(tmi, {"S": {"avg_mcap": 100}}, "20260731", "20260731", False)["status"], "hold")

    def test_nonstring_dates_and_malformed_external_objects_fail_closed(self):
        inputs = copy.deepcopy(self.inputs)
        inputs["official_ffr"]["as_of"] = 20261130
        self.assertEqual(self.event(inputs)["availability"], "hold")
        for field, value in (("valid_from", 20261211), ("reference_period", ["2026-09-01", "2026-11-30"])):
            inputs = copy.deepcopy(self.inputs)
            inputs["official_cap_factors"][field] = value
            self.assertEqual(self.event(inputs)["availability"], "hold")
        self.assertEqual(self.event(["invalid"])["availability"], "hold")
        for field in ("official_ffr", "official_cap_factors"):
            inputs = copy.deepcopy(self.inputs)
            inputs[field] = ["invalid"]
            self.assertEqual(self.event(inputs)["availability"], "hold")
        self.assertEqual(rebal_flow.resolve_tmi(["invalid"], {}, "20260731", "20260731", False)["status"], "hold")
        base_tmi = {"status": "official", "source_url": "https://example.test/tmi", "as_of": "20260731",
                    "review_ref": "20260731", "eligibility_verified": True, "event_coverage_verified": True,
                    "eligible_codes": ["S"], "previous_midlarge_codes": ["S"]}
        for field in ("as_of", "review_ref"):
            tmi = {**base_tmi, field: 20260731}
            self.assertEqual(rebal_flow.resolve_tmi(tmi, {"S": {"avg_mcap": 100}}, "20260731", "20260731", False)["status"], "hold")
        for field in ("eligible_codes", "previous_midlarge_codes"):
            for value in (["S", "S"], [""], [123], [" S"]):
                tmi = {**base_tmi, field: value}
                self.assertEqual(rebal_flow.resolve_tmi(tmi, {"S": {"avg_mcap": 100}}, "20260731", "20260731", False)["status"], "hold")
        for payload in ([], {"schema_version": 1, "indexes": []},
                        {"schema_version": 1, "indexes": {"kospi200_it": []}}, {"schema_version": 1, "tmi": []}):
            with self.subTest(payload=payload), patch.object(Path, "exists", return_value=True), \
                 patch.object(Path, "read_text", return_value=json.dumps(payload)), self.assertRaises(ValueError):
                flow_engine.read_flow_inputs(Path("."))

    def test_ffr_and_cap_values_arrays_hold_instead_of_get_crash(self):
        for field, error_code in (("official_ffr", "official_ffr_values_invalid"),
                                  ("official_cap_factors", "official_cap_factor_values_invalid")):
            for value in ([], [1], None):
                with self.subTest(field=field, values=value):
                    inputs = copy.deepcopy(self.inputs)
                    inputs[field]["values"] = value
                    event = self.event(inputs)
                    self.assertEqual(event["availability"], "hold")
                    self.assertIn(error_code, [r["code"] for r in event["hold_reasons"]])
                    self.assertTrue(all(r["flow_total"] is None for r in event["rows"]))


class SemiconductorInputTests(unittest.TestCase):
    def test_tmi_buffer_boundaries(self):
        items = [("A", 3000), ("NEW133", 133), ("NEW100", 100), ("KEEP75", 75), ("DROP74", 74), ("tiny", 1)]
        result = rebal_flow.select_midlarge(items, ["A", "KEEP75", "DROP74"])
        self.assertEqual(result["cutoff_mcap"], 100)
        self.assertEqual(set(result["codes"]), {"A", "NEW133", "KEEP75"})
        self.assertIsNone(rebal_flow.select_midlarge(items, None)["codes"])

    def test_cap10_is_discretionary_and_only_tmi_layer(self):
        supplied = {"status": "official", "source_url": "https://example.test/tmi", "as_of": "20260731",
            "review_ref": "20260731", "eligibility_verified": True, "event_coverage_verified": True,
            "eligible_codes": ["S"], "previous_midlarge_codes": ["S"]}
        actual = rebal_flow.resolve_tmi(supplied, {"S": {"avg_mcap": 100}}, "20260731", "20260731", False, .1)
        self.assertEqual(actual["status"], "hold")
        self.assertIn("tmi_cap_discretion_unverified", [r["code"] for r in actual["hold_reasons"]])
        codes = ["S" + str(i) for i in range(20)]
        tmi = {"codes": codes, "coverage_cap": .1}
        mapping = {"status": "official", "source_url": "https://example.test/krics", "as_of": "20260731",
            "rule_id": "P14_appendix1_krx_semi", "classifications": {
                c: {"krics1": "정보기술", "krics2": "반도체및디스플레이", "krics3": "반도체"} for c in codes}}
        stats = {c: {"avg_mcap": 100, "avg_trdval": 100} for c in codes}
        with patch.object(rebal_flow, "coverage_select", wraps=rebal_flow.coverage_select) as coverage:
            result = rebal_flow.semiconductor_selection(tmi, mapping, stats, "20260731")
        self.assertEqual(coverage.call_args.args[1], .95)
        self.assertNotIn("coverage_cap", coverage.call_args.kwargs)
        self.assertIsNone(result["sector_coverage_cap"])

    def test_display_is_not_semiconductor_and_missing_level3_holds(self):
        mapping = {"status": "official", "source_url": "https://example.test/krics", "as_of": "20260731",
            "rule_id": "P14_appendix1_krx_semi", "classifications": {
                "S": {"krics1": "정보기술", "krics2": "반도체및디스플레이", "krics3": "반도체"},
                "D": {"krics1": "정보기술", "krics2": "반도체및디스플레이", "krics3": "디스플레이"}}}
        self.assertEqual(fc.sector_membership("krx_semi", ["S", "D"], mapping, "20260731")["codes"], ["S"])
        del mapping["classifications"]["D"]["krics3"]
        self.assertIsNone(fc.sector_membership("krx_semi", ["S", "D"], mapping, "20260731")["codes"])


if __name__ == "__main__":
    unittest.main()
