# -*- coding: utf-8 -*-
"""KRX semiconductor: verified TMI buffers + dedicated KRICS membership + FFR/CF.
No current PDF is treated as an official future constituent list or an FFR source.
"""
import json
import math
import sqlite3
import sys
from pathlib import Path

from analyze import period_stats, load_review_events
from calendar_events import next_cycle, review_availability
from market_dates import anchor_stocks, completed_adv, korea_now, latest_completed_date, require_current_prices
from flow_calculation import (_input_date, _provenance, calculate_event, reason,
                              sector_membership, water_fill)
from flow_engine import read_flow_inputs

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).parent
DATA = ROOT / "data"
CAP = .20
CUM_SEC, LIQ_SEC, MIN_N = .95, .90, 20
MIDLARGE_CUM = .94
ETFS = [
    {"key": "krx_semi", "code": "091160", "name": "KODEX 반도체", "mult": 1.0},
    {"key": "krx_semi_tiger", "code": "091230", "name": "TIGER 반도체", "mult": 1.0},
    {"key": "krx_semi_lev", "code": "494310", "name": "KODEX 반도체레버리지", "mult": 2.0},
]


def coverage_select(items, cum_pct, coverage_cap=None):
    items = sorted(items, key=lambda x: (-x[1], x[0]))
    if not items:
        return [], None
    if coverage_cap:
        weights, _ = water_fill(dict(items), coverage_cap)
    else:
        total = sum(value for _, value in items)
        weights = {code: value / total for code, value in items}
    output, cumulative = [], 0
    for code, value in items:
        cumulative += weights[code]
        output.append((code, cumulative))
        if cumulative >= cum_pct - 1e-12:
            return output, value
    return output, items[-1][1]


def select_midlarge(items, previous_codes, coverage_cap=None):
    """P12 2.4.1/2.4.2: 94% first pass, existing >=75%, new >=133%."""
    if previous_codes is None:
        return {"codes": None, "status": "hold", "cutoff_mcap": None,
                "hold_reasons": [reason("previous_tmi_missing", "이전 중대형TMI 구성종목이 필요합니다.", "규모별TMI2.4.2")]}
    if not items:
        return {"codes": None, "status": "hold", "cutoff_mcap": None,
                "hold_reasons": [reason("tmi_statistics_missing", "TMI 심사기간 평균시총이 없습니다.")]}
    first, cutoff = coverage_select(items, MIDLARGE_CUM, coverage_cap)
    values, previous = dict(items), set(previous_codes)
    selected = {code for code, _ in first if code in previous or values[code] >= cutoff * 1.33 - 1e-9}
    selected |= {code for code in previous if values.get(code, 0) >= cutoff * .75 - 1e-9}
    return {"codes": sorted(selected), "status": "estimated", "first_pass_codes": [c for c, _ in first],
            "cutoff_mcap": cutoff, "coverage_cap": coverage_cap, "hold_reasons": [],
            "rule": {"coverage": .94, "existing_min_ratio": .75, "new_min_ratio": 1.33,
                     "cap10_scope": "TMI first-pass coverage only", "clause": "P12 2.4.1/2.4.2"}}


def resolve_tmi(supplied, stats, as_of, review_ref, pending, coverage_cap=None):
    reasons = []
    if pending:
        reasons.append(reason("review_not_started", "다음 KRX반도체 심사기간이 시작되지 않았습니다."))
    if not isinstance(supplied, dict):
        return {"codes": None, "status": "hold", "cutoff_mcap": None, "coverage_cap": coverage_cap,
                "hold_reasons": reasons + [reason("official_tmi_input_invalid", "공식 TMI 입력은 객체여야 합니다.")]}
    reference = _input_date(review_ref)
    valid = (_provenance(supplied, as_of) and reference is not None
             and _input_date(supplied.get("review_ref")) == reference and supplied.get("eligibility_verified") is True
             and supplied.get("event_coverage_verified") is True)
    if not valid:
        reasons.append(reason("official_tmi_universe_missing", "같은 심사기준일의 공식 TMI 적격목록·이벤트 검증이 필요합니다.", "P14 2.1/P12 2.1-2.3"))
    previous = supplied.get("previous_midlarge_codes")
    eligible = supplied.get("eligible_codes")

    def valid_codes(values, allow_empty=False):
        return (isinstance(values, list) and (bool(values) or allow_empty)
                and all(isinstance(c, str) and c and c.strip() == c for c in values)
                and len(values) == len(set(values)))

    if previous is None:
        reasons.append(reason("previous_tmi_missing", "이전 중대형TMI 구성목록 없이는75/133버퍼를 계산하지 않습니다.", "P12 2.4.2"))
    elif not valid_codes(previous, allow_empty=True):
        reasons.append(reason("previous_tmi_invalid", "이전 TMI 종목코드 목록에 중복·빈값·자료형 오류가 있습니다.", "P12 2.4.2"))
    if not valid_codes(eligible):
        reasons.append(reason("eligible_tmi_codes_invalid", "적격 TMI 종목코드 목록에 중복·빈값·자료형 오류가 있습니다.", "P12 2.1"))
    if coverage_cap and supplied.get("coverage_cap_status") != "official_discretion":
        reasons.append(reason("tmi_cap_discretion_unverified", "TMI10%상한의 해당 회차 공식 재량 적용을 확인해야 합니다.", "P12 2.4.1예외"))
    if reasons:
        return {"codes": None, "status": "hold", "hold_reasons": reasons, "cutoff_mcap": None, "coverage_cap": coverage_cap}
    codes = eligible
    if any(c not in stats or not stats[c].get("avg_mcap") for c in codes):
        return {"codes": None, "status": "hold", "hold_reasons": [reason("tmi_statistics_missing", "적격 TMI 종목의 심사기간 자료가 불완전합니다.")],
                "cutoff_mcap": None, "coverage_cap": coverage_cap}
    return select_midlarge([(c, stats[c]["avg_mcap"]) for c in codes], supplied["previous_midlarge_codes"], coverage_cap)


def semiconductor_selection(tmi, membership_input, stats, as_of):
    if tmi["codes"] is None:
        return {"codes": None, "status": "hold", "basis": "공식 TMI 입력 확인 대기", "hold_reasons": tmi["hold_reasons"]}
    mapped = sector_membership("krx_semi", tmi["codes"], membership_input, as_of)
    if mapped["codes"] is None:
        return mapped
    candidates = [dict(stats[c], code=c) for c in mapped["codes"]]
    candidates.sort(key=lambda x: (-x["avg_mcap"], x["code"]))
    liquidity = {x["code"] for x in sorted(candidates, key=lambda x: (-x["avg_trdval"], x["code"]))[:math.floor(len(candidates) * LIQ_SEC + 1e-9)]}
    # P14 sector95% uses raw sector market cap. TMI's discretionary10% is never copied here.
    coverage, _ = coverage_select([(x["code"], x["avg_mcap"]) for x in candidates], CUM_SEC)
    selected = {c for c, _ in coverage if c in liquidity}
    for row in candidates:
        if len(selected) >= MIN_N:
            break
        if row["code"] in liquidity:
            selected.add(row["code"])
    return {**mapped, "codes": sorted(selected), "status": "estimated", "basis": "공식 KRICS3반도체/TMI입력에95%·유동성90%·최소20규칙 적용(예상)",
            "sector_coverage_cap": None, "tmi_coverage_cap": tmi.get("coverage_cap")}


def _scenario(event, stats, current_codes, tmi):
    rows = []
    for row in event["rows"]:
        code = row["code"]
        rows.append({**row, "is_cur": code in current_codes, "w_cur_adj": row["w_cur"],
            "avg_mcap": stats.get(code, {}).get("avg_mcap"), "avg_trdval": stats.get(code, {}).get("avg_trdval"),
            "review_start": stats.get(code, {}).get("review_start"), "review_end": stats.get(code, {}).get("review_end"),
            "review_events": stats.get(code, {}).get("review_events", []),
            "in_midlarge": code in (tmi.get("codes") or []) if tmi.get("codes") is not None else None})
    return {**event, "rows": rows, "current_only": False, "n_eligible": None if tmi["codes"] is None else len(tmi["codes"]),
        "n_selected": event["n_target"], "coverage_cap": None, "tmi_coverage_cap": tmi.get("coverage_cap"),
        "capped": [r["code"] for r in rows if r["capped"]], "total_buy_eok": event["total_buy"], "total_sell_eok": event["total_sell"],
        "unavailable": False}


def main():
    con = sqlite3.connect((DATA / "market.sqlite").as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    stocks = {r["code"]: dict(r) for r in con.execute("SELECT * FROM stock")}
    holdings = json.loads((DATA / "holdings.json").read_text(encoding="utf-8"))
    etf_master = {e["code"]: e for e in json.loads((DATA / "etf_master.json").read_text(encoding="utf-8"))}
    analysis = json.loads((DATA / "analysis.json").read_text(encoding="utf-8"))
    supplied = read_flow_inputs(DATA)
    cycle = next_cycle("krx_semi", korea_now().date())
    as_of = latest_completed_date(con)
    if analysis.get("last_daily") != as_of:
        raise ValueError("분석과 반도체 수급의 시세 기준일이 다릅니다.")
    stocks = anchor_stocks(con, stocks, as_of, current=True)
    current_codes = {r["code"] for r in holdings["krx_semi"]["rows"] if r.get("code")}
    require_current_prices(stocks, current_codes, "KRX 반도체")
    pending = review_availability(cycle, as_of) == "pending_review"
    period = (cycle["period_start"], cycle["period_end"])
    events = [e for e in load_review_events() if e["type"] in ("merger", "split")]
    stats = period_stats(con, *period, as_of=as_of, review_events=events) if not pending else {}
    adv = completed_adv(con, as_of)
    price_cache = {}

    def prices_at(day):
        day = day.replace("-", "")
        if day not in price_cache:
            price_cache[day] = {r[0]: r[1] for r in con.execute("SELECT code, close FROM daily WHERE date=?", (day,))}
        return price_cache[day]

    cfg = {"key": "krx_semi", "name": "KRX 반도체", "proxy": "krx_semi",
           "etfs": [(e["code"], e["mult"]) for e in ETFS], "weighting": {"type": "float", "cap": .20},
           "apply": cycle["apply"], "fix": "공식 CAP Factor 산정기간·적용구간 확인 필요",
           "note": "공식 TMI·KRICS세부분류·FFR/CAP Factor 입력이 없으면 수급 보류"}
    index_inputs = supplied.get("indexes", {}).get("krx_semi", {})
    tmi_inputs = supplied.get("tmi") or {}
    scenarios, tmi_results = {}, {}
    for key, coverage_cap in (("literal", None), ("cap10", .10)):
        tmi = resolve_tmi(tmi_inputs, stats, as_of, cycle["ref_date"], pending, coverage_cap)
        membership = semiconductor_selection(tmi, index_inputs.get("membership"), stats, as_of)
        event = calculate_event(cfg, membership, stocks, holdings, etf_master, analysis, adv, prices_at, cycle, index_inputs)
        scenarios[key] = _scenario(event, stats, current_codes, tmi)
        tmi_results[key] = tmi
    chosen = "cap10" if tmi_inputs.get("coverage_cap") == .10 else "literal"
    scenarios["current"] = dict(scenarios[chosen])
    out = {"schema_version": 2, "index": "KRX 반도체", "ref_date": cycle["ref_date"], "period": period,
        "expiry": cycle["expiry"], "apply": cycle["apply"], "window": cycle, "data_through": as_of,
        "availability": scenarios["current"]["availability"], "review_availability": "pending_review" if pending else "in_review",
        "availability_note": "공식 입력이 부족한 항목은 목표비중·매매액·합계를 null로 보류합니다.",
        "hold_reasons": scenarios["current"]["hold_reasons"], "data_quality": analysis.get("data_quality"),
        "pdf_date": holdings["krx_semi"]["date"], "n_current": len(current_codes),
        "n_midlarge": len(tmi_results[chosen]["codes"]) if tmi_results[chosen]["codes"] is not None else None,
        "midlarge_cutoff_mcap": tmi_results[chosen].get("cutoff_mcap"), "tmi_scenarios": tmi_results,
        "rules": "공식 TMI94%+기존75%/신규133% 버퍼 → KRICS3반도체 → 섹터95%/유동성90%/최소20;공식FFR·고정CAPFactor",
        "krx_notices": [], "gics_excluded": {}, "cur_min_mcap_previous_review": None,
        "scenarios": scenarios, "selected_tmi_scenario": chosen,
        "scenario_desc": {"current": "공식 입력으로 선택한 TMI경로(입력 없으면 보류)",
            "literal": "TMI94%·75/133버퍼, 섹터95%에개별CAP미적용",
            "cap10": "해당회차 공식재량 확인시 TMI94%커버리지에만10%CAP;섹터95%에는미적용"}}
    (DATA / "krx_semi_rebal.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, allow_nan=False), encoding="utf-8")
    print("KRX 반도체", out["availability"], "hold reasons", len(out["hold_reasons"]))
    return out


if __name__ == "__main__":
    main()
