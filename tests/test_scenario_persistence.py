import copy
import tempfile
import unittest
from pathlib import Path

import archive
import build_site
from flow_calculation import aggregate_events
from flow_engine import krx_variants, variant_membership


def selected(code):
    return {"code": code, "name": code, "status": "신규편입", "is_cur": False}


def result(code):
    return {"name": "KOSDAQ 150", "all": [selected(code)], "adds": [selected(code)], "dels": []}


def flow(code, held=False):
    return {"key": "kosdaq150", "name": "KOSDAQ 150", "apply": "2026-12-11", "passed": False,
        "proxy_etf": "229200", "availability": "hold" if held else "estimated", "hold_reasons": [],
        "target_src": "scenario", "notional_total": 1000, "etfs": [],
        "adds": None if held else [code], "dels": None if held else [],
        "total_buy": None if held else 10, "total_sell": None if held else -10,
        "rows": [{"code": code, "name": code, "status": "검토보류" if held else "신규편입",
                  "w_cur": 0, "w_target": None if held else 1, "delta": None if held else 1,
                  "flow_total": None if held else 10, "adv_mult": None if held else 1}]}


class ScenarioPersistenceTests(unittest.TestCase):
    def test_axis_keys_and_future_hold_do_not_erase_gics(self):
        base = result("000010")
        base["smallcap_scenarios"] = {"applied": result("000010"), "not_applied": result("000020")}
        base["classification_scenarios"] = {"status": "ready", "smallcap_scenarios": {
            policy: {"A": result("000030"), "B": result("000040")}
            for policy in ("applied", "not_applied")}}
        self.assertEqual(len(krx_variants(base)), 6)
        self.assertEqual(variant_membership(base, "GICS:not_applied")["hold_reasons"], [])
        held = variant_membership(base, "B:not_applied")
        self.assertEqual(held["codes"], ["000010"])
        self.assertEqual(held["status"], "hold")
        self.assertTrue(held["hold_reasons"])
        base["classification_scenarios"]["status"] = "HOLD"
        self.assertEqual(set(krx_variants(base)), {"GICS:applied", "GICS:not_applied"})

    def test_alternative_flows_are_not_aggregated_with_default(self):
        default = flow("000010")
        default["scenario_flows"] = {"GICS:applied": flow("000010"), "GICS:not_applied": flow("000020")}
        aggregate = aggregate_events([default])
        self.assertEqual(set(aggregate), {"000010"})
        self.assertEqual(aggregate["000010"]["total"], 10)

    def test_archive_distinguishes_axes_and_does_not_turn_hold_into_zero(self):
        event = flow("000010")
        event["scenario_id"] = "GICS:applied"
        event["scenario_flows"] = {"GICS:applied": flow("000010"),
                                   "GICS:not_applied": flow("000020"), "A:applied": flow("000030", True)}
        holdings = {"kosdaq150": {"etf_code": "229200", "date": "2026-12-09", "rows": []}}
        saved = archive.from_flow(event, holdings, "2026-12-10 08:10")
        self.assertEqual(set(saved["scenarios"]), {"GICS:applied", "GICS:not_applied"})
        self.assertEqual(saved["default_scenario"], "GICS:applied")
        self.assertEqual(saved["scenarios"]["GICS:not_applied"]["adds"], ["000020"])
        self.assertEqual(saved["held_scenarios"]["A:applied"]["availability"], "hold")
        self.assertNotIn("total_buy", saved["held_scenarios"]["A:applied"])
        self.assertNotIn("rows", saved["held_scenarios"]["A:applied"])

    def test_same_day_rebuild_preserves_first_snapshot_and_deduplicates_timestamp(self):
        original = {"date": "2026-10-23", "generated": "2026-10-26 08:00", "candidates": [], "flows": {}}
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            primary = build_site.save_daily_snapshot(directory, original)
            before = primary.read_bytes()
            current = copy.deepcopy(original)
            current.update(generated="2026-10-26 11:00", scenario_candidates={"kosdaq150:GICS:not_applied": {"adds": [], "dels": []}})
            revision = build_site.save_daily_snapshot(directory, current)
            self.assertNotEqual(primary, revision)
            self.assertEqual(primary.read_bytes(), before)
            current["generated"] = "2026-10-26 11:30"
            self.assertEqual(build_site.save_daily_snapshot(directory, current), revision)
            self.assertEqual(len(list((directory / "revisions").glob("*.json"))), 1)
            current["scenario_candidates"]["kosdaq150:GICS:not_applied"]["adds"] = [{"code": "000010", "name": "A"}]
            self.assertNotEqual(build_site.save_daily_snapshot(directory, current), revision)

    def test_held_default_does_not_erase_an_independently_available_variant(self):
        event = flow("000010", True)
        event["scenario_id"] = "GICS:applied"
        event["scenario_flows"] = {"GICS:applied": flow("000010", True),
                                   "GICS:not_applied": flow("000020")}
        holdings = {"kosdaq150": {"etf_code": "229200", "date": "2026-12-09", "rows": []}}
        saved = archive.from_flow(event, holdings, "2026-12-10 08:10")
        self.assertEqual(saved["default_scenario"], "GICS:not_applied")
        self.assertEqual(saved["scenarios"]["GICS:not_applied"]["total_buy"], 10)
        self.assertEqual(saved["held_scenarios"]["GICS:applied"]["availability"], "hold")

    def test_legacy_snapshot_starts_scenario_baseline_without_false_changes(self):
        previous = {"date": "2026-10-22", "candidates": [], "flows": {}}
        current = {"date": "2026-10-23", "candidates": [], "flows": {},
            "scenario_candidates": {"kosdaq150:B:not_applied": {"adds": [{"code": "000010"}], "dels": []}}}
        changes = build_site.compare_snapshots(current, previous)
        self.assertEqual(changes["scenario_status"], "baseline_created")
        self.assertEqual(changes["scenario_changes"], [])
        before = copy.deepcopy(current)
        before["date"] = "2026-10-22"
        before["scenario_candidates"]["kosdaq150:B:not_applied"]["adds"] = []
        changes = build_site.compare_snapshots(current, before)
        self.assertEqual(changes["scenario_changes"], [{"scenario_id": "kosdaq150:B:not_applied", "direction": "adds", "new": ["000010"], "removed": []}])


if __name__ == "__main__":
    unittest.main()
