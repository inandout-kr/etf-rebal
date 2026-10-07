import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import requests

import build_site
from build_site import compare_snapshots, daily_snapshot, prepare_changes
from fetch_holdings import PROXY, collect_holdings, validate_holdings
from fetch_market import store_daily_rows, validate_universe
from validate_data import DataQualityError, finite_tree, validate_artifacts, validate_directory, validate_dist, validate_flow_rows

NOW = datetime(2026, 9, 11, 8, 30)


def holding(code="069500", day="2026-09-10"):
    return {"etf_code": code, "etf_name": "ETF", "date": day,
            "rows": [{"code": "005930", "name": "삼성전자", "count": 1, "weight": 100, "date": day}]}


def flow_rows():
    return [
        {"code": "005930", "name": "삼성전자", "w_cur": 50, "w_target": 60, "delta": 10,
         "flow_by_etf": {"069500": 10}, "flow_total": 10},
        {"code": "000660", "name": "SK하이닉스", "w_cur": 50, "w_target": 40, "delta": -10,
         "flow_by_etf": {"069500": -10}, "flow_total": -10},
    ]


def artifacts():
    analysis = {"last_daily": "20260910", "generated": "2026-09-11 08:00", "data_quality": {"warnings": []}}
    for key, n in (("kospi200", 200), ("kosdaq150", 150)):
        analysis[key] = {"n_selected": n, "n_current": n, "adds": [], "dels": [],
                         "all": [{"code": str(i)} for i in range(n)], "data_through": "20260910",
                         "watch_keep": [], "watch_new": []}
    return {"analysis": analysis, "holdings": {k: holding(code) for k, code in PROXY.items()},
            "etf_master": [{"code": "069500"}], "methodology": {"x": "method"}, "backtest_june": {"x": "backtest"},
            "flows": {"generated": analysis["generated"], "data_through": analysis["last_daily"], "events": [{"key": "test", "n_target": 2,
                       "rows": flow_rows(), "total_buy": 10, "total_sell": -10}],
                      "by_stock": {"005930": {"name": "삼성전자", "events": [{"flow": 10}], "total": 10}, "000660": {"name": "SK하이닉스", "events": [{"flow": -10}], "total": -10}}},
            "krx_semi_rebal": {"data_through": analysis["last_daily"], "expiry": "2026-09-01", "scenarios": {"current": {"rows": flow_rows(), "n_selected": 2,
                                 "total_buy_eok": 10, "total_sell_eok": -10}}}}


def database(path):
    con = sqlite3.connect(path)
    con.executescript("""
      CREATE TABLE daily(code TEXT, date TEXT, close REAL, trdval REAL, volume REAL, shares REAL, PRIMARY KEY(code,date));
      CREATE TABLE stock(code TEXT PRIMARY KEY, is_common INT);
      CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
    """)
    con.execute("INSERT INTO meta VALUES('current_universe',?)", (json.dumps({"version": 1, "codes": ["005930"],
                 "observed_at": "2026-09-11 08:10", "source": "Naver marketValue KOSPI/KOSDAQ"}),))
    return con


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.con = database(":memory:")
        self.con.executemany("INSERT INTO daily VALUES(?,?,?,?,?,?)", [
            ("005930", "20260910", 100, 1000, 10, 10000),
            ("000660", "20260910", 200, 2000, 10, 10000)])
        self.con.commit()

    def tearDown(self):
        self.con.close()

    def test_partial_source_failure_keeps_previous_history(self):
        self.assertTrue(store_daily_rows(self.con, "005930", [("005930", "20260910", 110, 1100, 10, 10000)], "20260901", "20260910"))
        self.assertFalse(store_daily_rows(self.con, "000660", None, "20260901", "20260910"))
        self.assertEqual(self.con.execute("SELECT close FROM daily WHERE code='000660'").fetchone()[0], 200)
        self.assertEqual(self.con.execute("SELECT count(*) FROM daily").fetchone()[0], 2)

    def test_malformed_response_is_rejected_before_any_upsert(self):
        with self.assertRaises(ValueError):
            store_daily_rows(self.con, "005930", [("005930", "20260910", 110, 1100, 10, 10000),
                             ("005930", "20260909", 100, float("nan"), 10, 10000)], "20260901", "20260910")
        self.assertEqual(self.con.execute("SELECT close FROM daily WHERE code='005930'").fetchone()[0], 100)

    def test_unfinished_session_filtered_but_suspension_zero_retained(self):
        store_daily_rows(self.con, "005930", [("005930", "20260910", 100, 0, 0, 10000),
                         ("005930", "20260911", 120, 900, 10, 10000)], "20260901", "20260910")
        self.assertEqual(self.con.execute("SELECT trdval FROM daily WHERE code='005930'").fetchall(), [(0,)])

    def test_empty_market_response_cannot_replace_universe(self):
        with self.assertRaises(ValueError):
            validate_universe([], 2500)

    def test_failed_and_empty_holdings_preserve_prior_entries(self):
        previous = {"good": holding("111111"), "timeout": holding("222222"), "empty": holding("333333")}
        def fetcher(session, code):
            if code == "222222":
                raise requests.Timeout("timeout")
            if code == "333333":
                return "ETF", []
            return "ETF", [{"name": "삼성전자", "count": 2, "weight": 100, "date": "2026-09-11"}]
        with redirect_stdout(io.StringIO()):
            out, meta = collect_holdings(None, {"삼성전자": [("005930", "KOSPI")]}, previous,
                                         {key: value["etf_code"] for key, value in previous.items()}, fetcher)
        self.assertEqual(out["timeout"], previous["timeout"])
        self.assertEqual(out["empty"], previous["empty"])
        self.assertEqual(out["good"]["rows"][0]["count"], 2)
        self.assertEqual(meta["warning_count"], 2)

    def test_regressed_holdings_and_nonfinite_weights_rejected(self):
        with self.assertRaises(ValueError):
            validate_holdings(holding(day="2026-09-09"), holding())
        entry = holding()
        entry["rows"][0]["weight"] = float("inf")
        with self.assertRaises(ValueError):
            validate_holdings(entry)


class QualityTests(unittest.TestCase):
    def test_valid_artifacts_pass_and_unfinished_date_fails(self):
        data = artifacts()
        self.assertEqual(validate_artifacts(data, NOW), [])
        data["analysis"]["last_daily"] = "20260911"
        with self.assertRaisesRegex(DataQualityError, "미완료"):
            validate_artifacts(data, NOW)

    def test_missing_artifact_and_nonfinite_data_fail(self):
        data = artifacts()
        del data["flows"]
        with self.assertRaises(DataQualityError):
            validate_artifacts(data, NOW)
        with self.assertRaises(DataQualityError):
            finite_tree({"rows": [{"weight": float("nan")}]})

    def test_explicit_current_exclusions_are_accounted_for(self):
        data = artifacts()
        data["analysis"]["kospi200"]["n_current"] = 201
        data["analysis"]["kospi200"]["excluded_current"] = [{"code": "123450", "why": "산업군 미분류"}]
        self.assertTrue(any("심사 대상 제외" in warning for warning in validate_artifacts(data, NOW)))

    def test_weights_counts_and_cashflows_are_checked(self):
        validate_flow_rows(flow_rows(), "valid", 2, 10, -10)
        for mutate in (
            lambda rows: rows[0].update(w_target=55),
            lambda rows: rows[0].update(flow_total=100),
            lambda rows: rows[0].update(delta=11),
        ):
            rows = flow_rows()
            mutate(rows)
            with self.assertRaises(DataQualityError):
                validate_flow_rows(rows, "bad", 2, 10, -10)
        with self.assertRaises(DataQualityError):
            validate_flow_rows(flow_rows(), "bad count", 3)

    def test_published_rounding_is_allowed(self):
        rows = flow_rows()
        rows[0].update(w_target=60.001, delta=10.001, flow_by_etf={"a": 5.0, "b": 5.1}, flow_total=10.0)
        validate_flow_rows(rows, "rounded", 2)

    def test_stale_semiconductor_artifact_blocks_build(self):
        data = artifacts()
        data["krx_semi_rebal"]["data_through"] = "20260102"
        with self.assertRaisesRegex(DataQualityError, "반도체 기준일"):
            validate_artifacts(data, NOW)

    def test_pending_review_is_explicit_and_only_permitted_for_selection_scenarios(self):
        data = artifacts()
        semi = data["krx_semi_rebal"]
        semi["availability"] = "pending_review"
        semi["scenarios"]["cap10"] = {"unavailable": True, "rows": [], "n_selected": None, "reason": "심사기간 시작 전"}
        self.assertTrue(any("심사기간 시작 전" in warning for warning in validate_artifacts(data, NOW)))
        semi["availability"] = "ready"
        with self.assertRaises(DataQualityError):
            validate_artifacts(data, NOW)

    def test_missing_aggregate_stock_is_not_silently_accepted(self):
        data = artifacts()
        del data["flows"]["by_stock"]["000660"]
        with self.assertRaisesRegex(DataQualityError, "종목 누락"):
            validate_artifacts(data, NOW)

    def test_cached_source_warns_and_low_market_coverage_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            con = database(path / "market.sqlite")
            con.execute("INSERT INTO stock VALUES('005930',1)")
            con.execute("INSERT INTO daily VALUES('005930','20260910',100,1000,10,10000)")
            con.execute("INSERT INTO meta VALUES('market_collection',?)", (json.dumps({"failed_codes": ["000660"]}),))
            con.commit()
            quality = validate_directory(path, NOW, artifacts())
            self.assertEqual(quality["status"], "warning")
            self.assertTrue(any("마지막 정상 자료" in warning for warning in quality["warnings"]))
            con.execute("INSERT INTO stock VALUES('000660',1)")
            con.execute("UPDATE meta SET v=? WHERE k='current_universe'", (json.dumps({"version": 1, "codes": ["005930", "000660"],
                         "observed_at": "2026-09-11 08:10", "source": "Naver marketValue KOSPI/KOSDAQ"}),))
            con.commit()
            with self.assertRaisesRegex(DataQualityError, "수집 범위 부족"):
                validate_directory(path, NOW, artifacts())
            con.close()

    def test_invalid_build_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "index.html").write_text("previous version", encoding="utf-8")
            with patch.object(build_site, "DATA", root), patch.object(build_site, "DIST", root), \
                 patch.object(build_site, "read_artifacts", return_value=artifacts()), \
                 patch.object(build_site, "validate_directory", side_effect=DataQualityError("bad source")):
                with self.assertRaises(DataQualityError):
                    build_site.main()
            self.assertEqual((root / "index.html").read_text(encoding="utf-8"), "previous version")
            self.assertFalse((root / "daily_snapshots").exists())

    def test_build_matches_payload_and_changed_source_blocks_deploy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_dir, dist_dir = root / "data", root / "dist"
            data_dir.mkdir()
            for name, value in artifacts().items():
                (data_dir / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")
            con = database(data_dir / "market.sqlite")
            con.execute("INSERT INTO stock VALUES('005930',1)")
            con.execute("INSERT INTO daily VALUES('005930','20260910',100,1000,10,10000)")
            con.commit()
            con.close()
            (root / "template.html").write_text("<script>const D=/*__DATA__*/null;</script>", encoding="utf-8")
            fixed_validation = lambda directory, artifacts: validate_directory(directory, NOW, artifacts)
            with patch.object(build_site, "ROOT", root), patch.object(build_site, "DATA", data_dir), \
                 patch.object(build_site, "DIST", dist_dir), patch.object(build_site, "validate_directory", side_effect=fixed_validation), \
                 redirect_stdout(io.StringIO()):
                build_site.main()
            validate_dist(data_dir, dist_dir, NOW)
            payload = json.loads((dist_dir / "data.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["changes"]["status"], "baseline_created")
            self.assertTrue((data_dir / "daily_snapshots" / "2026-09-10.json").exists())
            (data_dir / "methodology.json").write_text(json.dumps({"x": "changed"}), encoding="utf-8")
            with self.assertRaisesRegex(DataQualityError, "빌드 후 원본 변경"):
                validate_dist(data_dir, dist_dir, NOW)


class DailyChangeTests(unittest.TestCase):
    def snapshot(self, day="20260910", code="005930", total=10):
        return daily_snapshot({"last_daily": day, "generated": "2026-09-11 08:00", "kospi200": {
            "name": "KOSPI 200", "adds": [{"code": code, "name": code}]}},
            {"by_stock": {code: {"name": code, "total": total}}})

    def test_first_day_has_no_fabricated_changes(self):
        result = compare_snapshots(self.snapshot())
        self.assertEqual(result, {"status": "baseline_created", "baseline_date": None,
                                 "current_date": "2026-09-10", "adds": [], "flows": []})

    def test_next_day_new_removed_candidates_and_signed_flow_deltas(self):
        result = compare_snapshots(self.snapshot("20260911", "000660", -20), self.snapshot())
        self.assertEqual(result["status"], "ready")
        self.assertEqual({(r["code"], r["change"]) for r in result["adds"]}, {("005930", "removed"), ("000660", "new")})
        self.assertEqual([(r["code"], r["delta"]) for r in result["flows"]], [("000660", -20), ("005930", -10)])

    def test_repeat_build_compares_latest_prior_distinct_date(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            for day in ("20260909", "20260910", "20260911", "20260914"):
                snap = self.snapshot(day)
                (path / f"{snap['date']}.json").write_text(json.dumps(snap), encoding="utf-8")
            result, _ = prepare_changes({"last_daily": "20260911", "generated": "today"}, {"by_stock": {}}, path)
            self.assertEqual(result["baseline_date"], "2026-09-10")


if __name__ == "__main__":
    unittest.main()
