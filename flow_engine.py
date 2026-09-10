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
from datetime import date
from pathlib import Path

from calendar_events import prev_business_day

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).parent
DATA = ROOT / "data"

K200_ETFS = [("069500", 1.0), ("102110", 1.0), ("148020", 1.0), ("105190", 1.0), ("152100", 1.0), ("069660", 1.0), ("494890", 1.0), ("441800", 1.0),
             ("278530", 1.0), ("294400", 1.0), ("498400", 1.0), ("472150", 1.0), ("475720", 1.0), ("122630", 2.0)]
CONFIGS = [
    dict(key="fn_top10", name="FnGuide TOP10", apply="2026-09-14", fix="9/10 만기일 종가로 비중 확정", proxy="fn_top10", etfs=[("292150", 1.0)],
         target=("analysis_topn", "fn_top10"), weighting=dict(type="float", cap=0.25),
         note="선정(8/31 기준 20영업일 유동시총 평균 상위10) 확정 · 유동비율은 ETF 비중 역산 추정"),
    dict(key="fn_battery", name="FnGuide 2차전지 산업", apply="2026-09-14~16 (3영업일 분할)", fix="9/10(개편 마지막날 T-4) 종가", proxy="fn_battery", etfs=[("305720", 1.0)],
         target="current", weighting=dict(type="score_tiered", first=0.20, others=0.15),
         note="스코어(Z-score) 가중은 재현 불가 → 현재 비중을 스코어 대리로 두고 1위 20%/그외 15% 실링만 재적용. 종목 변경(8/31 선정) 미반영"),
    dict(key="fn_aitop3", name="FnGuide AI 반도체 TOP3+", apply="2026-09-14", fix="9/10 만기일 종가", proxy="fn_aitop3", etfs=[("469150", 1.0)],
         target="current", weighting=dict(type="topn_fixed", n=3, w=0.25, rest="equal", pick="weight"),
         note="TOP3(재무스코어 상위, 현행 TOP3 유지 가정) 각 25% 고정 + 나머지 17종목 균등(1.47%). 종목 변경 미반영"),
    dict(key="fn_semitop10", name="FnGuide 반도체 TOP10", apply="2026-10-12", fix="10/8(T-2) 종가", proxy="fn_semitop10", etfs=[("396500", 1.0), ("488080", 2.0)],
         target=("analysis_topn", "fn_semitop10"), weighting=dict(type="topn_fixed", n=2, w=0.25, rest="float", pick="mcap"),
         note="선정 9/30(1개월 단순시총 평균 상위10, 예측) · 상위2 각 25% 고정 + 하위8 유동시총 가중 50%"),
    dict(key="fn_aitop2", name="FnGuide AI반도체 TOP2+ (KODEX)", apply="2026-10-13", fix="10/8 만기일 종가", proxy="fn_aitop2", etfs=[("395160", 1.0)],
         target="current", weighting=dict(type="topn_fixed", n=2, w=0.25, rest="float", cap=0.15, pick="mcap"),
         note="ML 키워드 선정은 재현 불가 → 종목 변경 미반영. 시총 상위2 각 25% 고정, 그외 유동시총 가중·15% 실링(투자설명서)"),
    dict(key="fn_aitop2_sol", name="FnGuide AI반도체 TOP2 플러스 (SOL)", apply="2026-10-13", fix="10/8 만기일 종가", proxy="fn_aitop2_sol", etfs=[("0167A0", 1.0)],
         target="current", weighting=dict(type="topn_fixed", n=2, w=0.25, rest="float", cap=0.15, pick="mcap"),
         note="SOL 상품 기초지수(PR)는 구성종목 수가 달라 별도 계산. 종목 변경 미반영, TOP2 25%·그외 15% 실링"),
    dict(key="wise_battery", name="WISE 2차전지 테마", apply="2026-10-12", fix="10/8 만기일 종가(익일 적용)", proxy="wise_battery", etfs=[("305540", 1.0)],
         target="current", weighting=dict(type="float", cap=0.15), note="종목 변경 미반영, 15% 실링 재적용"),
    dict(key="fn_ship", name="FnGuide 조선 TOP3 플러스", apply="2026-11-16", fix="11/11(D-1) 종가", proxy="fn_ship", etfs=[("466920", 1.0)],
         target="current", weighting=dict(type="topn_fixed", n=3, w=0.25, rest="float", cap=0.20, pick="weight"),
         note="조선 TOP3(현행) 각 25% 고정 + 플러스 유니버스 유동시총 가중·20% 실링. 종목 변경(10/30 선정) 미반영"),
    dict(key="mkf_samsung", name="MKF 삼성그룹", apply="2026-12-02~08 (5영업일 분할)", fix="11월 말 리뷰", proxy="mkf_samsung", etfs=[("102780", 1.0)],
         target="current", weighting=dict(type="float", cap=0.25), note="유동시총 가중 25% 실링(삼성전자) 재적용"),
    dict(key="kospi200", name="KOSPI 200", apply="2026-12-11", fix="12/10 종가", proxy="kospi200", etfs=K200_ETFS,
         target=("analysis_krx", "kospi200"), weighting=dict(type="float", cap=None),
         note="추종 ETF(코스피200·TR·커버드콜·레버리지 2배) 합계 기준. 연기금·인덱스펀드 제외 → 실제 추종자금은 훨씬 큼. 유지 종목의 Δ는 유동비율 추정오차 포함"),
    dict(key="kosdaq150", name="KOSDAQ 150", apply="2026-12-11", fix="12/10 종가", proxy="kosdaq150", etfs=[("229200", 1.0), ("232080", 1.0), ("233740", 2.0)],
         target=("analysis_krx", "kosdaq150"), weighting=dict(type="float", cap=None), note="추종 ETF 합계(레버리지 2배) 기준"),
    dict(key="kospi100", name="KOSPI 100", apply="2026-12-11", fix="12/10 종가", proxy="kospi100", etfs=[("237350", 1.0)],
         target=("analysis_k100",), weighting=dict(type="float", cap=0.30), note="KOSPI200 심사 선정종목 중 상위100(버퍼 120/80%), 30% CAP"),
    dict(key="kospi200_it", name="코스피 200 정보기술", apply="2026-12-11", fix="12/10 종가", proxy="kospi200_it", etfs=[("139260", 1.0), ("243880", 2.0)],
         target=("k200_sector", "정보기술"), weighting=dict(type="float", cap=0.20), note="KOSPI200 12월 선정종목 중 GICS 정보기술 전부, 20% CAP 재설정"),
    dict(key="fn_ksemi", name="FnGuide K-반도체", apply="2026-12-16~17 (D+4·D+5)", fix="12/14(D+2) 종가", proxy="fn_ksemi", etfs=[("395270", 1.0)],
         target="current", weighting=dict(type="float", cap=0.25), note="종목 변경(11/30 선정) 미반영, 25% 실링 재적용"),
    dict(key="fn_defense", name="FnGuide K-방위산업", apply="2026-12-14", fix="12/10 만기일 종가", proxy="fn_defense", etfs=[("449450", 1.0)],
         target="current", weighting=dict(type="float", cap=0.20), note="종목 변경 미반영, 20% 실링 재적용"),
    dict(key="fn_sobujang", name="FnGuide AI 반도체 소부장", apply="2026-12-14", fix="12/9(D-1) 종가", proxy="fn_sobujang", etfs=[("455850", 1.0)],
         target="current", weighting=dict(type="float", cap=0.20), note="종목 변경 미반영, 20% 실링 재적용"),
    dict(key="is_power", name="iSelect AI 전력핵심설비", apply="2026-12-14", fix="만기일 익주 첫 영업일", proxy="is_power", etfs=[("487240", 1.0)],
         target="current", weighting=dict(type="float", cap=0.20), note="종목 변경 미반영, 20% 캡 재적용(투자설명서 기준)"),
    dict(key="kedi_power", name="KEDI 코리아AI전력기기TOP3+", apply="2026-12-15", fix="12/10 만기일 기준", proxy="kedi_power", etfs=[("0117V0", 1.0)],
         target="current", weighting=dict(type="topn_fixed", n=3, w=0.25, rest="current", pick="weight"), note="TOP3 각 25% 고정, 나머지 7종목 점수가중(현재비중 대리)"),
    dict(key="fn_top5plus", name="FnGuide TOP 5 Plus", apply="2026-12-14 (연2회 시)", fix="T-2 종가", proxy="fn_top5plus", etfs=[("315930", 1.0)],
         target="current", weighting=dict(type="float", cap=0.25), note="12월 정기변경 실시 여부 자료 상충(방법론상 연1회 6월). 참고용"),
]


def water_fill(weights, cap):
    tot = sum(weights.values())
    w = {k: v / tot for k, v in weights.items()}
    if not cap:
        return w, set()
    capped = set()
    for _ in range(80):
        over = [k for k, v in w.items() if v > cap + 1e-12 and k not in capped]
        if not over:
            break
        capped |= set(over)
        free = 1 - cap * len(capped)
        rest = {k: v for k, v in w.items() if k not in capped}
        s = sum(rest.values())
        w = {k: (cap if k in capped else v / s * free) for k, v in w.items()}
    return w, capped


def tiered_ceiling(weights, first, others):
    """가장 큰 종목 first, 나머지 others 실링 (초과분 비례 재배분 반복)"""
    tot = sum(weights.values())
    w = {k: v / tot for k, v in weights.items()}
    for _ in range(80):
        top = max(w, key=w.get)
        caps = {k: (first if k == top else others) for k in w}
        over = {k for k, v in w.items() if v > caps[k] + 1e-12}
        if not over:
            break
        fixed = sum(caps[k] for k in over)
        rest = {k: v for k, v in w.items() if k not in over}
        s = sum(rest.values())
        w = {k: (caps[k] if k in over else v / s * (1 - fixed)) for k, v in w.items()}
    return w


def main():
    con = sqlite3.connect(DATA / "market.sqlite")
    con.row_factory = sqlite3.Row
    stocks = {r["code"]: dict(r) for r in con.execute("SELECT * FROM stock")}
    holdings = json.loads((DATA / "holdings.json").read_text(encoding="utf-8"))
    by_etf = {v["etf_code"]: v for v in holdings.values()}
    etf_master = {e["code"]: e for e in json.loads((DATA / "etf_master.json").read_text(encoding="utf-8"))}
    A = json.loads((DATA / "analysis.json").read_text(encoding="utf-8"))
    fif_global = A.get("fif", {})
    adv = {r[0]: r[1] for r in con.execute(
        "SELECT code, AVG(trdval) FROM (SELECT code, trdval, ROW_NUMBER() OVER (PARTITION BY code ORDER BY date DESC) rn FROM daily) WHERE rn<=20 GROUP BY code")}
    px_cache = {}

    def px_at(dt):
        dt = dt.replace("-", "")
        if dt not in px_cache:
            px_cache[dt] = {r[0]: r[1] for r in con.execute("SELECT code, close FROM daily WHERE date=?", (dt,))}
        return px_cache[dt]

    def adjusted_weights(h):
        """PDF 비중을 최근 종가로 드리프트 보정 (현금 제외 재정규화)"""
        p0 = px_at(h["date"])
        adj = {}
        for r in h["rows"]:
            c, w = r.get("code"), r.get("weight")
            if not c or w is None or c not in stocks:
                continue
            a, b = p0.get(c), stocks[c].get("close")
            adj[c] = w * (b / a) if (a and b) else w
        s = sum(adj.values())
        return {c: w / s * 100 for c, w in adj.items()} if s else adj

    def target_codes(cfg, cur):
        t = cfg["target"]
        if t == "current":
            return list(cur), "구성종목 변경 없음 가정"
        if t[0] == "analysis_topn":
            r = A[t[1]]
            top = [u for u in r["top"] if not u.get("excluded")][: r["N"]]
            return [u["code"] for u in top], f"analyze.py 예측 (편입 {[u['name'] for u in r['adds']]} / 편출 {[u['name'] for u in r['dels']]})"
        if t[0] == "analysis_krx":
            r = A[t[1]]
            sel = [u["code"] for u in r["all"] if u.get("status") and (u["status"].startswith("유지") or u["status"].startswith("신규편입"))]
            return sel, f"analyze.py 12월 시뮬레이션 (편입 {len(r['adds'])} / 편출 {len(r['dels'])})"
        if t[0] == "analysis_k100":
            r = A["kospi100"]
            dels = {u["code"] for u in r["dels"]}
            adds = [u["code"] for u in r["adds"]]
            return [c for c in cur if c not in dels] + adds, f"analyze.py (편입 {[u['name'] for u in r['adds']]} / 편출 {[u['name'] for u in r['dels']]})"
        if t[0] == "k200_sector":
            r = A["kospi200"]
            sel = [u["code"] for u in r["all"] if u.get("status") and (u["status"].startswith("유지") or u["status"].startswith("신규편입")) and u.get("sector") == t[1]]
            # 현행 섹터ETF 구성종목 중 시뮬레이션에서 유지된 것 + 신규편입 중 해당 산업군
            return sel, f"KOSPI200 12월 시뮬레이션 선정종목 중 {t[1]} 산업군 ({len(sel)}종목)"
        return list(cur), "?"

    out = {"generated": A["generated"], "events": [], "by_stock": {}}
    today = date.today().isoformat()
    for cfg in CONFIGS:
        h = holdings.get(cfg["proxy"])
        if not h:
            print("skip (no proxy):", cfg["key"])
            continue
        cur_w = adjusted_weights(h)
        cur = set(cur_w)
        tgt, tgt_src = target_codes(cfg, cur)
        tgt = [c for c in tgt if c in stocks and stocks[c].get("mktcap")]
        wt = cfg["weighting"]
        cap = wt.get("cap")
        # ---- FIF 추정: 프록시 비중/시총 역산 (캡 근접·고정비중 종목 제외), 상위 10분위 = 1.0 ----
        excl = set()
        if wt["type"] == "topn_fixed":
            keyf = (lambda c: cur_w.get(c, 0)) if wt.get("pick") == "weight" else (lambda c: stocks[c]["mktcap"])
            excl |= set(sorted(cur, key=keyf, reverse=True)[: wt["n"]])
        if cap:
            excl |= {c for c in cur if cur_w[c] >= cap * 100 - 0.7}
        ratios = {c: cur_w[c] / stocks[c]["mktcap"] for c in cur if c not in excl and stocks[c].get("mktcap")}
        vals = sorted(ratios.values(), reverse=True)
        ref = vals[max(0, int(len(vals) * 0.1))] if vals else None
        med = (sorted(ratios.values())[len(ratios) // 2] / ref) if (ratios and ref) else 0.6

        def fif_of(c):
            if c in ratios and ref:
                # 상위 10분위 기준 정규화, 대형주 상대비중 보존을 위해 1.0 초과분은 1.25까지 허용
                return max(0.05, min(1.25, ratios[c] / ref)), "프록시 역산"
            if c in fif_global:
                return fif_global[c], "K200/KQ150 역산"
            return round(med, 3), "중앙값"

        fifs = {c: fif_of(c) for c in set(tgt) | cur}
        float_w = {c: stocks[c]["mktcap"] * fifs[c][0] for c in tgt}
        # ---- 목표비중 ----
        capped, fixed = set(), set()
        if wt["type"] == "float":
            w_t, capped = water_fill(float_w, cap)
        elif wt["type"] == "score_tiered":
            base = {c: (cur_w.get(c) or 0.01) for c in tgt}
            w_t = tiered_ceiling(base, wt["first"], wt["others"])
            capped = {c for c, v in w_t.items() if v >= wt["others"] - 1e-9}
        elif wt["type"] == "topn_fixed":
            keyf = (lambda c: cur_w.get(c, 0)) if wt.get("pick") == "weight" else (lambda c: stocks[c]["mktcap"])
            top = sorted(tgt, key=keyf, reverse=True)[: wt["n"]]
            fixed = set(top)
            rest = [c for c in tgt if c not in fixed]
            free = 1 - wt["w"] * len(top)
            if wt["rest"] == "equal":
                rw = {c: free / len(rest) for c in rest}
            elif wt["rest"] == "current":
                s = sum(cur_w.get(c, 0.01) for c in rest)
                rw = {c: free * cur_w.get(c, 0.01) / s for c in rest}
            else:
                rw, capped = water_fill({c: float_w[c] for c in rest}, (cap / free) if cap else None)
                rw = {c: v * free for c, v in rw.items()}
            w_t = {**{c: wt["w"] for c in top}, **rw}
        else:
            raise ValueError(wt["type"])
        # ---- ETF별 명목·플로우 ----
        etfs_meta, flows = [], {}
        for code, mult in cfg["etfs"]:
            aum = etf_master.get(code, {}).get("aum_eok")
            if not aum:
                continue
            eh = by_etf.get(code)
            note = f"순자산 {aum:,.0f}억"
            notional = aum * mult
            if mult > 1 and eh:
                # 코드 매칭 안 되는 보유행 중 선물이 아닌 것 = 기초 ETF 실물 보유 (KODEX 200 등)
                inner = sum((r.get("weight") or 0) for r in eh["rows"] if r.get("code") is None and "선물" not in r["name"])
                if inner:
                    notional = aum * (mult - inner / 100)
                    note = f"{mult:.0f}배 명목 {aum*mult:,.0f}억 − 기초ETF 실물 {inner:.1f}% 중복 = {notional:,.0f}억"
                else:
                    note = f"{mult:.0f}배 명목 {notional:,.0f}억 (스왑·선물 헤지 포함 가정)"
            elif mult > 1:
                note = f"{mult:.0f}배 명목 {notional:,.0f}억 (PDF 없음)"
            w_e = adjusted_weights(eh) if (eh and mult == 1) else cur_w
            etfs_meta.append({"code": code, "name": etf_master[code]["name"], "aum_eok": aum, "notional_eok": round(notional), "note": note})
            for c in set(tgt) | cur | set(w_e):
                flows.setdefault(c, {})[code] = notional * ((w_t.get(c, 0) * 100) - w_e.get(c, 0)) / 100
        all_codes = set(tgt) | cur | set(flows)
        rows = []
        for c in all_codes:
            f = flows.get(c, {})
            tot = sum(f.values())
            st = ("유지" if c in tgt else "편출") if c in cur else "신규편입"
            rows.append({"code": c, "name": stocks[c]["name"], "status": st, "w_cur": round(cur_w.get(c, 0), 3), "w_target": round(w_t.get(c, 0) * 100, 3),
                         "delta": round(w_t.get(c, 0) * 100 - cur_w.get(c, 0), 3), "fixed": c in fixed, "capped": c in capped,
                         "flow_by_etf": {k: round(v, 1) for k, v in f.items()}, "flow_total": round(tot, 1),
                         "adv20": adv.get(c), "adv_mult": (abs(tot) * 1e8 / adv[c]) if adv.get(c) else None,
                         "fif": round(fifs[c][0], 3), "fif_src": fifs[c][1], "mktcap_now": stocks[c]["mktcap"]})
        rows.sort(key=lambda r: -abs(r["flow_total"]))
        ev = {"key": cfg["key"], "name": cfg["name"], "apply": cfg["apply"], "fix": cfg.get("fix"), "proxy_etf": h["etf_code"], "pdf_date": h["date"],
              "target_src": tgt_src, "weighting": wt, "note": cfg.get("note"), "etfs": etfs_meta, "notional_total": round(sum(e["notional_eok"] for e in etfs_meta)),
              "n_current": len(cur), "n_target": len(tgt), "adds": [r["name"] for r in rows if r["status"] == "신규편입"], "dels": [r["name"] for r in rows if r["status"] == "편출"],
              "total_buy": round(sum(r["flow_total"] for r in rows if r["flow_total"] > 0), 1), "total_sell": round(sum(r["flow_total"] for r in rows if r["flow_total"] < 0), 1),
              "rows": rows}
        ev["trade_date"] = prev_business_day(date.fromisoformat(cfg["apply"][:10])).isoformat()   # 적용일 전 영업일 종가 매매
        ev["passed"] = ev["trade_date"] < today
        out["events"].append(ev)
        if ev["passed"]:   # 매매 끝난 이벤트: 스냅샷은 archive.py 가 보관, 합산·표시에서 제외
            continue
        for r in rows:
            out["by_stock"].setdefault(r["code"], {"name": r["name"], "events": [], "total": 0.0})
            out["by_stock"][r["code"]]["events"].append({"key": cfg["key"], "index": cfg["name"], "apply": cfg["apply"], "flow": r["flow_total"], "status": r["status"], "adv_mult": r["adv_mult"]})
            out["by_stock"][r["code"]]["total"] = round(out["by_stock"][r["code"]]["total"] + r["flow_total"], 1)
        print(f"{cfg['name']:24s} {cfg['apply']:22s} 명목 {ev['notional_total']:>8,}억  buy +{ev['total_buy']:>8,.0f} sell {ev['total_sell']:>9,.0f}  adds {ev['adds'][:4]} dels {ev['dels'][:4]}")
    # KRX 반도체(별도 모듈) 결과를 by_stock 합산에 포함
    semi_p = DATA / "krx_semi_rebal.json"
    if semi_p.exists():
        semi = json.loads(semi_p.read_text(encoding="utf-8"))
        sc = semi["scenarios"]["current"]
        for r in (sc["rows"] if semi["expiry"] >= today else []):
            if not r.get("flow_total"):
                continue
            out["by_stock"].setdefault(r["code"], {"name": r["name"], "events": [], "total": 0.0})
            out["by_stock"][r["code"]]["events"].append({"key": "krx_semi", "index": "KRX 반도체 (시나리오A)", "apply": semi["apply"], "flow": r["flow_total"], "status": r["status"], "adv_mult": r.get("adv_mult")})
            out["by_stock"][r["code"]]["total"] = round(out["by_stock"][r["code"]]["total"] + r["flow_total"], 1)
    (DATA / "flows.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("saved data/flows.json")


if __name__ == "__main__":
    main()
