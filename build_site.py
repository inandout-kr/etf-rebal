# -*- coding: utf-8 -*-
"""정적 사이트 빌드: data/analysis.json + etf_master.json + methodology.json → dist/index.html (데이터 내장)"""
import json
import hashlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from validate_data import parse_date, read_artifacts, validate_directory

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).parent
DATA = ROOT / "data"
DIST = ROOT / "dist"


def slim(rows, keys):
    return [{k: r.get(k) for k in keys if r.get(k) is not None} for r in rows]


def daily_snapshot(analysis, flows):
    candidates = []
    for key, result in analysis.items():
        if not isinstance(result, dict) or "adds" not in result:
            continue
        for row in result["adds"]:
            if isinstance(row, dict) and row.get("code"):
                candidates.append({"index": result.get("name", key), "key": key,
                                   "code": row["code"], "name": row["name"]})
    scenario_candidates = {}
    kq = analysis.get("kosdaq150", {})
    for policy, value in kq.get("smallcap_scenarios", {}).items():
        scenario_candidates[f"kosdaq150:GICS:{policy}"] = {
            "small_cap_policy": policy, "classification": "GICS",
            "adds": [{"code": r["code"], "name": r["name"]} for r in value["adds"]],
            "dels": [{"code": r["code"], "name": r["name"]} for r in value["dels"]]}
    comparison = kq.get("classification_scenarios", {})
    if comparison.get("status") == "ready":
        for policy, scenarios in comparison.get("smallcap_scenarios", {}).items():
            for interpretation, value in scenarios.items():
                scenario_candidates[f"kosdaq150:{interpretation}:{policy}"] = {
                    "small_cap_policy": policy, "classification": "KRICS", "interpretation": interpretation,
                    "adds": [{"code": r["code"], "name": r["name"]} for r in value["adds"]],
                    "dels": [{"code": r["code"], "name": r["name"]} for r in value["dels"]]}
    scenario_flows = {}
    for event in (flows or {}).get("events", []):
        for scenario, value in event.get("scenario_flows", {}).items():
            scenario_flows[f"{event['key']}:{scenario}"] = {
                "availability": value["availability"], "total_buy": value["total_buy"],
                "total_sell": value["total_sell"], "hold_reasons": value.get("hold_reasons", [])}
    return {"date": parse_date(analysis["last_daily"]).isoformat(), "generated": analysis["generated"],
            "scenario_candidates": scenario_candidates, "scenario_flows": scenario_flows,
            "candidates": candidates,
            "flows": {code: {"name": row["name"], "total": row["total"],
                             "complete": row.get("complete"), "held_count": row.get("held_count"),
                             "known_total": row.get("known_total"),
                             "known_coverage": sorted([e.get("key") or e.get("index") or "", e.get("apply") or ""]
                                                      for e in row.get("events", [])),
                             "held_coverage": sorted([e.get("key") or e.get("index") or "", e.get("apply") or ""]
                                                     for e in row.get("held_events", [])),
                             "coverage_known": "complete" in row and "held_count" in row
                                and all((e.get("key") or e.get("index")) and e.get("apply")
                                        for e in row.get("events", []) + row.get("held_events", []))}
                      for code, row in (flows or {}).get("by_stock", {}).items()}}


def compare_snapshots(current, previous=None):
    result = {"status": "ready" if previous else "baseline_created",
              "baseline_date": previous["date"] if previous else None,
              "current_date": current["date"], "adds": [], "flows": [], "flow_comparison_holds": [],
              "scenario_changes": [], "scenario_status": "ready" if previous and "scenario_candidates" in previous else "baseline_created"}
    if previous is None:
        return result
    for scenario in sorted(current.get("scenario_candidates", {}).keys() & previous.get("scenario_candidates", {}).keys()):
        before_scenario = previous["scenario_candidates"][scenario]
        after_scenario = current["scenario_candidates"][scenario]
        for direction in ("adds", "dels"):
            before_codes = {r["code"] for r in before_scenario[direction]}
            after_codes = {r["code"] for r in after_scenario[direction]}
            if before_codes != after_codes:
                result["scenario_changes"].append({"scenario_id": scenario, "direction": direction,
                    "new": sorted(after_codes - before_codes), "removed": sorted(before_codes - after_codes)})
    before = {(r["key"], r["code"]): r for r in previous["candidates"]}
    after = {(r["key"], r["code"]): r for r in current["candidates"]}
    for keys, source, change in ((after.keys() - before.keys(), after, "new"),
                                 (before.keys() - after.keys(), before, "removed")):
        for key in sorted(keys):
            result["adds"].append({k: source[key][k] for k in ("index", "code", "name")} | {"change": change})
    for code in sorted(previous["flows"].keys() | current["flows"].keys()):
        prior = previous["flows"].get(code)
        latest = current["flows"].get(code)
        hold = None
        if prior is None or latest is None:
            hold = "한쪽 관측 없음 (0원으로 대체하지 않음)"
        elif prior.get("total") is None or latest.get("total") is None:
            hold = "수급 HOLD · 숫자 변화 비교 보류"
        elif not prior.get("coverage_known") or not latest.get("coverage_known"):
            hold = "이전 스냅샷의 계산 범위 확인 불가"
        elif any(prior.get(k) != latest.get(k) for k in ("known_coverage", "held_coverage", "complete")):
            hold = "계산 가능한 이벤트·HOLD 범위가 달라 비교 보류"
        if hold:
            result["flow_comparison_holds"].append({"code": code, "name": (latest or prior)["name"], "reason": hold})
            continue
        before_value, after_value = prior["total"], latest["total"]
        delta = round(after_value - before_value, 1)
        if delta:
            result["flows"].append({"code": code, "name": latest.get("name", prior.get("name")),
                                    "before": before_value, "after": after_value, "delta": delta,
                                    "partial": latest.get("complete") is False})
    result["flows"].sort(key=lambda r: (-abs(r["delta"]), r["code"]))
    return result


def prepare_changes(analysis, flows, directory):
    snapshot = daily_snapshot(analysis, flows)
    prior_paths = sorted(p for p in directory.glob("????-??-??.json") if p.stem < snapshot["date"])
    if prior_paths:
        day = prior_paths[-1].stem
        revisions = list((directory / "revisions").glob(f"{day}_*.json"))
        saved = [(json.loads(p.read_text(encoding="utf-8")), p.stat().st_mtime_ns) for p in [prior_paths[-1], *revisions]]
        previous = max(saved, key=lambda item: (item[0].get("generated", ""), item[1]))[0]
    else:
        previous = None
    return compare_snapshots(snapshot, previous), snapshot


def atomic_text(path, text):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)


def save_daily_snapshot(directory, snapshot):
    """Preserve a frozen day's first observation; new calculations get content keys."""
    directory.mkdir(parents=True, exist_ok=True)
    primary = directory / f"{snapshot['date']}.json"
    if not primary.exists():
        atomic_text(primary, json.dumps(snapshot, ensure_ascii=False, allow_nan=False))
        return primary
    def identity(value):
        return json.dumps({k: v for k, v in value.items() if k != "generated"},
                          ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    existing = json.loads(primary.read_text(encoding="utf-8"))
    content = identity(snapshot)
    if identity(existing) == content:
        return primary
    revision_dir = directory / "revisions"
    revision_dir.mkdir(exist_ok=True)
    revision = revision_dir / f"{snapshot['date']}_{hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]}.json"
    if not revision.exists():
        atomic_text(revision, json.dumps(snapshot, ensure_ascii=False, allow_nan=False))
    return revision


def main():
    artifacts = read_artifacts(DATA)
    quality = validate_directory(DATA, artifacts=artifacts)
    a, etfs, kb, hold, semi, flows, backtest = (artifacts[k] for k in
        ("analysis", "etf_master", "methodology", "holdings", "krx_semi_rebal", "flows", "backtest_june"))
    a["data_quality"] = quality
    changes, snapshot = prepare_changes(a, flows, DATA / "daily_snapshots")
    today = datetime.now(timezone(timedelta(hours=9))).date().isoformat()
    if semi and semi["expiry"] < today:   # 매매 끝난 KRX 반도체 정기변경 → 히스토리 탭으로
        semi = None
    if flows:
        flows["events"] = [e for e in flows["events"] if not e.get("passed")]
    history = []
    for p in sorted((DATA / "history").glob("*.json"), reverse=True):
        h = json.loads(p.read_text(encoding="utf-8"))
        h.pop("pdf_rows", None)
        history.append(h)
    history.sort(key=lambda h: h["trade_date"], reverse=True)
    keys = ["code", "name", "sector", "sector_src", "avg_mcap", "avg_trdval", "n_days", "mcap_rank", "n_exist", "n_sector",
            "rank_ratio", "cum_share", "trd_rank", "liq_ok", "primary", "status", "mktcap_now", "listing_date", "mcap15", "note",
            "is_cur", "cur_weight", "fif", "confidence", "conf_note", "ret_period", "risk", "price_as_of", "price_status", "universe_as_of",
            "market_mcap_rank", "discretionary_action", "review_start", "review_end", "review_events", "mcap15_rank", "mcap15_n_days"]
    for k in ("kospi200", "kosdaq150"):
        a[k]["all"] = slim(a[k]["all"], keys)
        for f in ("adds", "dels", "watch_keep", "watch_new"):
            a[k][f] = slim(a[k][f], keys)
    # 프록시 ETF 구성종목(검색용): 지수키 → [code]
    members = {k: [r["code"] for r in v["rows"] if r.get("code")] for k, v in hold.items()}
    if semi:
        keep = ["code", "name", "market", "status", "is_cur", "in_midlarge", "gics_excluded", "avg_mcap", "avg_mcap_2025", "avg_trdval", "mcap_rank", "cum_share",
                "trd_rank", "liq_ok", "mktcap_now", "fif", "fif_src", "w_cur_adj", "w_target", "delta", "capped", "flow_by_etf", "flow_total", "adv20", "adv_mult", "w_cur_etf"]
        keep += ["availability", "hold_reasons", "official_ffr", "cap_factor", "relative_coefficient", "relative_coefficient_source"]
        for sc in semi["scenarios"].values():
            # Other tracked ETFs can hold a sale outside the proxy and new universe.
            # Preserve every engine row, including null-valued HOLD fields.
            sc["rows"] = [{k: r.get(k) for k in keep if k in r} for r in sc["rows"]]
    payload = {"analysis": a, "etfs": etfs, "kb": kb, "members": members, "semi": semi, "flows": flows, "backtest": backtest, "history": history, "changes": changes,
               "proxy": {k: {"etf": v["etf_code"], "date": v["date"], "n": len(v["rows"])} for k, v in hold.items()}}
    html = (ROOT / "template.html").read_text(encoding="utf-8")
    if html.count("/*__DATA__*/null") != 1:
        raise ValueError("사이트 템플릿 데이터 위치가 없거나 중복됨")
    source_paths = (list(DATA.glob("history/*.json")) + [DATA / f"{name}.json" for name in artifacts]
                    + list(DATA.glob("krics*.json")) + list(DATA.glob("official*.json")) + [ROOT / "template.html"])
    payload["_build"] = {"sources": {str(p.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
                                     for p in sorted(source_paths)}}
    js = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
    html = html.replace("/*__DATA__*/null", js)
    data_json = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    DIST.mkdir(exist_ok=True)
    atomic_text(DIST / "data.json", data_json)
    atomic_text(DIST / "index.html", html)
    atomic_text(DIST / ".nojekyll", "")
    save_daily_snapshot(DATA / "daily_snapshots", snapshot)
    print(f"built dist/index.html ({len(html)//1024} KB), data.json ({len(js)//1024} KB)")
    for warning in quality["warnings"]:
        print(f"  ! {warning}")


if __name__ == "__main__":
    main()
