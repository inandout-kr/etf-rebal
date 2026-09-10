# -*- coding: utf-8 -*-
"""정적 사이트 빌드: data/analysis.json + etf_master.json + methodology.json → dist/index.html (데이터 내장)"""
import json
import sys
from datetime import date
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).parent
DATA = ROOT / "data"
DIST = ROOT / "dist"


def slim(rows, keys):
    return [{k: r.get(k) for k in keys if r.get(k) is not None} for r in rows]


def main():
    a = json.loads((DATA / "analysis.json").read_text(encoding="utf-8"))
    etfs = json.loads((DATA / "etf_master.json").read_text(encoding="utf-8"))
    kb = json.loads((DATA / "methodology.json").read_text(encoding="utf-8"))
    hold = json.loads((DATA / "holdings.json").read_text(encoding="utf-8"))
    semi = json.loads((DATA / "krx_semi_rebal.json").read_text(encoding="utf-8")) if (DATA / "krx_semi_rebal.json").exists() else None
    flows = json.loads((DATA / "flows.json").read_text(encoding="utf-8")) if (DATA / "flows.json").exists() else None
    backtest = json.loads((DATA / "backtest_june.json").read_text(encoding="utf-8")) if (DATA / "backtest_june.json").exists() else None
    today = date.today().isoformat()
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
            "is_cur", "cur_weight", "fif", "confidence", "conf_note", "ret_period", "risk"]
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
    payload = {"analysis": a, "etfs": etfs, "kb": kb, "members": members, "semi": semi, "flows": flows, "backtest": backtest, "history": history,
               "proxy": {k: {"etf": v["etf_code"], "date": v["date"], "n": len(v["rows"])} for k, v in hold.items()}}
    html = (ROOT / "template.html").read_text(encoding="utf-8")
    js = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = html.replace("/*__DATA__*/null", js)
    DIST.mkdir(exist_ok=True)
    (DIST / "index.html").write_text(html, encoding="utf-8")
    (DIST / "data.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    (DIST / ".nojekyll").write_text("", encoding="utf-8")
    print(f"built dist/index.html ({len(html)//1024} KB), data.json ({len(js)//1024} KB)")


if __name__ == "__main__":
    main()
