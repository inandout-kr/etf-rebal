# -*- coding: utf-8 -*-
"""KRX 반도체 지수 9월 정기변경 — 종목별 패시브 매수/매도 추정

공식 규칙 (docs/methodology/krx/130_KRX섹터지수.txt, 3_KRX규모별TMI.txt, 161_KRX_TMI.txt):
  - 심사기준일 = 정기변경월(9월)의 전전월 최종 매매거래일 = 7/31
  - 심사대상 = 같은 심사기준일의 KRX 중대형 TMI 구성종목(9월 정기변경 예정) 중 GICS 반도체 및 반도체장비
      · KRX TMI 유니버스: 유가+코스닥 보통주, 외국주권·SPAC·선박·집합투자기구 제외, 관리/투환/실질심사 제외, 유동비율 10% 미만 제외,
        유동시총 회전율 0.1% 미만 제외, 신규상장 3개월 미만 제외, 매매일수 15일 미만 제외
      · KRX 중대형 TMI: 심사기간(심사기준일 포함 최근 3개월 = 5~7월) 일평균시총 누적 94%까지
        (KRX 2026-09-04 공지: 이번 정기변경은 누적 커버리지 계산 시 개별종목 CAP 10% 적용)
  - 선정: 섹터 심사대상 중 일평균시총 순 누적 95%까지 & 일평균거래대금 상위 90% 이내, 20종목 미달 시 차순위 보충
  - 정기변경일 = KOSPI200 선물 9월 결제월 최종거래일(9/10)의 다음 매매거래일 = 9/11 (9/10 종가 리밸런싱)
  - 유동시가총액 가중, 20% CAP (CAP Factor 정기변경일 조정; KRX 2026-09-04 'CAP Factor 정기변경' 공지)

시나리오:
  cap10   : 누적 커버리지 계산에 개별종목 10% CAP 적용 (현행 35종목 구성과 정합적, KRX TMI 공지 방식) — 기본
  literal : 방법론 문언대로 단순 누적 95% → 사실상 20종목으로 축소
출력: data/krx_semi_rebal.json
"""
import json
import math
import sqlite3
import sys
from pathlib import Path

from analyze import period_stats, is_excluded_name

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).parent
DATA = ROOT / "data"

REF_DATE = "20260731"
PERIOD = ("20260501", "20260731")
EXPIRY, APPLY = "2026-09-10", "2026-09-11"
CAP = 0.20
CUM_SEC, LIQ_SEC, MIN_N = 0.95, 0.90, 20
MIDLARGE_CUM = 0.94
SEMI_WICS = "반도체와반도체장비"
# WICS는 반도체로 분류하나 GICS(KRX 적용)상 반도체가 아닌 것으로 추정되는 종목 (현행 지수 미편입 근거)
GICS_NOT_SEMI = {"402340": "SK스퀘어: 지주회사(GICS 비반도체 추정, 현행 KRX 반도체 미편입)",
                 "007660": "이수페타시스: PCB(GICS 전자부품, 현행 미편입)"}
ETFS = [
    {"key": "krx_semi", "code": "091160", "name": "KODEX 반도체", "mult": 1.0},
    {"key": "krx_semi_tiger", "code": "091230", "name": "TIGER 반도체", "mult": 1.0},
    {"key": "krx_semi_lev", "code": "494310", "name": "KODEX 반도체레버리지", "mult": 2.0},
]


def water_fill(weights, cap):
    """단일종목 비중상한 적용 (초과분을 미상한 종목에 비례 재배분, 반복)"""
    tot = sum(weights.values())
    w = {k: v / tot for k, v in weights.items()}
    capped = set()
    for _ in range(60):
        over = [k for k, v in w.items() if v > cap + 1e-12 and k not in capped]
        if not over:
            break
        capped |= set(over)
        free = 1 - cap * len(capped)
        rest = {k: v for k, v in w.items() if k not in capped}
        s = sum(rest.values())
        w = {k: (cap if k in capped else v / s * free) for k, v in w.items()}
    return w, capped


def coverage_select(items, cum_pct, coverage_cap=None):
    """items: [(code, avg_mcap)] 시총 내림차순 정렬 후 누적 커버리지 cum_pct 도달 종목까지 선정.
    coverage_cap 지정 시 커버리지 계산용 비중에 개별 CAP 적용."""
    items = sorted(items, key=lambda x: -x[1])
    if coverage_cap:
        w, _ = water_fill({c: m for c, m in items}, coverage_cap)
    else:
        tot = sum(m for _, m in items)
        w = {c: m / tot for c, m in items}
    out, cum, cutoff = [], 0.0, None
    for c, m in items:
        cum += w[c]
        out.append((c, cum))
        if cum >= cum_pct - 1e-12:
            cutoff = m
            break
    return out, cutoff


def main():
    con = sqlite3.connect(DATA / "market.sqlite")
    con.row_factory = sqlite3.Row
    stocks = {r["code"]: dict(r) for r in con.execute("SELECT * FROM stock")}
    holdings = json.loads((DATA / "holdings.json").read_text(encoding="utf-8"))
    etf_list = {e["code"]: e for e in json.loads((DATA / "etf_master.json").read_text(encoding="utf-8"))}
    fif_global = json.loads((DATA / "analysis.json").read_text(encoding="utf-8")).get("fif", {})
    stats = period_stats(con, *PERIOD)
    last_date = con.execute("SELECT max(date) FROM daily").fetchone()[0]
    adv = {r[0]: r[1] for r in con.execute(
        "SELECT code, AVG(trdval) FROM (SELECT code, trdval, ROW_NUMBER() OVER (PARTITION BY code ORDER BY date DESC) rn FROM daily) WHERE rn<=20 GROUP BY code")}

    # ---- KRX TMI 유니버스 → 중대형 TMI ----
    tmi = []
    for c, s in stocks.items():
        if not s["is_common"] or c[0] == "9" or is_excluded_name(s["name"]):
            continue
        ld = (s.get("listing_date") or "").replace("-", "")
        if ld and ld > "20260430":
            continue
        ps = stats.get(c)
        if not ps or not ps["avg_mcap"] or ps["n"] < 15:
            continue
        tmi.append((c, ps["avg_mcap"]))
    ml_sel, ml_cut = coverage_select(tmi, MIDLARGE_CUM, coverage_cap=0.10)
    midlarge = {c for c, _ in ml_sel}

    cur_rows = {r["code"]: r for r in holdings["krx_semi"]["rows"] if r.get("code")}
    semis = {c for c, s in stocks.items() if s.get("wics_mid") == SEMI_WICS and s["is_common"]} | set(cur_rows)
    # GICS 분류 추론: 2025년 9월 정기변경 심사기간(2025.5~7월) 기준으로 이미 규모요건을 충족했는데도 현행 지수에 없는 종목은
    # KRX(GICS)상 반도체가 아닌 것으로 추정 → 심사대상에서 제외 (현행 구성종목 중 최소 규모 종목의 2025.5~7월 일평균시총을 기준선으로 사용)
    stats25 = period_stats(con, "20250501", "20250731")
    cur_min25 = min((stats25[c]["avg_mcap"] for c in cur_rows if c in stats25 and stats25[c]["avg_mcap"]), default=None)
    gics_inferred = {}
    for c in semis:
        if c in cur_rows or c in GICS_NOT_SEMI:
            continue
        ld = (stocks[c].get("listing_date") or "").replace("-", "")
        m25 = stats25.get(c, {}).get("avg_mcap")
        if cur_min25 and m25 and (not ld or ld <= "20250430") and m25 >= cur_min25:
            gics_inferred[c] = f"2025 심사기간 일평균시총 {m25/1e8:,.0f}억 ≥ 현행 최소 구성종목 {cur_min25/1e8:,.0f}억인데 미편입 → GICS 비반도체 추정"
    pdf_date = holdings["krx_semi"]["date"].replace("-", "")
    px_pdf = {r[0]: r[1] for r in con.execute("SELECT code, close FROM daily WHERE date=?", (pdf_date,))}

    def build_universe():
        univ = []
        for c in semis:
            s = stocks[c]
            ps = stats.get(c)
            if not ps or not ps["avg_mcap"]:
                continue
            univ.append({"code": c, "name": s["name"], "market": s["market"], "avg_mcap": ps["avg_mcap"], "avg_trdval": ps["avg_trdval"],
                         "n_days": ps["n"], "is_cur": c in cur_rows, "w_cur": cur_rows.get(c, {}).get("weight"),
                         "in_midlarge": c in midlarge, "gics_excluded": GICS_NOT_SEMI.get(c) or gics_inferred.get(c),
                         "avg_mcap_2025": stats25.get(c, {}).get("avg_mcap"),
                         "mktcap_now": s["mktcap"], "close": s["close"], "wics_mid": s.get("wics_mid"), "listing_date": s.get("listing_date")})
        return univ

    # FIF 추정 (기존 비상한 종목: KODEX 반도체 비중/시총 역산, 상위 10분위=1.0)
    base = build_universe()
    ratios = {u["code"]: u["w_cur"] / u["mktcap_now"] for u in base if u["is_cur"] and u["w_cur"] and u["mktcap_now"] and u["code"] not in ("005930", "000660")}
    vals = sorted(ratios.values(), reverse=True)
    ref = vals[max(0, int(len(vals) * 0.1))] if vals else 1
    med = (sorted(ratios.values())[len(ratios) // 2] / ref) if ratios else 0.6

    def fif_of(code):
        if code in ratios:
            return max(0.05, min(1.0, ratios[code] / ref)), "KODEX반도체 역산"
        if code in fif_global:
            return fif_global[code], "KODEX200/코스닥150 역산"
        return round(med, 3), "중앙값 가정"

    def run_scenario(coverage_cap, current_only=False):
        univ = build_universe()
        elig = [u for u in univ if u["in_midlarge"] and not u["gics_excluded"] and (u["is_cur"] or not current_only)]
        elig.sort(key=lambda u: -u["avg_mcap"])
        by_trd = sorted(elig, key=lambda u: -u["avg_trdval"])
        liq_cut = math.floor(len(elig) * LIQ_SEC + 1e-9)
        for i, u in enumerate(by_trd):
            u["trd_rank"] = i + 1
            u["liq_ok"] = (i + 1) <= liq_cut
        cov, _ = coverage_select([(u["code"], u["avg_mcap"]) for u in elig], CUM_SEC, coverage_cap=coverage_cap)
        cov_codes = {c for c, _ in cov}
        cov_cum = dict(cov)
        selected = []
        for i, u in enumerate(elig):
            u["mcap_rank"] = i + 1
            u["primary"] = u["code"] in cov_codes
            u["cum_share"] = cov_cum.get(u["code"])
            if current_only or (u["primary"] and u["liq_ok"]):
                selected.append(u["code"])
        if len(selected) < MIN_N:
            for u in elig:
                if len(selected) >= MIN_N:
                    break
                if u["code"] not in selected and u["liq_ok"]:
                    selected.append(u["code"])
                    u["filled"] = True
        sel = set(selected)
        for u in univ:
            if u["gics_excluded"]:
                u["status"] = "심사제외(GICS 비반도체 추정)"
            elif not u["in_midlarge"]:
                u["status"] = "편출(중대형 TMI 밖)" if u["is_cur"] else "심사제외(중대형 밖)"
            elif u["code"] in sel:
                u["status"] = ("유지" if u["is_cur"] else "신규편입") + ("(20종목 보충)" if u.get("filled") else "")
            elif u["is_cur"] and current_only:
                u["status"] = "유지"
            elif not u.get("liq_ok"):
                u["status"] = "편출(유동성 미달)" if u["is_cur"] else ("심사제외(현행종목만)" if current_only else "미선정(유동성 미달)")
            else:
                u["status"] = "편출(커버리지 밖)" if u["is_cur"] else "미선정"
        for u in univ:
            u["fif"], u["fif_src"] = fif_of(u["code"])
            u["float_now"] = (u["mktcap_now"] or 0) * u["fif"]
        w_t, capped = water_fill({u["code"]: u["float_now"] for u in univ if u["code"] in sel and u["float_now"] > 0}, CAP)
        for u in univ:
            u["w_target"] = w_t.get(u["code"], 0.0) * 100
            u["capped"] = u["code"] in capped
        # ETF별 플로우
        etf_meta, flows = [], {u["code"]: {} for u in univ}
        for e in ETFS:
            h = holdings.get(e["key"])
            aum = etf_list.get(e["code"], {}).get("aum_eok")
            if h is None or not aum:
                continue
            if e["key"] == "krx_semi_lev":
                inner = next((r["weight"] for r in h["rows"] if r["name"] == "KODEX 반도체"), 0) or 0
                notional = aum * (e["mult"] - inner / 100)
                rows = {r["code"]: r["weight"] for r in holdings["krx_semi"]["rows"] if r.get("code") and r.get("weight") is not None}
                note = f"2배 명목 {aum*2:,.0f}억 − KODEX 반도체 실물보유 {inner:.1f}%(중복) = {notional:,.0f}억, 비중구조는 KODEX 반도체와 동일 가정(스왑·개별선물 헤지 포함)"
            else:
                notional = aum * e["mult"]
                rows = {r["code"]: r["weight"] for r in h["rows"] if r.get("code") and r.get("weight") is not None}
                note = f"순자산 {aum:,.0f}억 (네이버, {h['date']} PDF 비중)"
            adj = {}
            for c, w in rows.items():
                p0, p1 = px_pdf.get(c), stocks.get(c, {}).get("close")
                adj[c] = w * (p1 / p0) if (p0 and p1) else w
            s_ = sum(adj.values())
            adj = {c: w / s_ * 100 for c, w in adj.items()} if s_ else adj
            etf_meta.append({"code": e["code"], "name": e["name"], "aum_eok": aum, "notional_eok": round(notional), "pdf_date": h["date"], "note": note})
            for u in univ:
                wc = adj.get(u["code"], 0.0)
                u.setdefault("w_cur_etf", {})[e["code"]] = round(wc, 3)
                flows[u["code"]][e["code"]] = notional * (u["w_target"] - wc) / 100
        for u in univ:
            f = flows[u["code"]]
            u["flow_by_etf"] = {k: round(v, 1) for k, v in f.items()}
            u["flow_total"] = round(sum(f.values()), 1)
            u["adv20"] = adv.get(u["code"])
            u["adv_mult"] = (abs(u["flow_total"]) * 1e8 / u["adv20"]) if u["adv20"] else None
            u["w_cur_adj"] = u.get("w_cur_etf", {}).get("091160", 0.0)
            u["delta"] = u["w_target"] - u["w_cur_adj"]
        univ.sort(key=lambda u: -abs(u["flow_total"]))
        return {"coverage_cap": coverage_cap, "current_only": current_only, "n_eligible": len(elig), "n_selected": len(sel), "liq_cut": liq_cut, "capped": sorted(capped),
                "etfs": etf_meta, "rows": univ,
                "adds": [u["code"] for u in univ if u["status"].startswith("신규편입")],
                "dels": [u["code"] for u in univ if u["status"].startswith("편출")],
                "total_buy_eok": round(sum(u["flow_total"] for u in univ if u["flow_total"] > 0), 1),
                "total_sell_eok": round(sum(u["flow_total"] for u in univ if u["flow_total"] < 0), 1)}

    out = {
        "index": "KRX 반도체", "ref_date": REF_DATE, "period": PERIOD, "expiry": EXPIRY, "apply": APPLY, "data_through": last_date,
        "pdf_date": holdings["krx_semi"]["date"], "n_current": len(cur_rows), "n_midlarge": len(midlarge), "midlarge_cutoff_mcap": ml_cut,
        "rules": "심사대상 = KRX 중대형 TMI(5~7월 일평균시총, 누적 94%·커버리지 CAP 10%) ∩ GICS 반도체및반도체장비 → 일평균시총 누적 95% & 거래대금 상위 90%(최소 20종목) → 유동시총 가중·20% CAP",
        "krx_notices": ["2026-09-04 '26년 9월 KRX 100 지수 및 KRX 섹터지수 구성종목 정기변경 (반영일 9/11, 상세는 지수정보상품)",
                        "2026-09-04 '26년 9월 CAP Factor 정기변경 (KRX 100·섹터지수 17종, 9/11)",
                        "2026-09-04 '26.9월 KRX TMI 지수 정기변경 (커버리지 계산 시 개별종목 CAP 10% 적용)"],
        "gics_excluded": {**GICS_NOT_SEMI, **{c: v for c, v in gics_inferred.items()}}, "cur_min_mcap_2025": cur_min25,
        "scenarios": {"current": run_scenario(0.10, current_only=True), "cap10": run_scenario(0.10), "literal": run_scenario(None)},
        "scenario_desc": {
            "current": "A. 편출입 없음 — 현행 35종목 그대로 유동시총 가중·20% CAP 복원만 반영 (플로우 하한선, 확신도 높음)",
            "cap10": "B. 커버리지 CAP 10% — KRX가 9/4 TMI 공지에서 밝힌 방식(누적 시총 계산 시 개별종목 10% CAP)을 섹터지수에도 적용. WICS 반도체 중 GICS 미확인 후보 편입 포함(불확실)",
            "literal": "C. 방법론 문언 — 단순 누적 95%·거래대금 90%·최소 20종목 → 소형주 대거 편출 (현행 35종목 구성과 상충, 참고용)"},
    }
    (DATA / "krx_semi_rebal.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    for k, sc in out["scenarios"].items():
        print(f"[{k}] eligible {sc['n_eligible']} → selected {sc['n_selected']} (cur {len(cur_rows)}) | buy {sc['total_buy_eok']:,.0f}억 sell {sc['total_sell_eok']:,.0f}억")
        print("   adds:", [stocks[c]['name'] for c in sc['adds']], "| dels:", [stocks[c]['name'] for c in sc['dels']])
        for u in sc["rows"][:10]:
            print(f"   {u['name']:12s} {u['status']:14s} {u['w_cur_adj']:5.2f}%→{u['w_target']:5.2f}%  {u['flow_total']:>9,.0f}억  ADV×{(u['adv_mult'] or 0):.1f}")


if __name__ == "__main__":
    main()
