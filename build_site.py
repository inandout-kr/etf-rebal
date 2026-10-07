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
    return {"date": parse_date(analysis["last_daily"]).isoformat(), "generated": analysis["generated"],
            "candidates": candidates,
            "flows": {code: {"name": row["name"], "total": row["total"]}
                      for code, row in (flows or {}).get("by_stock", {}).items()}}


def compare_snapshots(current, previous=None):
    result = {"status": "ready" if previous else "baseline_created",
              "baseline_date": previous["date"] if previous else None,
              "current_date": current["date"], "adds": [], "flows": []}
    if previous is None:
        return result
    before = {(r["key"], r["code"]): r for r in previous["candidates"]}
    after = {(r["key"], r["code"]): r for r in current["candidates"]}
    for keys, source, change in ((after.keys() - before.keys(), after, "new"),
                                 (before.keys() - after.keys(), before, "removed")):
        for key in sorted(keys):
            result["adds"].append({k: source[key][k] for k in ("index", "code", "name")} | {"change": change})
    for code in sorted(previous["flows"].keys() | current["flows"].keys()):
        prior = previous["flows"].get(code, {})
        latest = current["flows"].get(code, {})
        before_value, after_value = prior.get("total", 0), latest.get("total", 0)
        delta = round(after_value - before_value, 1)
        if delta:
            result["flows"].append({"code": code, "name": latest.get("name", prior.get("name")),
                                    "before": before_value, "after": after_value, "delta": delta})
    result["flows"].sort(key=lambda r: (-abs(r["delta"]), r["code"]))
    return result


def prepare_changes(analysis, flows, directory):
    snapshot = daily_snapshot(analysis, flows)
    prior_paths = sorted(p for p in directory.glob("????-??-??.json") if p.stem < snapshot["date"])
    previous = json.loads(prior_paths[-1].read_text(encoding="utf-8")) if prior_paths else None
    return compare_snapshots(snapshot, previous), snapshot


def atomic_text(path, text):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)


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
            "is_cur", "cur_weight", "fif", "confidence", "conf_note", "ret_period", "risk", "price_as_of", "price_status", "universe_as_of"]
    for k in ("kospi200", "kosdaq150"):
        a[k]["all"] = slim(a[k]["all"], keys)
        for f in ("adds", "dels", "watch_keep", "watch_new"):
            a[k][f] = slim(a[k][f], keys)
    # 프록시 ETF 구성종목(검색용): 지수키 → [code]
    members = {k: [r["code"] for r in v["rows"] if r.get("code")] for k, v in hold.items()}
    if semi:
        keep = ["code", "name", "market", "status", "is_cur", "in_midlarge", "gics_excluded", "avg_mcap", "avg_mcap_2025", "avg_trdval", "mcap_rank", "cum_share",
                "trd_rank", "liq_ok", "mktcap_now", "fif", "fif_src", "w_cur_adj", "w_target", "delta", "capped", "flow_by_etf", "flow_total", "adv20", "adv_mult", "w_cur_etf"]
        for sc in semi["scenarios"].values():
            sc["rows"] = [{k: r.get(k) for k in keep if r.get(k) is not None} for r in sc["rows"] if r.get("is_cur") or r.get("in_midlarge")]
    payload = {"analysis": a, "etfs": etfs, "kb": kb, "members": members, "semi": semi, "flows": flows, "backtest": backtest, "history": history, "changes": changes,
               "proxy": {k: {"etf": v["etf_code"], "date": v["date"], "n": len(v["rows"])} for k, v in hold.items()}}
    html = (ROOT / "template.html").read_text(encoding="utf-8")
    if html.count("/*__DATA__*/null") != 1:
        raise ValueError("사이트 템플릿 데이터 위치가 없거나 중복됨")
    source_paths = list(DATA.glob("history/*.json")) + [DATA / f"{name}.json" for name in artifacts] + [ROOT / "template.html"]
    payload["_build"] = {"sources": {str(p.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
                                     for p in sorted(source_paths)}}
    js = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
    html = html.replace("/*__DATA__*/null", js)
    data_json = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    DIST.mkdir(exist_ok=True)
    atomic_text(DIST / "data.json", data_json)
    atomic_text(DIST / "index.html", html)
    atomic_text(DIST / ".nojekyll", "")
    snapshots = DATA / "daily_snapshots"
    snapshots.mkdir(exist_ok=True)
    atomic_text(snapshots / f"{snapshot['date']}.json", json.dumps(snapshot, ensure_ascii=False, allow_nan=False))
    print(f"built dist/index.html ({len(html)//1024} KB), data.json ({len(js)//1024} KB)")
    for warning in quality["warnings"]:
        print(f"  ! {warning}")


if __name__ == "__main__":
    main()
