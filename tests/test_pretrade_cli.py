import contextlib
import io
import json
import sqlite3
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pretrade_cli


class PretradeCliTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.db = self.root / "market.sqlite"
        with contextlib.closing(sqlite3.connect(self.db)) as con:
            con.execute("CREATE TABLE daily (code TEXT, date TEXT, close REAL)")
            con.executemany("INSERT INTO daily VALUES (?, ?, ?)",
                            [("00AB10", "20260910", 20_000), ("000660", "20260910", 100_000)])
            con.commit()
        self.rows = [{"code": "00AB10", "name": "Example A", "flow_total": 2,
                      "flow_by_etf": {"ETF1": 3, "ETF2": -1}},
                     {"code": "000660", "name": "Example B", "flow_total": -1,
                      "flow_by_etf": {"ETF1": -1}},
                     {"code": "NO_PRICE", "flow_total": 0, "flow_by_etf": {"ETF1": 4, "ETF2": -4}}]
        self.event = {"key": "example", "trade_date": "2026-09-11", "trade_end": "2026-09-11",
                      "pdf_date": "2026-09-10", "note": "Synthetic fixture", "rows": self.rows}
        self.source = self.save("flows.json", {"generated": "2026-09-11 08:30",
                                               "data_through": "20260910", "events": [self.event]})

    def save(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def freeze(self, **kwargs):
        inputs = dict(source_path=self.source, event_id="example", window_start="2026-09-11T09:00:00+09:00",
                      available_at="2026-09-11T08:30:00+09:00", prices_db=self.db)
        inputs.update(kwargs)
        return pretrade_cli.freeze_plan(**inputs)

    def test_eok_conversion_codes_and_read_only_prices(self):
        before = self.db.read_bytes()
        with patch.object(pretrade_cli.sqlite3, "connect", wraps=sqlite3.connect) as connect:
            result = self.freeze()
        self.assertTrue(connect.call_args.args[0].endswith("?mode=ro"))
        self.assertTrue(connect.call_args.kwargs["uri"])
        self.assertEqual(before, self.db.read_bytes())
        self.assertEqual(result["rows"][0]["code"], "00AB10")
        self.assertEqual(result["rows"][0]["required_shares"], 10_000)
        self.assertEqual(result["rows"][1]["required_shares"], -1_000)
        self.assertEqual(len(result["rows"]), 2)
        self.assertEqual(result["scope"], "remaining_at_window_start")
        self.assertTrue(result["assumption_no_overtrade"])
        self.assertFalse(result["assumption_cash_completion_at_close"])
        self.assertEqual(result["trade_end"], "2026-09-11T15:20:00+09:00")

    def test_gross_offsets_preserved_even_when_net_zero(self):
        result = self.freeze()
        self.assertIn("offsetting_etf_flows", result["warnings"])
        netzero = result["provenance"]["rows"][2]
        self.assertEqual(netzero["gross_etf_buy_eok"], 4)
        self.assertEqual(netzero["gross_etf_sell_eok"], -4)
        self.assertEqual(netzero["flow_total_eok"], 0)
        self.assertEqual(result["provenance"]["source_pdf_date"], "2026-09-10")

    def test_missing_nonzero_price_fails_with_code(self):
        self.rows[2]["flow_total"] = 1
        self.save("flows.json", {"data_through": "20260910", "events": [self.event]})
        with self.assertRaisesRegex(ValueError, "NO_PRICE"):
            self.freeze()

    def test_archive_uses_default_or_explicit_scenario_and_saved_time(self):
        archive = {"key": "example", "trade_date": "2026-09-11", "settle_date": "2026-09-11",
                   "snapshot": {"generated": "2026-09-11 08:00", "saved": "2026-09-11 08:30",
                                "data_through": "2026-09-10"}, "default_scenario": "base",
                   "scenarios": {"base": {"rows": self.rows}, "alternative": {"rows": [self.rows[1]]}}}
        path = self.save("archive.json", archive)
        self.assertEqual(len(self.freeze(source_path=path)["rows"]), 2)
        result = self.freeze(source_path=path, scenario="alternative")
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["provenance"]["scenario"], "alternative")
        with self.assertRaisesRegex(ValueError, "snapshot_saved"):
            self.freeze(source_path=path, available_at="2026-09-11T08:15:00+09:00")

    def test_semi_source_identity_and_scenario(self):
        source = {"index": "KRX 반도체", "data_through": "20260910",
                  "window": {"trade_date": "2026-09-11", "trade_end": "2026-09-11"},
                  "scenarios": {"current": {"rows": self.rows}, "other": {"rows": [self.rows[0]]}}}
        path = self.save("semi.json", source)
        self.assertEqual(self.freeze(source_path=path, event_id="krx_semi")["provenance"]["scenario"], "current")
        self.assertEqual(len(self.freeze(source_path=path, event_id="krx_semi", scenario="other")["rows"]), 1)
        with self.assertRaisesRegex(ValueError, "not 'example'"):
            self.freeze(source_path=path)

    def test_no_source_backdating_or_naive_user_timestamps(self):
        with self.assertRaisesRegex(ValueError, "backdating"):
            self.freeze(available_at="2026-09-11T08:29:00+09:00")
        with self.assertRaisesRegex(ValueError, "timezone"):
            self.freeze(window_start="2026-09-11T09:00:00")
        with self.assertRaisesRegex(ValueError, "closing time"):
            self.freeze(price_date="20260911")

    def test_ad_hoc_late_target_cannot_use_earlier_window(self):
        with self.assertRaisesRegex(ValueError, "not known at window_start"):
            self.freeze(event_kind="ad_hoc", available_at="2026-09-11T11:00:00+09:00")

    def test_explicit_prior_target_time_preserves_later_file_availability(self):
        from pretrade_estimator import estimate_pretrade
        self.save("flows.json", {"generated": "2026-09-11 10:48", "data_through": "20260910", "events": [self.event]})
        plan = self.freeze(available_at="2026-09-11T11:00:00+09:00", target_known_at="2026-09-11T08:50:00+09:00")
        self.assertEqual(plan["available_at"], "2026-09-11T11:00:00+09:00")
        self.assertEqual(plan["target_known_at"], "2026-09-11T08:50:00+09:00")
        self.assertEqual(plan["provenance"]["target_known_at_basis"], "user_attestation")
        self.assertFalse(plan["provenance"]["target_known_at_independently_verified"])
        self.assertIn("target_known_at_is_user_attested_not_independently_verified", plan["warnings"])
        self.assertEqual(estimate_pretrade(plan, as_of="2026-09-11T11:00:00+09:00")["status"], "no_observation")
        with self.assertRaisesRegex(ValueError, "unavailable"):
            estimate_pretrade(plan, as_of="2026-09-11T10:00:00+09:00")

    def test_target_known_time_requires_timezone_and_cannot_follow_availability(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            self.freeze(target_known_at="2026-09-11T08:00:00")
        with self.assertRaisesRegex(ValueError, "later than plan"):
            self.freeze(target_known_at="2026-09-11T08:40:00+09:00")

    def test_missing_price_date_requires_explicit_date(self):
        self.save("flows.json", {"events": [self.event]})
        with self.assertRaisesRegex(ValueError, "price-date"):
            self.freeze()
        self.assertEqual(self.freeze(price_date="2026-09-10")["provenance"]["price_date"], "20260910")
        self.assertIn("source_timestamp_missing_availability_not_verified", self.freeze(price_date="20260910")["warnings"])

    def test_frozen_plan_is_exclusive_and_input_is_preserved(self):
        target = self.root / "plan.json"
        plan = self.freeze()
        pretrade_cli._write(target, plan, immutable=True)
        before = target.read_bytes()
        with self.assertRaises(FileExistsError):
            pretrade_cli._write(target, {"changed": True}, immutable=True)
        self.assertEqual(target.read_bytes(), before)
        with self.assertRaisesRegex(ValueError, "input file"):
            pretrade_cli._write(target, {}, inputs=(target,))
        with self.assertRaises(ValueError):
            pretrade_cli._write(target, {"bad": float("nan")})
        self.assertEqual(target.read_bytes(), before)

    def test_estimate_passes_cumulative_snapshots_without_summing_and_can_recompute(self):
        plan = self.save("plan.json", self.freeze())
        snapshots = [{"kind": "cumulative", "as_of": "2026-09-11T09:30:00+09:00", "rows": []},
                     {"kind": "cumulative", "as_of": "2026-09-11T10:00:00+09:00", "rows": []}]
        obs = self.save("observations.json", snapshots)
        output = self.save("result.json", {"old": True})
        estimate = Mock(return_value={"status": "conditional", "actual_etf_executed_shares": None})
        module = types.SimpleNamespace(estimate_pretrade=estimate)
        with patch.dict("sys.modules", {"pretrade_estimator": module}), contextlib.redirect_stdout(io.StringIO()):
            pretrade_cli.main(["estimate", "--plan", str(plan), "--observations", str(obs),
                               "--as-of", "2026-09-11T10:00:00+09:00", "--out", str(output)])
        self.assertEqual(estimate.call_args.args[1], snapshots)
        self.assertEqual(json.loads(output.read_text())["status"], "conditional")
        self.assertEqual(json.loads(obs.read_text()), snapshots)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_cli_freeze_and_refusal_to_replace_input(self):
        target = self.root / "plan.json"
        argv = ["freeze", "--source", str(self.source), "--event", "example", "--prices-db", str(self.db),
                "--window-start", "2026-09-11T09:00:00+09:00", "--available-at", "2026-09-11T08:30:00+09:00",
                "--out", str(target)]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(pretrade_cli.main(argv), 0)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as failure:
            pretrade_cli.main(argv[:-1] + [str(self.source)])
        self.assertEqual(failure.exception.code, 2)

    def test_frozen_plan_runs_real_core_without_observations(self):
        from pretrade_estimator import estimate_pretrade
        result = estimate_pretrade(self.freeze(), as_of="2026-09-11T10:00:00+09:00")
        self.assertEqual(result["status"], "no_observation")
        self.assertIsNone(result["actual_etf_pretrade_shares"])

    def test_final_auction_snapshot_is_rejected(self):
        from pretrade_estimator import estimate_pretrade
        plan = self.freeze()
        observation = {"event_id": "example", "window_start": plan["window_start"],
                       "as_of": "2026-09-11T15:30:00+09:00", "available_at": "2026-09-11T15:30:00+09:00",
                       "kind": "cumulative", "rows": []}
        with self.assertRaisesRegex(ValueError, "trading window"):
            estimate_pretrade(plan, observation, as_of="2026-09-11T15:30:00+09:00")

    def test_multiday_source_preserves_first_and_last_trade_dates(self):
        from pretrade_estimator import estimate_pretrade
        self.event["trade_end"] = "2026-09-14"
        self.save("flows.json", {"data_through": "20260910", "events": [self.event]})
        plan = self.freeze()
        self.assertEqual(plan["trade_date"], "2026-09-11")
        self.assertEqual(plan["trade_end"], "2026-09-14T15:20:00+09:00")
        self.assertEqual(plan["provenance"]["source_trade_end"], "2026-09-14")
        self.assertEqual(estimate_pretrade(plan, as_of="2026-09-11T10:00:00+09:00")["status"], "no_observation")

    def test_cli_real_core_uses_latest_cumulative_snapshot(self):
        from pretrade_estimator import estimate_pretrade
        plan = self.freeze()
        plan["rows"].append({"code": "THIRD", "name": "Synthetic third", "required_shares": 500,
                             "reference_price": 10_000})
        def observation(hour, alpha):
            stamp = f"2026-09-11T{hour}:00:00+09:00"
            return {"event_id": "example", "window_start": plan["window_start"], "as_of": stamp,
                    "available_at": stamp, "kind": "cumulative",
                    "rows": [{"code": row["code"], "net_buy_shares": alpha * row["required_shares"],
                              "background_shares": 0, "error_bound_shares": abs(row["required_shares"]) * .05}
                             for row in plan["rows"]]}
        latest = observation("11", .4)
        snapshots = [latest, observation("10", .2)]
        path = self.save("plan.json", plan)
        obs = self.save("observations.json", snapshots)
        output = self.root / "result.json"
        with contextlib.redirect_stdout(io.StringIO()):
            pretrade_cli.main(["estimate", "--plan", str(path), "--observations", str(obs),
                               "--as-of", "2026-09-11T11:00:00+09:00", "--out", str(output)])
        result = json.loads(output.read_text(encoding="utf-8"))
        self.assertAlmostEqual(result["basket_aligned_fraction"], .4)
        self.assertEqual(result, estimate_pretrade(plan, latest, as_of="2026-09-11T11:00:00+09:00"))


if __name__ == "__main__":
    unittest.main()
