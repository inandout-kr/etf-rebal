# -*- coding: utf-8 -*-
"""정기변경 패시브 플로우 범용 엔진 — 남은 정기변경 전부에 대해
   현재비중(프록시 ETF PDF, 드리프트 보정) → 목표비중(공식 가중·캡 규칙) → ETF별 명목 × Δ비중 = 매수/매도 추정

목표 구성종목 소스
  current        : 구성종목 변경 없음(선정 로직 재현 불가 지수) — 비중·캡 재설정 효과만
  analysis_topn  : analyze.py TOP-N 예측 (FnGuide TOP10, 반도체TOP10)
  analysis_krx   : analyze.py KOSPI200/KOSDAQ150 시뮬레이션 (유지+신규편입)
  analysis_k100  : analyze.py KOSPI100
  k200_sector    : KOSPI200 시뮬레이션 결과 중 해당 산업군 (코스피200 섹터지수)
가중 방식 (공식 방법론)
  float          : 유동시총 가중 (+cap 실링, water-filling)
  topn_fixed     : 상위 n종목 w 고정, 나머지는 float/equal/current 방식(+cap)
  score_tiered   : 스코어 가중 대리(현재비중) + 1위 20%/그외 15% 실링 (FnGuide 2차전지)
출력: data/flows.json
"""
import json
import sqlite3
import sys
from pathlib import Path

from calendar_events import next_cycle
from flow_calculation import (aggregate_events, calculate_event, reason, sector_membership,
                              tiered_ceiling, water_fill)
from market_dates import anchor_stocks, completed_adv, korea_now, latest_completed_date, MARKET_CLOSE, require_current_prices

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).parent
DATA = ROOT / "data"

K200_ETFS = [("069500", 1.0), ("102110", 1.0), ("148020", 1.0), ("105190", 1.0), ("152100", 1.0), ("069660", 1.0), ("494890", 1.0), ("441800", 1.0),
             ("278530", 1.0), ("294400", 1.0), ("498400", 1.0), ("472150", 1.0), ("475720", 1.0), ("122630", 2.0)]
CONFIGS = [
    dict(key="fn_top10", name="FnGuide TOP10", proxy="fn_top10", etfs=[("292150", 1.0)],
         target=("analysis_topn", "fn_top10"), weighting=dict(type="float", cap=0.25),
         note="선정기준일 최근 20영업일 유동시총 평균 상위10 예측 · 유동비율은 ETF 비중 역산 추정"),
    dict(key="fn_battery", name="FnGuide 2차전지 산업", proxy="fn_battery", etfs=[("305720", 1.0)],
         target="current", weighting=dict(type="score_tiered", first=0.20, others=0.15),
         note="스코어(Z-score) 가중은 재현 불가 → 현재 비중을 스코어 대리로 두고 1위 20%/그외 15% 실링만 재적용. 종목 변경(전월 말 선정) 미반영"),
    dict(key="fn_aitop3", name="FnGuide AI 반도체 TOP3+", proxy="fn_aitop3", etfs=[("469150", 1.0)],
         target="current", weighting=dict(type="topn_fixed", n=3, w=0.25, rest="equal", pick="weight"),
         note="TOP3(재무스코어 상위, 현행 TOP3 유지 가정) 각 25% 고정 + 나머지 17종목 균등(1.47%). 종목 변경 미반영"),
    dict(key="fn_semitop10", name="FnGuide 반도체 TOP10", proxy="fn_semitop10", etfs=[("396500", 1.0), ("488080", 2.0)],
         target=("analysis_topn", "fn_semitop10"), weighting=dict(type="topn_fixed", n=2, w=0.25, rest="float", pick="mcap"),
         note="선정 전월 말(1개월 단순시총 평균 상위10, 예측) · 상위2 각 25% 고정 + 하위8 유동시총 가중 50%"),
    dict(key="fn_aitop2", name="FnGuide AI반도체 TOP2+ (KODEX)", proxy="fn_aitop2", etfs=[("395160", 1.0)],
         target="current", weighting=dict(type="topn_fixed", n=2, w=0.25, rest="float", cap=0.15, pick="mcap"),
         note="ML 키워드 선정은 재현 불가 → 종목 변경 미반영. 시총 상위2 각 25% 고정, 그외 유동시총 가중·15% 실링(투자설명서)"),
    dict(key="fn_aitop2_sol", name="FnGuide AI반도체 TOP2 플러스 (SOL)", proxy="fn_aitop2_sol", etfs=[("0167A0", 1.0)],
         target="current", weighting=dict(type="topn_fixed", n=2, w=0.25, rest="float", cap=0.15, pick="mcap"),
         note="SOL 상품 기초지수(PR)는 구성종목 수가 달라 별도 계산. 종목 변경 미반영, TOP2 25%·그외 15% 실링"),
    dict(key="wise_battery", name="WISE 2차전지 테마", proxy="wise_battery", etfs=[("305540", 1.0)],
         target="current", weighting=dict(type="float", cap=0.15), note="종목 변경 미반영, 15% 실링 재적용"),
    dict(key="fn_ship", name="FnGuide 조선 TOP3 플러스", proxy="fn_ship", etfs=[("466920", 1.0)],
         target="current", weighting=dict(type="topn_fixed", n=3, w=0.25, rest="float", cap=0.20, pick="weight"),
         note="조선 TOP3(현행) 각 25% 고정 + 플러스 유니버스 유동시총 가중·20% 실링. 종목 변경(전월 말 선정) 미반영"),
    dict(key="mkf_samsung", name="MKF 삼성그룹", proxy="mkf_samsung", etfs=[("102780", 1.0)],
         target="current", weighting=dict(type="float", cap=0.25), note="유동시총 가중 25% 실링(삼성전자) 재적용"),
    dict(key="kospi200", name="KOSPI 200", proxy="kospi200", etfs=K200_ETFS,
         target=("analysis_krx", "kospi200"), weighting=dict(type="float", cap=None),
         note="추종 ETF(코스피200·TR·커버드콜·레버리지 2배) 합계 기준. 연기금·인덱스펀드 제외 → 실제 추종자금은 훨씬 큼. 유지 종목의 Δ는 유동비율 추정오차 포함"),
    dict(key="kosdaq150", name="KOSDAQ 150", proxy="kosdaq150", etfs=[("229200", 1.0), ("232080", 1.0), ("233740", 2.0)],
         target=("analysis_krx", "kosdaq150"), weighting=dict(type="float", cap=None), note="추종 ETF 합계(레버리지 2배) 기준"),
    dict(key="kospi100", name="KOSPI 100", proxy="kospi100", etfs=[("237350", 1.0)],
         target=("analysis_k100",), weighting=dict(type="float", cap=0.30), note="KOSPI200 심사 선정종목 중 상위100(버퍼 120/80%), 30% CAP"),
    dict(key="kospi200_it", name="코스피 200 정보기술", proxy="kospi200_it", etfs=[("139260", 1.0), ("243880", 2.0)],
         target=("k200_sector", "정보기술"), weighting=dict(type="float", cap=0.20), note="공식 KRICS 별표·지주회사 예외와 공식 FFR/CAP Factor 확인 후 계산"),
    dict(key="fn_ksemi", name="FnGuide K-반도체", proxy="fn_ksemi", etfs=[("395270", 1.0)],
         target="current", weighting=dict(type="float", cap=0.25), note="종목 변경(전월 말 선정) 미반영, 25% 실링 재적용"),
    dict(key="fn_defense", name="FnGuide K-방위산업", proxy="fn_defense", etfs=[("449450", 1.0)],
         target="current", weighting=dict(type="float", cap=0.20), note="종목 변경 미반영, 20% 실링 재적용"),
    dict(key="fn_sobujang", name="FnGuide AI 반도체 소부장", proxy="fn_sobujang", etfs=[("455850", 1.0)],
         target="current", weighting=dict(type="float", cap=0.20), note="종목 변경 미반영, 20% 실링 재적용"),
    dict(key="is_power", name="iSelect AI 전력핵심설비", proxy="is_power", etfs=[("487240", 1.0)],
         target="current", weighting=dict(type="float", cap=0.20), note="종목 변경 미반영, 20% 캡 재적용(투자설명서 기준)"),
    dict(key="kedi_power", name="KEDI 코리아AI전력기기TOP3+", proxy="kedi_power", etfs=[("0117V0", 1.0)],
         target="current", weighting=dict(type="topn_fixed", n=3, w=0.25, rest="current", pick="weight"), note="TOP3 각 25% 고정, 나머지 7종목 점수가중(현재비중 대리)"),
    dict(key="fn_top5plus", name="FnGuide TOP 5 Plus", proxy="fn_top5plus", etfs=[("315930", 1.0)],
         target="current", weighting=dict(type="float", cap=0.25), note="12월 정기변경 실시 여부 자료 상충(방법론상 연1회 6월). 참고용"),
]


def build_configs(today=None):
    out = []
    for cfg in CONFIGS:
        cycle = next_cycle(cfg["key"], today)
        apply = cycle["apply"]
        if cycle["apply_end"] != apply:
            apply += f"~{cycle['apply_end']} (분할 적용)"
        out.append(dict(cfg, apply=apply, fix=f"{cycle['fix_date']} 종가 기준(일정 규칙)", cycle=cycle))
    return out


def event_has_passed(cycle, now=None):
    now = korea_now(now)
    end = cycle["trade_end"]
    return end < now.date().isoformat() or (end == now.date().isoformat() and now.time() >= MARKET_CLOSE)



def read_flow_inputs(directory=None):
    directory = directory or DATA
    path = directory / "flow_inputs.json"
    if not path.exists():
        return {"schema_version": 1, "indexes": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("flow_inputs.json 최상위는 객체여야 합니다.")
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
        raise ValueError("지원하지 않는 공식 수급입력 형식입니다.")
    indexes = payload.get("indexes", {})
    if not isinstance(indexes, dict):
        raise ValueError("flow_inputs.json indexes는 객체여야 합니다.")
    if any(not isinstance(key, str) or not key.strip() or not isinstance(value, dict)
           for key, value in indexes.items()):
        raise ValueError("flow_inputs.json의 지수별 입력은 이름이 있는 객체여야 합니다.")
    if "tmi" in payload and not isinstance(payload["tmi"], dict):
        raise ValueError("flow_inputs.json tmi는 객체여야 합니다.")
    return payload


def resolve_membership(cfg, analysis, current, inputs):
    target = cfg["target"]
    if target == "current":
        return {"codes": sorted(current), "status": "estimated", "basis": "구성종목 유지 가정", "hold_reasons": []}
    result = analysis.get(target[1], {}) if len(target) > 1 else analysis.get("kospi100", {})
    if target[0] == "analysis_topn":
        if result.get("availability") == "pending_review":
            return {"codes": None, "status": "hold", "basis": "심사기간 시작 전 · 편출입 예측 없음",
                    "hold_reasons": [reason("review_not_started", "다음 심사기간이 시작되지 않았습니다.")]}
        selected = [u["code"] for u in result.get("top", []) if not u.get("excluded")][:result["N"]]
    elif target[0] == "analysis_krx":
        selected = [u["code"] for u in result["all"] if u.get("status", "").startswith(("유지", "신규편입"))]
    elif target[0] == "analysis_k100":
        selected = sorted((set(current) - {u["code"] for u in result["dels"]}) | {u["code"] for u in result["adds"]})
    elif target[0] == "k200_sector":
        parent = analysis["kospi200"]
        selected = [u["code"] for u in parent["all"] if u.get("status", "").startswith(("유지", "신규편입"))]
        return sector_membership(cfg["key"], selected, inputs.get("membership"), analysis["last_daily"])
    else:
        return {"codes": None, "status": "hold", "basis": "목표 구성 미확인",
                "hold_reasons": [reason("unsupported_target", "지원하지 않는 목표 구성종목 입력입니다.")]}
    return {"codes": selected, "status": "estimated", "basis": "기존 선정 시뮬레이션 · 공식 결과 아님",
            "classification": (result.get("classification") or {}).get("scheme"), "hold_reasons": []}


def krx_variants(result):
    """Two independent scenario axes; alternatives never enter the aggregate twice."""
    variants = {}
    for policy, value in result.get("smallcap_scenarios", {}).items():
        variants[f"GICS:{policy}"] = value
    comparison = result.get("classification_scenarios", {})
    if comparison.get("status") == "ready":
        for policy, scenarios in comparison.get("smallcap_scenarios", {}).items():
            for interpretation, value in scenarios.items():
                variants[f"{interpretation}:{policy}"] = value
    return variants


def variant_membership(result, scenario):
    selected = [r["code"] for r in result["all"]
                if (r.get("status") or "").startswith(("유지", "신규편입"))]
    scheme, policy = scenario.split(":", 1)
    held = scheme != "GICS"
    return {"codes": selected, "status": "hold" if held else "estimated",
            "basis": f"{'기존 GICS' if not held else 'KRICS-' + scheme} · 300위 재량 {'적용' if policy == 'applied' else '미적용'} · 공식 확정 아님",
            "classification": "GICS" if not held else "KRICS", "scenario_id": scenario,
            "small_cap_policy": policy,
            "hold_reasons": [reason("krics_weight_inputs_unverified",
                "KRICS 분류 적용 시점과 공식 유동주식비율·가중 입력 확인 전 매매 추정은 HOLD입니다.")] if held else []}


def main():
    con = sqlite3.connect((DATA / "market.sqlite").as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    stocks = {r["code"]: dict(r) for r in con.execute("SELECT * FROM stock")}
    holdings = json.loads((DATA / "holdings.json").read_text(encoding="utf-8"))
    by_etf = {v["etf_code"]: v for v in holdings.values()}
    etf_master = {e["code"]: e for e in json.loads((DATA / "etf_master.json").read_text(encoding="utf-8"))}
    analysis = json.loads((DATA / "analysis.json").read_text(encoding="utf-8"))
    inputs = read_flow_inputs()
    now = korea_now()
    as_of = latest_completed_date(con, now)
    if analysis.get("last_daily") != as_of:
        raise ValueError("분석과 수급 계산의 시세 기준일이 다릅니다.")
    stocks = anchor_stocks(con, stocks, as_of, current=True)
    adv = completed_adv(con, as_of)
    cache = {}

    def prices_at(day):
        day = day.replace("-", "")
        if day not in cache:
            cache[day] = {r[0]: r[1] for r in con.execute("SELECT code, close FROM daily WHERE date=?", (day,))}
        return cache[day]

    out = {"schema_version": 2, "generated": analysis["generated"], "data_through": as_of,
           "data_quality": analysis.get("data_quality"), "events": [], "by_stock": {},
           "calculation_basis": "precomputed_event_rows; official FFR/CF separated from relative coefficients",
           "adv_basis": {"sessions": 20, "as_of": as_of}}
    for cfg in build_configs(now.date()):
        h = holdings.get(cfg["proxy"])
        if not h:
            continue
        relevant = [h] + [by_etf[c] for c, _ in cfg["etfs"] if c in by_etf]
        require_current_prices(stocks, {r["code"] for entry in relevant for r in entry["rows"]
            if r.get("code") and r.get("weight") is not None}, cfg["name"])
        supplied = inputs.get("indexes", {}).get(cfg["key"], {})
        membership = resolve_membership(cfg, analysis, {r["code"] for r in h["rows"] if r.get("code")}, supplied)
        if membership["codes"] is not None:
            require_current_prices(stocks, membership["codes"], cfg["name"])
        event = calculate_event(cfg, membership, stocks, holdings, etf_master, analysis, adv, prices_at, cfg["cycle"], supplied)
        event["passed"] = event_has_passed(cfg["cycle"], now)
        if cfg["key"] == "kosdaq150":
            scenario_flows = {}
            for scenario_id, variant in krx_variants(analysis[cfg["key"]]).items():
                scenario_membership = variant_membership(variant, scenario_id)
                scenario_event = calculate_event(cfg, scenario_membership, stocks, holdings, etf_master,
                    analysis, adv, prices_at, cfg["cycle"], supplied)
                scenario_event.update(scenario_id=scenario_id, small_cap_policy=scenario_membership["small_cap_policy"],
                                      passed=event["passed"])
                scenario_flows[scenario_id] = scenario_event
            event["scenario_id"] = "GICS:applied"
            event["small_cap_policy"] = "applied"
            event["scenario_flows"] = scenario_flows
        out["events"].append(event)
        print(cfg["key"], event["availability"], "hold reasons", len(event["hold_reasons"]))
    aggregate = list(out["events"])
    semi_path = DATA / "krx_semi_rebal.json"
    if semi_path.exists():
        semi = json.loads(semi_path.read_text(encoding="utf-8"))
        scenario = semi.get("scenarios", {}).get("current", {})
        rows = scenario.get("rows", [])
        if semi.get("schema_version") != 2:
            rows = [{**r, "flow_total": None, "status": "검토보류"} for r in rows]
        aggregate.append({"key": "krx_semi", "name": "KRX 반도체", "apply": semi["apply"], "rows": rows,
            "hold_reasons": scenario.get("hold_reasons", [reason("legacy_flow_not_replayed", "공식 입력 검증 전 수급은 합산하지 않습니다.")]),
            "passed": event_has_passed(semi.get("window") or {"trade_end": semi["expiry"]}, now)})
    out["by_stock"] = aggregate_events(aggregate)
    out["coverage"] = {"known_events": sum(e["availability"] != "hold" for e in out["events"]),
                       "held_events": sum(e["availability"] == "hold" for e in out["events"]),
                       "complete": all(e["availability"] != "hold" for e in out["events"])}
    (DATA / "flows.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, allow_nan=False), encoding="utf-8")
    print("saved data/flows.json")
    return out


if __name__ == "__main__":
    main()
