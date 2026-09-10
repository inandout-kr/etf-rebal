# -*- coding: utf-8 -*-
"""지난 정기변경 스냅샷 보관 + 실제 결과(ETF PDF) 대조

- 매매일(= 적용일 전 영업일)이 되면 그날 아침 계산 결과(직전 PDF = 매매 전 포트폴리오)를 data/history/<key>_<apply>.json 에 동결
  (이미 파일이 있으면 덮어쓰지 않음 → 이후 갱신에서 예측이 사라지지 않음)
- 매매 후 PDF(기준일 ≥ 마지막 매매일)가 들어오면 실제 비중변화·편출입을 계산해 같은 파일의 actual 에 기록
  (실제 매매액 = 총 추종명목 × 프록시 ETF PDF 비중변화, 직전 PDF는 새 PDF 기준일 종가로 드리프트 보정)
사용: python archive.py [--today YYYY-MM-DD]
출력: data/history/*.json  (build_site.py 가 읽어 히스토리 탭에 표시)
"""
import argparse
import json
import re
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

from calendar_events import prev_business_day

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).parent
DATA = ROOT / "data"
HIST = DATA / "history"
ROW_KEYS = ["code", "name", "status", "w_cur", "w_target", "delta", "flow_total", "adv_mult", "capped", "fixed", "flow_by_etf"]


def parse_apply(s):
    """'2026-09-14' / '2026-09-14~16 (3영업일 분할)' → (start, end) ISO"""
    m = re.match(r"(\d{4}-\d{2}-\d{2})(?:~(\d{2}))?", s)
    start = m.group(1)
    end = start[:8] + m.group(2) if m.group(2) else start
    return start, end


def event_dates(apply):
    start, end = parse_apply(apply)
    trade = prev_business_day(date.fromisoformat(start)).isoformat()
    settle = prev_business_day(date.fromisoformat(end)).isoformat()   # 마지막 매매일
    return start, trade, settle


def norm_rows(rows, cur_key):
    out = []
    for r in rows:
        d = {k: r.get(k) for k in ROW_KEYS if r.get(k) is not None}
        d["w_cur"] = r.get(cur_key) or 0.0
        if r.get("flow_total") or r.get("is_cur") or (r.get("status") or "").startswith(("신규편입", "편출")):
            out.append(d)
    return out


def from_semi(semi, holdings, generated):
    start, trade, settle = event_dates(semi["apply"])
    pdf = holdings["krx_semi"]
    etfs = semi["scenarios"]["current"]["etfs"]
    return {
        "key": "krx_semi", "name": "KRX 반도체", "apply": semi["apply"], "apply_start": start, "trade_date": trade, "settle_date": settle,
        "fix": f"{semi['expiry']} 종가", "rules": semi["rules"], "proxy_etf": "091160",
        "snapshot": {"generated": generated, "saved": datetime.now().strftime("%Y-%m-%d %H:%M"), "pdf_date": pdf["date"], "data_through": semi["data_through"],
                     "pre_trade": pdf["date"] < trade},
        "etfs": [{k: e.get(k) for k in ("code", "name", "aum_eok", "notional_eok")} for e in etfs], "notional_total": round(sum(e["notional_eok"] for e in etfs)),
        "pdf_rows": {r["code"]: r["weight"] for r in pdf["rows"] if r.get("code") and r.get("weight") is not None},
        "default_scenario": "current",
        "scenarios": {k: {"desc": semi["scenario_desc"].get(k, k), "adds": [r["name"] for r in sc["rows"] if r["status"].startswith("신규편입")],
                          "dels": [r["name"] for r in sc["rows"] if r["status"].startswith("편출")],
                          "total_buy": sc["total_buy_eok"], "total_sell": sc["total_sell_eok"], "rows": norm_rows(sc["rows"], "w_cur_adj")}
                      for k, sc in semi["scenarios"].items()},
        "actual": None,
    }


def from_flow(ev, holdings, generated):
    start, trade, settle = event_dates(ev["apply"])
    pdf = next(v for v in holdings.values() if v["etf_code"] == ev["proxy_etf"])
    return {
        "key": ev["key"], "name": ev["name"], "apply": ev["apply"], "apply_start": start, "trade_date": trade, "settle_date": settle,
        "fix": ev.get("fix"), "rules": f"목표 구성: {ev['target_src']} · {ev.get('note') or ''}", "proxy_etf": ev["proxy_etf"],
        "snapshot": {"generated": generated, "saved": datetime.now().strftime("%Y-%m-%d %H:%M"), "pdf_date": pdf["date"], "data_through": None,
                     "pre_trade": pdf["date"] < trade},
        "etfs": [{k: e.get(k) for k in ("code", "name", "aum_eok", "notional_eok")} for e in ev["etfs"]], "notional_total": ev["notional_total"],
        "pdf_rows": {r["code"]: r["weight"] for r in pdf["rows"] if r.get("code") and r.get("weight") is not None},
        "default_scenario": "base",
        "scenarios": {"base": {"desc": "공식 가중·캡 규칙 적용 예측", "adds": ev["adds"], "dels": ev["dels"], "total_buy": ev["total_buy"], "total_sell": ev["total_sell"],
                               "rows": norm_rows(ev["rows"], "w_cur")}},
        "actual": None,
    }


def compute_actual(arc, pdf_new, con, names):
    """매매 전 PDF(스냅샷 pdf_rows) vs 매매 후 PDF → 실제 비중변화·편출입, 시나리오별 적중"""
    old_raw = arc["pdf_rows"]
    new = {r["code"]: r["weight"] for r in pdf_new["rows"] if r.get("code") and r.get("weight") is not None}
    px = lambda dt: {r[0]: r[1] for r in con.execute("SELECT code, close FROM daily WHERE date=?", (dt.replace("-", ""),))}
    p0, p1 = px(arc["snapshot"]["pdf_date"]), px(pdf_new["date"])
    old = {c: w * (p1[c] / p0[c]) if p0.get(c) and p1.get(c) else w for c, w in old_raw.items()}
    s = sum(old.values())
    old = {c: w / s * 100 for c, w in old.items()} if s else old
    notional = arc["notional_total"]
    rows = {}
    for c in set(old) | set(new):
        d = new.get(c, 0.0) - old.get(c, 0.0)
        rows[c] = {"name": names.get(c, c), "w_old": round(old.get(c, 0.0), 3), "w_new": round(new.get(c, 0.0), 3), "delta": round(d, 3), "flow": round(notional * d / 100, 1)}
    adds, dels = [c for c in new if c not in old_raw], [c for c in old_raw if c not in new]
    by_sc = {}
    for k, sc in arc["scenarios"].items():
        pa = {r["code"] for r in sc["rows"] if r["status"].startswith("신규편입")}
        pd = {r["code"] for r in sc["rows"] if r["status"].startswith("편출")}
        nmz = lambda cs: sorted(names.get(c, c) for c in cs)
        pairs = [(r["delta"], rows[r["code"]]["delta"]) for r in sc["rows"] if r["code"] in rows and (abs(r["delta"]) >= 0.05 or abs(rows[r["code"]]["delta"]) >= 0.05)]
        dir_hit = sum(1 for a, b in pairs if (a > 0) == (b > 0)) / len(pairs) if pairs else None
        corr = None
        if len(pairs) >= 3:
            ma, mb = sum(a for a, _ in pairs) / len(pairs), sum(b for _, b in pairs) / len(pairs)
            va, vb = sum((a - ma) ** 2 for a, _ in pairs) ** .5, sum((b - mb) ** 2 for _, b in pairs) ** .5
            corr = sum((a - ma) * (b - mb) for a, b in pairs) / (va * vb) if va and vb else None
        by_sc[k] = {"adds_hit": nmz(pa & set(adds)), "adds_fp": nmz(pa - set(adds)), "adds_miss": nmz(set(adds) - pa),
                    "dels_hit": nmz(pd & set(dels)), "dels_fp": nmz(pd - set(dels)), "dels_miss": nmz(set(dels) - pd),
                    "n_pairs": len(pairs), "dir_hit": dir_hit, "corr": corr}
    return {"pdf_date": pdf_new["date"], "adds": sorted(names.get(c, c) for c in adds), "dels": sorted(names.get(c, c) for c in dels), "rows": rows,
            "total_buy": round(sum(r["flow"] for r in rows.values() if r["flow"] > 0), 1), "total_sell": round(sum(r["flow"] for r in rows.values() if r["flow"] < 0), 1),
            "by_scenario": by_sc, "computed": datetime.now().strftime("%Y-%m-%d %H:%M")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--today", default=date.today().isoformat())
    args = ap.parse_args()
    today = args.today
    HIST.mkdir(exist_ok=True)
    holdings = json.loads((DATA / "holdings.json").read_text(encoding="utf-8"))
    generated = json.loads((DATA / "analysis.json").read_text(encoding="utf-8")).get("generated")
    con = sqlite3.connect(DATA / "market.sqlite")
    names = {r[0]: r[1] for r in con.execute("SELECT code, name FROM stock")}

    cands = []
    semi_p, flows_p = DATA / "krx_semi_rebal.json", DATA / "flows.json"
    if semi_p.exists() and "krx_semi" in holdings:
        cands.append(from_semi(json.loads(semi_p.read_text(encoding="utf-8")), holdings, generated))
    if flows_p.exists():
        for ev in json.loads(flows_p.read_text(encoding="utf-8"))["events"]:
            cands.append(from_flow(ev, holdings, generated))

    # 1) 매매일 도래 → 스냅샷 동결 (기존 파일은 보존)
    for arc in cands:
        p = HIST / f"{arc['key']}_{arc['apply_start']}.json"
        if arc["trade_date"] <= today and not p.exists():
            p.write_text(json.dumps(arc, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"snapshot: {arc['name']} apply {arc['apply']} (매매일 {arc['trade_date']}, PDF {arc['snapshot']['pdf_date']}, "
                  f"{'매매 전' if arc['snapshot']['pre_trade'] else '매매 후(사후 스냅샷)'}) → {p.name}")

    # 2) 매매 후 PDF 도착 → 실제 결과 대조
    by_etf = {v["etf_code"]: v for v in holdings.values()}
    for p in sorted(HIST.glob("*.json")):
        arc = json.loads(p.read_text(encoding="utf-8"))
        pdf_new = by_etf.get(arc["proxy_etf"])
        if arc.get("actual") or not pdf_new or pdf_new["date"] < arc["settle_date"]:
            continue
        arc["actual"] = compute_actual(arc, pdf_new, con, names)
        p.write_text(json.dumps(arc, ensure_ascii=False, indent=1), encoding="utf-8")
        a = arc["actual"]
        print(f"actual: {arc['name']} PDF {a['pdf_date']} 편입 {a['adds']} 편출 {a['dels']} buy +{a['total_buy']:,.0f} sell {a['total_sell']:,.0f}")
        for k, v in a["by_scenario"].items():
            print(f"   [{k}] 편입 {len(v['adds_hit'])}/{len(a['adds'])} 편출 {len(v['dels_hit'])}/{len(a['dels'])} 방향일치 {v['dir_hit']} corr {v['corr']}")
    print(f"history: {len(list(HIST.glob('*.json')))} files")


if __name__ == "__main__":
    main()
