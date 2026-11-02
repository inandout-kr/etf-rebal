import copy
import json
import tempfile
import unittest
from pathlib import Path

from krics_history import read_last_good, save_last_good


def result(key, as_of):
    count = 200 if key == "kospi200" else 150
    rows = [{"code": f"{i:06d}", "name": f"stock{i}", "status": "유지", "is_cur": True,
             "mktcap_now": 12345, "fif": .7, "avg_mcap": 1000} for i in range(count)]
    rows[0]["code"] = "0009K0"
    rows[-1].update(status="신규편입", is_cur=False)
    rows.append({"code": "999990", "name": "old", "status": "편출", "is_cur": True})
    return {"key": key, "name": key, "data_through": as_of, "n_selected": count,
            "n_current": count, "n_universe": count + 1, "all": rows,
            "adds": [rows[-2]], "dels": [rows[-1]], "watch_keep": [], "watch_new": []}


def analysis(as_of="20261023", price_end=None):
    value = {"last_daily": price_end or as_of, "generated": "2026-10-26 13:09"}
    for key in ("kospi200", "kosdaq150"):
        value[key] = result(key, as_of)
        variants = {policy: {label: result(key, as_of) for label in ("A", "B")}
                    for policy in ("applied", "not_applied")}
        comparison = {"status": "ready", "as_of": as_of, "input_hash": "a" * 64,
            "mapping_hash": "b" * 64, "hold_reasons": [], "scenarios": variants["applied"],
            "coverage": {"review_rows": value[key]["n_universe"], "mapped_rows": value[key]["n_universe"]},
            "source": {"source_id": "user_test", "source_read_location": "private-do-not-export"}}
        if key == "kosdaq150":
            value[key]["smallcap_scenarios"] = {policy: result(key, as_of) for policy in variants}
            comparison["smallcap_scenarios"] = variants
        value[key]["classification_scenarios"] = comparison
    return value


class KricsHistoryTests(unittest.TestCase):
    def test_ready_snapshot_is_derived_only_and_preserves_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            status = save_last_good(analysis(), path)
            self.assertEqual(set(status["updated"]), {"kospi200", "kosdaq150"})
            saved = read_last_good(path)["indexes"]["kosdaq150"]
            self.assertEqual(saved["as_of"], "20261023")
            self.assertIn("0009K0", saved["krics"]["not_applied"]["B"]["selected_codes"])
            for outcome in (saved["gics"]["applied"], saved["krics"]["not_applied"]["B"]):
                self.assertNotIn("all", outcome)
                self.assertEqual(outcome["flow_availability"], "hold")
                self.assertNotIn("mktcap_now", outcome["adds"][0])
                self.assertNotIn("fif", outcome["adds"][0])
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("private-do-not-export", text)

    def test_hold_and_invalid_new_candidate_preserve_last_good_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            save_last_good(analysis(), path)
            before = path.read_bytes()
            future = analysis("20261030")
            for key in ("kospi200", "kosdaq150"):
                future[key]["classification_scenarios"].update(status="HOLD", scenarios={}, smallcap_scenarios={})
            self.assertEqual(save_last_good(future, path)["updated"], [])
            self.assertEqual(path.read_bytes(), before)
            invalid = analysis("20261030")
            for key in ("kospi200", "kosdaq150"):
                invalid[key]["classification_scenarios"]["coverage"]["missing_codes"] = ["0009K0"]
            self.assertEqual(save_last_good(invalid, path)["updated"], [])
            self.assertEqual(path.read_bytes(), before)

    def test_recovery_archives_prior_success_and_only_timestamp_changes_do_not_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            save_last_good(analysis(), path)
            before = path.read_bytes()
            same = analysis()
            same["generated"] = "2026-10-26 14:00"
            self.assertEqual(save_last_good(same, path)["updated"], [])
            self.assertEqual(path.read_bytes(), before)
            recovered = analysis("20261030")
            recovered["generated"] = "2026-11-02 09:00"
            recovered["kosdaq150"]["classification_scenarios"].update(
                current_snapshot_date="20261102", review_ref_date="20261030",
                classification_date_status="current_snapshot_after_review", date_warning="현재 분류 관측, 과거 효력 미확인")
            save_last_good(recovered, path)
            saved = read_last_good(path)
            self.assertEqual(saved["indexes"]["kosdaq150"]["as_of"], "20261030")
            self.assertEqual(saved["indexes"]["kosdaq150"]["current_snapshot_date"], "20261102")
            self.assertEqual(saved["indexes"]["kosdaq150"]["date_warning"], "현재 분류 관측, 과거 효력 미확인")
            for key in ("kospi200", "kosdaq150"):
                self.assertEqual({s["as_of"] for s in saved["history"][key].values()}, {"20261023"})

    def test_completed_review_can_be_saved_after_later_market_prices_arrive(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            later = analysis("20261030", "20261102")
            save_last_good(later, path)
            saved = read_last_good(path)["indexes"]["kosdaq150"]
            self.assertEqual(saved["as_of"], "20261030")
            self.assertEqual(saved["price_data_through"], "20261102")
            self.assertEqual(saved["krics"]["applied"]["A"]["data_through"], "20261030")

    def test_partial_failure_is_not_saved_over_other_indexes_success(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            save_last_good(analysis(), path)
            newer = analysis("20261030")
            newer["kosdaq150"]["classification_scenarios"]["smallcap_scenarios"]["not_applied"]["B"]["all"].append(
                copy.deepcopy(newer["kosdaq150"]["classification_scenarios"]["smallcap_scenarios"]["not_applied"]["B"]["all"][0]))
            changed = save_last_good(newer, path)
            self.assertEqual(changed["updated"], ["kospi200"])
            saved = read_last_good(path)
            self.assertEqual(saved["indexes"]["kospi200"]["as_of"], "20261030")
            self.assertEqual(saved["indexes"]["kosdaq150"]["as_of"], "20261023")

    def test_corrupt_existing_history_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            path.write_text("not json", encoding="utf-8")
            with self.assertRaises(ValueError):
                save_last_good(analysis(), path)
            self.assertEqual(path.read_text(encoding="utf-8"), "not json")


if __name__ == "__main__":
    unittest.main()
