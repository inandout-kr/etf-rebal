import copy
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import analyze
import fetch_krics
from classification_scenarios import classification_comparison, load_collection_status
from fetch_krics import collect_snapshot, refresh_mapping


class FixtureAPI:
    def __init__(self):
        labels = ["에너지화학", "소재", "산업재", "모빌리티", "정보기술", "금융 및 부동산", "소비재", "헬스케어", "미디어·콘텐츠"]
        counts = [3, 1, 5, 2, 4, 2, 2, 2, 3]
        self.options, self.members, self.stocks, self.calls = [], {}, {}, []
        for i, (label, count) in enumerate(zip(labels, counts), 1):
            sector = str(i * 10)
            self.options.append({"value": sector, "label": f"{label}({sector})"})
            self.members[sector] = []
            for n in range(1, count + 1):
                group = sector + str(n * 10)
                self.options.append({"value": group, "label": f"{label}-그룹{n}({sector}-{n * 10})"})
                rows = []
                for suffix, market in ((0, "KOSPI"), (1, "KOSDAQ")):
                    code = f"{int(group) * 10 + suffix:06d}"
                    name = "fixture" + code
                    rows.append({"isu_cd": code, "isu_abbr": name, "prsnt_prc": "1,000"})
                    self.stocks[code] = {"code": code, "name": name, "market": market, "universe_active": True}
                self.members[group] = rows
                self.members[sector].extend(rows)

    def __call__(self, kind, group, date):
        self.calls.append((kind, group, date))
        if kind == "01": return {"block1": copy.deepcopy(self.options)}
        if kind == "02": return {"block1": [{"isu_cnt": str(len(self.members[group]))}]}
        return {"block1": copy.deepcopy(self.members[group])}


class RefreshTests(unittest.TestCase):
    observed = "2026-11-02T08:30:00+09:00"
    now = datetime.fromisoformat("2026-11-02T09:00:00+09:00")

    def collect(self, api=None, observed=None):
        api = api or FixtureAPI()
        return collect_snapshot(api, "20261030", observed or self.observed, api.stocks, set(api.stocks))

    def comparison(self, payload, status=None):
        rows = []
        for i, r in enumerate(payload["records"]):
            if r["market"] != "KOSDAQ": continue
            rows.append({"code": r["code"], "name": r["name"], "sector": "GICS", "is_cur": True,
                         "avg_mcap": 100, "avg_trdval": 100, "mcap15": 100, "mcap15_n_days": 15,
                         "market_mcap_rank": i + 1, "status": "유지"})
        base = {"key": "kosdaq150", "all": rows, "data_through": "20261030", "price_data_through": "20261030",
                "n_current": len(rows), "window": {"ref_date": "20261030"}, "excluded_current": []}
        cfg = dict(name="fixture", market="KOSDAQ", N=len(rows), cum=.6, liq=1,
                   keep_buf=1.2, new_buf=.8, sector_min_share=.01, small_cap_exclusion=True, rules="fixture")
        status = status or {"schema_version": 1, "status": "ok", "attempted_at": payload["observed_at"],
                            "requested_date": payload["requested_date"], "snapshot_id": payload["snapshot_id"]}
        return classification_comparison(base, cfg, payload, analyze.select_krx_rows,
                                         snapshot_now=self.now, collection_status=status)

    def test_complete_current_snapshot_has_dynamic_counts_and_no_effective_date_claim(self):
        api = FixtureAPI()
        payload = self.collect(api)
        self.assertEqual(len(api.calls), 67)
        self.assertEqual(len(payload["records"]), 48)
        self.assertEqual(payload["sources"]["kosdaq150"]["expected_records"], 24)
        self.assertIsNone(payload["returned_as_of"])
        self.assertFalse(payload["historical_asof_supported"])
        self.assertIsNone(payload["valid_from"])
        result = self.comparison(payload)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["classification_date_status"], "current_snapshot_after_review")
        self.assertEqual(result["current_snapshot_date"], "20261102")
        self.assertEqual(result["effective_date_status"], "HOLD")
        self.assertFalse(result["official_result"])
        self.assertEqual(set(result["smallcap_scenarios"]), {"applied", "not_applied"})

    def test_utc_observation_is_compared_on_the_korean_market_day(self):
        payload = self.collect(observed="2026-11-01T23:30:00+00:00")
        result = self.comparison(payload)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["current_snapshot_date"], "20261102")

    def test_same_query_date_does_not_make_stale_or_failed_snapshot_ready(self):
        payload = self.collect()
        for mutation in ("stale", "failed", "wrong_id", "future", "future_attempt", "historical_claim", "bad_date", "partial"):
            changed = copy.deepcopy(payload)
            status = {"schema_version": 1, "status": "ok", "attempted_at": changed["observed_at"],
                      "requested_date": changed["requested_date"], "snapshot_id": changed["snapshot_id"]}
            if mutation == "stale": changed["observed_at"] = "2026-11-01T08:30:00+09:00"
            if mutation == "failed": status["status"] = "HOLD"
            if mutation == "wrong_id": status["snapshot_id"] = "not-the-cache"
            if mutation == "future": changed["observed_at"] = "2026-11-03T08:30:00+09:00"
            if mutation == "future_attempt": status["attempted_at"] = "2026-11-02T10:00:00+09:00"
            if mutation == "historical_claim": changed["returned_as_of"] = "20261030"
            if mutation == "bad_date": changed["requested_date"] = "20260230"
            if mutation == "partial": changed["records"].pop()
            with self.subTest(mutation=mutation):
                result = self.comparison(changed, status)
                self.assertEqual(result["status"], "HOLD")
                self.assertEqual(result["scenarios"], {})
                self.assertEqual(result["smallcap_scenarios"], {})

    def test_partial_duplicate_summary_and_sector_mismatch_reject_before_cache_replacement(self):
        for mutation in ("summary", "duplicate", "sector", "missing", "identity", "empty", "options"):
            api = FixtureAPI()
            expected = set(api.stocks)
            if mutation == "duplicate": api.members["1020"][0] = copy.deepcopy(api.members["1010"][0])
            if mutation == "sector": api.members["10"][0] = {"isu_cd": "ZZZZZZ", "isu_abbr": "wrong"}
            if mutation == "missing": expected.add("ZZZZZZ")
            if mutation == "identity": api.stocks[next(iter(api.stocks))]["name"] = "renamed"
            if mutation == "empty": expected = set()
            if mutation == "options": api.options.pop()
            fetch = api
            if mutation == "summary":
                def fetch(kind, group, date, original=api):
                    return {"block1": [{"isu_cnt": "999"}]} if kind == "02" else original(kind, group, date)
            with tempfile.TemporaryDirectory() as folder:
                cache, status_path = Path(folder) / "mapping.json", Path(folder) / "status.json"
                cache.write_text("prior verified cache", encoding="utf-8")
                before = cache.read_bytes()
                status = refresh_mapping(cache, status_path, fetch, "20261030", self.observed, api.stocks, expected)
                with self.subTest(mutation=mutation):
                    self.assertEqual(status["status"], "HOLD")
                    self.assertEqual(cache.read_bytes(), before)
                    self.assertEqual(load_collection_status(status_path)["status"], "HOLD")

    def test_official_alias_preserves_original_option_label(self):
        payload = self.collect()
        labels = [r["label"] for r in payload["validation"]["official_options"]]
        self.assertIn("미디어·콘텐츠(90)", labels)
        self.assertIn("미디어 및 콘텐츠", {r["sector"] for r in payload["records"]})

    def test_preflight_failure_records_hold_without_exposing_error_paths(self):
        with patch.object(fetch_krics, "run_collection", side_effect=OSError("private/path")), \
                patch.object(fetch_krics, "atomic_json") as write:
            self.assertEqual(fetch_krics.main(), 0)
        status = write.call_args.args[1]
        self.assertEqual(status["status"], "HOLD")
        self.assertIsNone(status["snapshot_id"])
        self.assertNotIn("private/path", json.dumps(status))

    def test_status_storage_failure_is_fatal_and_pipeline_does_not_allow_it(self):
        with patch.object(fetch_krics, "run_collection", side_effect=OSError("setup")), \
                patch.object(fetch_krics, "atomic_json", side_effect=OSError("status write")):
            with self.assertRaises(OSError): fetch_krics.main()
        pipeline = (Path(__file__).resolve().parents[1] / "update.ps1").read_text(encoding="utf-8-sig")
        line = next(line for line in pipeline.splitlines() if 'Arguments @("fetch_krics.py")' in line)
        self.assertNotIn("AllowFailure", line)


if __name__ == "__main__": unittest.main()
