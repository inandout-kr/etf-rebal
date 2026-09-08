# -*- coding: utf-8 -*-
"""지수 정기/수시 변경 예측 엔진

공식 방법론(KRX 지수 방법론 원문) 기준으로 심사대상기간 누적 데이터를 이용해
KOSPI 200 / KOSDAQ 150 / KOSPI 100·50 정기변경 편출입을 시뮬레이션하고,
FnGuide TOP-N 계열·MSCI·특례편입·캡 트리거 모니터링 결과를 data/analysis.json 으로 저장.

사용: python analyze.py
"""
import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from calendar_events import (REVIEW_WINDOWS, futures_last_trading_day, next_business_day, events_upcoming)

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).parent
DATA = ROOT / "data"

# WICS 대분류 → KRX(GICS 참고) 산업군
WICS_TO_GICS = {"G10": "에너지", "G15": "소재", "G20": "산업재", "G25": "자유소비재", "G30": "필수소비재",
                "G35": "헬스케어", "G40": "금융및부동산", "G45": "정보기술", "G50": "커뮤니케이션서비스", "G55": "유틸리티"}
# WICS 중분류가 GICS와 다른 대표 케이스 (GICS 기준으로 보정)
WICS_MID_FIX = {"전기제품": "산업재"}          # 2차전지(LG엔솔·삼성SDI·에코프로비엠 등): GICS 전기장비=산업재
# KOSPI 200 섹터 ETF 보유 → GICS 산업군 (섹터지수 방법론 별표1)
K200_SECTOR_ETF = {
    "kospi200_it": "정보기술", "k200s_fin": "금융및부동산", "k200s_health": "헬스케어", "k200s_comm": "커뮤니케이션서비스",
    "k200s_discr": "자유소비재", "k200s_ind": "산업재", "k200s_heavy": "산업재",
    "k200s_enerchem": None,   # 에너지(G10) or 소재(화학)
    "k200s_steel": "소재", "k200s_const": None,   # 산업재(건설) or 소재(건축자재)
    "k200s_staples": None,    # 필수소비재 or 유틸리티
}
FIF = {}   # implied float ratio (main에서 채움)
EXCLUDE_NAME_KW = ("스팩", "인프라", "선박투자", "맵스리얼티")   # SPAC·인프라·선박투자회사 (리츠는 접미사로 별도 판정)
# WICS 분류가 GICS 상식과 다른 개별 종목 수동 보정 (코드: 산업군)
SECTOR_OVERRIDE = {"140410": "헬스케어"}   # 메지온 (WICS 식품 → 제약)


def is_excluded_name(nm):
    return nm.endswith("리츠") or any(k in nm for k in EXCLUDE_NAME_KW)


def load():
    con = sqlite3.connect(DATA / "market.sqlite")
    con.row_factory = sqlite3.Row
    stocks = {r["code"]: dict(r) for r in con.execute("SELECT * FROM stock")}
    meta = {k: v for k, v in con.execute("SELECT k, v FROM meta")}
    holdings = json.loads((DATA / "holdings.json").read_text(encoding="utf-8"))
    etfs = json.loads((DATA / "etf_master.json").read_text(encoding="utf-8"))
    return con, stocks, meta, holdings, etfs


def period_stats(con, start, end):
    """심사대상기간 [start, end] 일평균 시가총액(종가×상장주식수)·일평균 거래대금"""
    rows = con.execute(
        """SELECT code, COUNT(*) n, AVG(close*shares) avg_mcap, AVG(trdval) avg_trdval, MAX(date) last
           FROM daily WHERE date BETWEEN ? AND ? AND close IS NOT NULL AND shares IS NOT NULL GROUP BY code""",
        (start, end)).fetchall()
    return {r["code"]: {"n": r["n"], "avg_mcap": r["avg_mcap"], "avg_trdval": r["avg_trdval"], "last": r["last"]} for r in rows}


def implied_float(holdings, stocks, keys=("kospi200", "kosdaq150", "msci_korea")):
    """유동시가총액 가중 ETF의 PDF 비중 vs 전체 시총 비중으로 종목별 유동비율(FIF) 역산.
    FIF_i ∝ (w_i / mcap_i), 최댓값 기준 1.0으로 정규화 후 0.05~1.0 클리핑. 데이터 없는 종목은 None."""
    out = {}
    for k in keys:
        h = holdings.get(k)
        if not h:
            continue
        rows = [r for r in h["rows"] if r.get("code") and r.get("weight") and stocks.get(r["code"], {}).get("mktcap")]
        if not rows:
            continue
        ratios = {r["code"]: r["weight"] / stocks[r["code"]]["mktcap"] for r in rows}
        # 상위 10% 분위의 값을 FIF=1.0 기준으로 정규화 (완전유동 종목이 존재한다고 가정)
        vals = sorted(ratios.values(), reverse=True)
        ref = vals[max(0, int(len(vals) * 0.1))]
        for c, v in ratios.items():
            f = max(0.05, min(1.0, v / ref))
            out.setdefault(c, f)
    return out


def recent_avg_mcap(con, code, ndays):
    rows = con.execute("SELECT close*shares FROM daily WHERE code=? ORDER BY date DESC LIMIT ?", (code, ndays)).fetchall()
    vals = [r[0] for r in rows if r[0]]
    return (sum(vals) / len(vals)) if vals else None


def gics_sector(st, in_sector_etf):
    """KRX 산업군 추정: 섹터 ETF 보유(구성종목) 우선, 아니면 WICS(+보정)"""
    wics = st.get("wics_sec")
    mid = st.get("wics_mid") or ""
    base = WICS_TO_GICS.get(wics)
    if mid in WICS_MID_FIX:
        base = WICS_MID_FIX[mid]
    if st["code"] in SECTOR_OVERRIDE:
        return SECTOR_OVERRIDE[st["code"]], "수동보정"
    key = in_sector_etf.get(st["code"])
    if key:
        fixed = K200_SECTOR_ETF.get(key)
        if fixed:
            return fixed, "섹터ETF"
        if key == "k200s_enerchem":
            return ("에너지" if wics == "G10" else "소재"), "섹터ETF"
        if key == "k200s_const":
            return ("소재" if wics == "G15" else "산업재"), "섹터ETF"
        if key == "k200s_staples":
            return ("유틸리티" if wics == "G55" else "필수소비재"), "섹터ETF"
    return base, ("WICS" if base else None)


def eligible(st, ref_date, min_listing_months=6, is_cur=False):
    """심사대상 여부 (관리종목·유동비율 데이터 없음 → 미반영). 기존 구성종목(특례편입·분할 신설법인)은 상장기간 요건 예외"""
    nm = st["name"]
    if is_excluded_name(nm):
        return False, "리츠/스팩/인프라/선박"
    if st["code"][0] == "9":
        return False, "외국주권/DR"
    ld = st.get("listing_date")
    if ld and not is_cur:
        ld = ld.replace("-", "")
        cutoff = (datetime.strptime(ref_date, "%Y%m%d") - timedelta(days=int(30.5 * min_listing_months))).strftime("%Y%m%d")
        if ld > cutoff:
            return False, f"신규상장 {min_listing_months}개월 미만"
    return True, None


def simulate(univ, current, N, cum_pct, liq_pct, keep_buf, new_buf, sector_min_share=None, min_sector_exist=3):
    """KRX 대표지수 구성종목 선정 시뮬레이션 (1차·2차·3차 선정)

    univ: list of dict(code, name, sector, avg_mcap, avg_trdval, is_cur)
    """
    by_sec = defaultdict(list)
    for u in univ:
        by_sec[u["sector"]].append(u)
    total_mcap = sum(u["avg_mcap"] for u in univ)
    dropped_sectors = []
    selected = set()
    for sec, lst in by_sec.items():
        sec_mcap = sum(u["avg_mcap"] for u in lst)
        if sector_min_share and sec_mcap / total_mcap < sector_min_share:
            dropped_sectors.append(sec)
            for u in lst:
                u.update(status="산업군제외", note=f"산업군 시총 {sec_mcap/total_mcap*100:.2f}% <1%")
            continue
        lst.sort(key=lambda u: -u["avg_mcap"])
        n_sec = len(lst)
        liq_cut = math.floor(n_sec * liq_pct + 1e-9)
        by_trd = sorted(lst, key=lambda u: -u["avg_trdval"])
        for i, u in enumerate(by_trd):
            u["trd_rank"] = i + 1
            u["liq_ok"] = (i + 1) <= liq_cut
        cum = 0.0
        reached = False
        for i, u in enumerate(lst):
            u["mcap_rank"] = i + 1
            cum += u["avg_mcap"]
            u["cum_share"] = cum / sec_mcap
            u["primary"] = (not reached)
            if not reached and cum / sec_mcap >= cum_pct:
                reached = True
        n_exist = sum(1 for u in lst if u["is_cur"])
        for u in lst:
            u["n_sector"] = n_sec
            u["n_exist"] = n_exist
            u["rank_ratio"] = (u["mcap_rank"] / n_exist) if n_exist else None
            first = u["primary"] and u["liq_ok"]
            if u["is_cur"]:
                keep = u["liq_ok"] and n_exist and u["mcap_rank"] <= n_exist * keep_buf
                sel = first or keep
                u["status"] = "유지" if sel else "편출"
            else:
                if n_exist < min_sector_exist:
                    sel = first
                else:
                    sel = first and u["mcap_rank"] <= n_exist * new_buf
                u["status"] = "신규편입" if sel else "미선정"
            if sel:
                selected.add(u["code"])
    # 3차: 종목수 조정
    sel_list = [u for u in univ if u["code"] in selected]
    if len(sel_list) < N:
        pool = sorted([u for u in univ if u["code"] not in selected and u.get("liq_ok") and u.get("status") != "산업군제외"],
                      key=lambda u: -u["avg_mcap"])
        for u in pool[: N - len(sel_list)]:
            selected.add(u["code"])
            u["status"] = "유지(3차충원)" if u["is_cur"] else "신규편입(3차충원)"
    elif len(sel_list) > N:
        sel_list.sort(key=lambda u: u["avg_mcap"])
        for u in sel_list[: len(sel_list) - N]:
            selected.discard(u["code"])
            u["status"] = "편출(3차초과)" if u["is_cur"] else "미선정(3차초과)"
    return selected, dropped_sectors


def run_krx_index(con, stocks, holdings, key, cfg, sector_etf_map):
    """KOSPI 200 / KOSDAQ 150 시뮬레이션"""
    win = REVIEW_WINDOWS[key]
    stats = period_stats(con, win["period_start"], min(win["period_end"], date.today().strftime("%Y%m%d")))
    cur = {r["code"] for r in holdings[cfg["proxy"]]["rows"] if r.get("code")}
    curw = {r["code"]: r.get("weight") for r in holdings[cfg["proxy"]]["rows"] if r.get("code")}
    univ = []
    excluded = []
    for code, st in stocks.items():
        if st["market"] != cfg["market"] or not st["is_common"]:
            continue
        is_cur = code in cur
        ok, why = eligible(st, win["ref_date"], is_cur=is_cur)
        ps = stats.get(code)
        if not ok:
            if is_cur:
                excluded.append({"code": code, "name": st["name"], "why": why, "is_cur": True})
            continue
        if not ps or not ps["avg_mcap"] or not ps["avg_trdval"]:
            continue
        sec, src = gics_sector(st, sector_etf_map if key == "kospi200" else {})
        if not sec:
            if is_cur:
                excluded.append({"code": code, "name": st["name"], "why": "산업군 미분류(WICS 없음)", "is_cur": True})
            continue
        univ.append({"code": code, "name": st["name"], "sector": sec, "sector_src": src, "avg_mcap": ps["avg_mcap"],
                     "avg_trdval": ps["avg_trdval"], "n_days": ps["n"], "is_cur": is_cur, "cur_weight": curw.get(code),
                     "mktcap_now": st["mktcap"], "close": st["close"], "listing_date": st.get("listing_date"),
                     "fif": FIF.get(code)})
    selected, dropped = simulate(univ, cur, cfg["N"], cfg["cum"], cfg["liq"], cfg["keep_buf"], cfg["new_buf"],
                                 sector_min_share=cfg.get("sector_min_share"), min_sector_exist=3)
    # 대형주 특례 후보 (최근 15매매일 평균시총 상위 50위)
    for u in univ:
        u["mcap15"] = None
    top50 = sorted(univ, key=lambda u: -(u["mktcap_now"] or 0))[:50]
    for u in top50:
        u["mcap15"] = recent_avg_mcap(con, u["code"], 15)
    adds = sorted([u for u in univ if u["code"] in selected and not u["is_cur"]], key=lambda u: -u["avg_mcap"])
    dels = sorted([u for u in univ if u["code"] not in selected and u["is_cur"]], key=lambda u: u["avg_mcap"])
    # 경계 종목: 기존 - rank_ratio 0.95~1.3 / 신규 - primary & rank_ratio 0.7~1.0 or cum 0.8~0.9
    watch_keep = sorted([u for u in univ if u["is_cur"] and u["code"] in selected and u.get("rank_ratio") and u["rank_ratio"] >= 0.9],
                        key=lambda u: -u["rank_ratio"])
    watch_new = sorted([u for u in univ if not u["is_cur"] and u["code"] not in selected and u.get("rank_ratio") is not None
                        and u["rank_ratio"] <= 1.3 and (u.get("cum_share", 1) <= min(0.95, cfg["cum"] + 0.1) or u.get("primary"))],
                       key=lambda u: u["rank_ratio"])[:25]
    sectors = defaultdict(lambda: {"n_univ": 0, "n_exist": 0, "n_sel": 0, "mcap": 0.0})
    for u in univ:
        s = sectors[u["sector"]]
        s["n_univ"] += 1
        s["n_exist"] += int(u["is_cur"])
        s["n_sel"] += int(u["code"] in selected)
        s["mcap"] += u["avg_mcap"]
    fields = ["code", "name", "sector", "sector_src", "avg_mcap", "avg_trdval", "n_days", "mcap_rank", "n_exist", "n_sector",
              "rank_ratio", "cum_share", "trd_rank", "liq_ok", "primary", "status", "mktcap_now", "close", "listing_date", "mcap15", "note",
              "is_cur", "cur_weight", "fif"]

    def pick(u):
        return {k: u.get(k) for k in fields}

    return {
        "key": key, "name": cfg["name"], "proxy_etf": holdings[cfg["proxy"]]["etf_code"], "proxy_date": holdings[cfg["proxy"]]["date"],
        "window": win, "data_through": max((s["last"] for s in stats.values()), default=None),
        "n_universe": len(univ), "n_current": len(cur), "n_selected": len(selected),
        "rules": cfg["rules"],
        "adds": [pick(u) for u in adds], "dels": [pick(u) for u in dels],
        "watch_keep": [pick(u) for u in watch_keep], "watch_new": [pick(u) for u in watch_new],
        "excluded_current": excluded, "dropped_sectors": dropped,
        "sectors": {k: v for k, v in sorted(sectors.items(), key=lambda kv: -kv[1]["mcap"])},
        "all": [pick(u) for u in sorted(univ, key=lambda u: -u["avg_mcap"])],
    }


def run_kospi100_50(k200_result, holdings):
    """KOSPI 100/50: 12월 정기심사에서 선정된 KOSPI200 구성종목 중 일평균시총 상위 100/50 (기존 120%/신규 80% 버퍼)"""
    pool = [u for u in k200_result["all"] if u["status"] and u["status"].startswith(("유지", "신규편입"))]
    pool.sort(key=lambda u: -u["avg_mcap"])
    out = {}
    for name, N, hk in (("kospi100", 100, "kospi100"),):
        cur = {r["code"] for r in holdings[hk]["rows"] if r.get("code")}
        sel = set()
        for i, u in enumerate(pool):
            rank = i + 1
            if u["code"] in cur:
                if rank <= N * 1.2:
                    sel.add(u["code"])
            elif rank <= N * 0.8:
                sel.add(u["code"])
        sel_list = [u for u in pool if u["code"] in sel]
        if len(sel_list) > N:   # 기존 우선
            extra = [u for u in reversed(sel_list) if u["code"] not in cur][: len(sel_list) - N]
            for u in extra:
                sel.discard(u["code"])
        elif len(sel_list) < N:
            for u in pool:
                if len(sel) >= N:
                    break
                if u["code"] in cur and u["code"] not in sel:
                    sel.add(u["code"])
        ranks = {u["code"]: i + 1 for i, u in enumerate(pool)}
        adds = [dict(u, k100_rank=ranks[u["code"]]) for u in pool if u["code"] in sel and u["code"] not in cur]
        dels = [dict(u, k100_rank=ranks.get(u["code"])) for u in pool if u["code"] in cur and u["code"] not in sel]
        dels += [{"code": c, "name": next((r["name"] for r in holdings[hk]["rows"] if r.get("code") == c), c), "k100_rank": None, "note": "KOSPI200 편출/심사제외"}
                 for c in cur if c not in ranks]
        out[name] = {"N": N, "adds": adds, "dels": dels, "n_current": len(cur), "proxy_date": holdings[hk]["date"],
                     "border": [dict(u, k100_rank=ranks[u["code"]], is_cur=u["code"] in cur) for u in pool[int(N * 0.7):int(N * 1.25)]]}
    return out


INTERMEDIATE_HOLDCO = {"402340": "000660"}   # SK스퀘어 → SK하이닉스 (공정위 중간지주회사; FnGuide TOP10 방법론: 자회사 동시 포함 시 중간지주 제외)


def run_topn(con, stocks, holdings, key, name, N, universe_filter, window_start, window_end, note, fif=None, prefilter_top=None, holdco_rule=False):
    """단순 시가총액(1개월 평균) 상위 N 선정형 지수 (FnGuide TOP10·반도체TOP10 등) 예측"""
    stats = period_stats(con, window_start, min(window_end, date.today().strftime("%Y%m%d")))
    cur = {r["code"]: r for r in holdings[key]["rows"] if r.get("code")}
    cands = []
    for code, st in stocks.items():
        if not st["is_common"] or code[0] == "9":
            continue
        if is_excluded_name(st["name"]):
            continue
        if not universe_filter(st):
            continue
        ps = stats.get(code)
        if not ps or not ps["avg_mcap"]:
            continue
        f = (fif or {}).get(code)
        cands.append({"code": code, "name": st["name"], "market": st["market"], "avg_mcap": ps["avg_mcap"], "n_days": ps["n"],
                      "mktcap_now": st["mktcap"], "is_cur": code in cur, "cur_weight": cur.get(code, {}).get("weight"),
                      "wics_mid": st.get("wics_mid"), "fif": f, "avg_float_mcap": ps["avg_mcap"] * (f if f else 1.0)})
    if fif is not None:
        cands.sort(key=lambda u: -u["avg_mcap"])
        if prefilter_top:
            cands = cands[:prefilter_top]
        cands.sort(key=lambda u: -u["avg_float_mcap"])
    else:
        cands.sort(key=lambda u: -u["avg_mcap"])
    for i, u in enumerate(cands):
        u["rank"] = i + 1
    if holdco_rule:
        top_codes = {u["code"] for u in cands[:N]}
        for hold, sub in INTERMEDIATE_HOLDCO.items():
            if hold in top_codes and sub in top_codes:
                for u in cands:
                    if u["code"] == hold:
                        u["excluded"] = "중간지주회사 제외(자회사 동시 편입)"
        cands = [u for u in cands if not u.get("excluded")] + [u for u in cands if u.get("excluded")]
    sel = {u["code"] for u in cands[:N]}
    adds = [u for u in cands[:N] if not u["is_cur"]]
    dels = [u for u in cands if u["is_cur"] and u["code"] not in sel]
    missing = [{"code": c, "name": r["name"], "cur_weight": r.get("weight"), "note": "유니버스 필터 밖(업종분류 상이 가능)"} for c, r in cur.items() if c not in {u["code"] for u in cands}]
    return {"key": key, "name": name, "N": N, "proxy_etf": holdings[key]["etf_code"], "proxy_date": holdings[key]["date"],
            "window": {"start": window_start, "end": window_end}, "data_through": max((s["last"] for s in stats.values()), default=None),
            "note": note, "top": cands[: N + 10], "adds": adds, "dels": dels, "cur_not_in_universe": missing,
            "current": [{"code": c, "name": r["name"], "weight": r.get("weight")} for c, r in cur.items()]}


def cap_monitor(holdings, etfs):
    """FnGuide 계열 수시 비중개편(30% 트리거) 모니터: 프록시 ETF PDF 상 상위종목 비중"""
    checks = {
        "fn_semitop10": {"name": "FnGuide 반도체 TOP10", "trigger": 0.30, "rule": "매 월말 T-4~T 5영업일 연속 특정종목 비중 30% 초과 → T종가 기준 비중 재계산, T+3 반영 (TOP2 각 25% 고정)"},
        "fn_aitop2": {"name": "FnGuide AI반도체 TOP2+", "trigger": 0.30, "rule": "종가 비중 3영업일 연속 30% 초과 시 25% 실링 재적용, T+3 반영"},
        "fn_ksemi": {"name": "FnGuide K-반도체", "trigger": 0.30, "rule": "정기변경월(6·12월) 제외 매월: 전월말 30% 초과 종목 존재 시 당월 만기일 종가로 25% 제한, D+2 반영"},
        "fn_defense": {"name": "FnGuide K-방위산업", "trigger": 0.30, "rule": "매월말 30% 초과 시 25% 초과분 배분, 익월 둘째 영업일부터 반영"},
        "fn_ship": {"name": "FnGuide 조선 TOP3 플러스", "trigger": 0.30, "rule": "종가 비중 3영업일 연속 30% 초과 → 25%, T+3 반영"},
        "fn_sobujang": {"name": "FnGuide AI 반도체 소부장", "trigger": 0.30, "rule": "매월말 30% 초과 + 투자자보호 필요 시 25% 실링, D+3 반영"},
        "fn_top10": {"name": "FnGuide TOP10", "trigger": None, "rule": "정기변경(3·9월) 시 25% 실링. 수시 캡 점검 없음"},
        "fn_top5plus": {"name": "FnGuide TOP 5 Plus", "trigger": None, "rule": "정기변경 시 25% 고정. 수시 점검 없음"},
        "kospi200_it": {"name": "KOSPI 200 정보기술", "trigger": None, "rule": "섹터지수 20% CAP, CAP Factor는 정기변경일(6·12월) 조정. 연계상품 운용 곤란 시 수시 조정 가능"},
        "valueup": {"name": "코리아 밸류업", "trigger": None, "rule": "15% 비중상한, 정기변경(6월) 시 적용"},
        "krx_semi": {"name": "KRX 반도체", "trigger": None, "rule": "KRX 섹터지수 CAP (방법론 참조), 연1회(9월) 정기변경"},
    }
    out = []
    for k, c in checks.items():
        h = holdings.get(k)
        if not h:
            continue
        rows = sorted([r for r in h["rows"] if r.get("weight") is not None], key=lambda r: -r["weight"])[:5]
        out.append({"key": k, "name": c["name"], "etf": h["etf_code"], "date": h["date"], "rule": c["rule"], "trigger": c["trigger"],
                    "top": [{"name": r["name"], "code": r.get("code"), "weight": r["weight"], "over": (c["trigger"] is not None and r["weight"] >= c["trigger"] * 100)} for r in rows]})
    return out


def ipo_monitor(con, stocks):
    """신규상장 특례편입 모니터: KOSPI 상위 50위(+유동시총 0.5×50위) / KOSDAQ 상위 30위 (상장 후 15매매일 평균시총)"""
    today = date.today()
    out = []
    for mkt, top in (("KOSPI", 50), ("KOSDAQ", 30)):
        ranked = sorted([s for s in stocks.values() if s["market"] == mkt and s["is_common"] and s.get("mktcap")], key=lambda s: -s["mktcap"])
        thr = ranked[top - 1]["mktcap"] if len(ranked) >= top else 0
        for s in ranked:
            ld = s.get("listing_date")
            if not ld or s["code"][0] == "9" or is_excluded_name(s["name"]):
                continue
            d = datetime.strptime(ld, "%Y-%m-%d").date()
            if (today - d).days > 120:
                continue
            m15 = recent_avg_mcap(con, s["code"], 15)
            ndays = con.execute("SELECT COUNT(*) FROM daily WHERE code=? AND date>=?", (s["code"], ld.replace("-", ""))).fetchone()[0]
            rank = ranked.index(s) + 1
            out.append({"code": s["code"], "name": s["name"], "market": mkt, "listing_date": ld, "days_listed": ndays,
                        "mktcap": s["mktcap"], "rank": rank, "threshold_rank": top, "threshold_mcap": thr, "mcap15": m15,
                        "eligible": rank <= top,
                        "note": ("15매매일 경과 후 최초 도래 KOSPI200 선물 최근월물 최종거래일(둘째 목) 익일 편입" if rank <= top else f"시총 {top}위 밖")})
    return sorted(out, key=lambda x: x["rank"])


def msci_watch(stocks, holdings, usdkrw, fif=None):
    """MSCI Korea 표준지수 편입 후보 (참고용): 비구성 대형주. 기준: GIMI 2.3.2 EM Global Minimum Size Range 및 시장별 컷오프 (공식 컷오프는 리뷰 시점 산정)"""
    cur = {r["code"] for r in holdings["msci_korea"]["rows"] if r.get("code")}
    em_min_full = 3_940_000_000 * usdkrw     # USD 3.94bn (May 2026 GIMI, EM Standard 하한 0.5×)
    out = []
    for s in stocks.values():
        if not s["is_common"] or s["code"] in cur or not s.get("mktcap"):
            continue
        if is_excluded_name(s["name"]) or s["code"][0] == "9":
            continue
        if s["mktcap"] >= em_min_full * 0.8:
            f = (fif or {}).get(s["code"])
            out.append({"code": s["code"], "name": s["name"], "market": s["market"], "mktcap": s["mktcap"], "mktcap_usd_bn": s["mktcap"] / usdkrw / 1e9,
                        "fif": f, "float_usd_bn": (s["mktcap"] * f / usdkrw / 1e9) if f else None,
                        "listing_date": s.get("listing_date"), "note": "풀시총 ≥ EM 최소규모 0.8배. 유동시총(추정FIF)·외국인한도 요건은 별도"})
    out.sort(key=lambda x: -x["mktcap"])
    # 기존 구성종목 중 소형 (편출 후보 참고)
    small = sorted([{"code": r["code"], "name": r["name"], "weight": r.get("weight"), "mktcap": stocks.get(r["code"], {}).get("mktcap")}
                    for r in holdings["msci_korea"]["rows"] if r.get("code") and stocks.get(r["code"])], key=lambda x: (x["mktcap"] or 0))[:12]
    return {"candidates": out[:25], "smallest_current": small, "em_min_full_usd_bn": 3.94, "usdkrw": usdkrw,
            "n_current": len(cur), "proxy_date": holdings["msci_korea"]["date"]}


def fetch_usdkrw():
    try:
        import requests
        r = requests.get("https://m.stock.naver.com/api/marketindex/exchange/FX_USDKRW", headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        v = r.json().get("closePrice")
        return float(str(v).replace(",", ""))
    except Exception:
        return 1380.0


def tracking_aum(etfs, keys):
    """BM 문자열 키워드로 ETF 추종 AUM 합계(억원)"""
    tot = 0.0
    lst = []
    for e in etfs:
        bm = (e.get("bm") or "")
        if any(k in bm for k in keys):
            a = e.get("aum_eok") or 0
            tot += a
            lst.append({"code": e["code"], "name": e["name"], "bm": bm, "aum_eok": a})
    return tot, lst


def main():
    con, stocks, meta, holdings, etfs = load()
    sector_etf_map = {}
    for k in K200_SECTOR_ETF:
        for r in holdings.get(k, {}).get("rows", []):
            if r.get("code"):
                sector_etf_map[r["code"]] = k
    usdkrw = fetch_usdkrw()
    fif = implied_float(holdings, stocks)
    FIF.update(fif)

    cfg_k200 = dict(name="KOSPI 200", proxy="kospi200", market="KOSPI", N=200, cum=0.85, liq=0.85, keep_buf=1.10, new_buf=0.90,
                    rules="산업군(GICS 참고 10개)별 일평균시총 누적 85% & 거래대금 순위 산업군 심사대상 85% 이내 → 기존 110%/신규 90% 버퍼 → 200종목 조정")
    cfg_kq150 = dict(name="KOSDAQ 150", proxy="kosdaq150", market="KOSDAQ", N=150, cum=0.60, liq=0.80, keep_buf=1.20, new_buf=0.80,
                     sector_min_share=0.01,
                     rules="산업군(GICS 참고 11개, 시총 1% 미만 산업군 제외)별 누적 60% & 거래대금 순위 80% 이내 → 기존 120%/신규 80% 버퍼 → 150종목 조정 (소형주 300위 초과 제외 가능)")
    k200 = run_krx_index(con, stocks, holdings, "kospi200", cfg_k200, sector_etf_map)
    kq150 = run_krx_index(con, stocks, holdings, "kosdaq150", cfg_kq150, {})
    k100 = run_kospi100_50(k200, holdings)

    # FnGuide TOP10: 유가+코스닥 유동시총 상위 100 중 유동시총 상위 10 (선정 8월말 → 9월 만기 D+2 적용). 유동시총 데이터 없음 → 시총 근사
    top10 = run_topn(con, stocks, holdings, "fn_top10", "FnGuide TOP10 (TIGER 코리아TOP10)", 10,
                     lambda st: True, "20260803", "20260831",
                     "선정기준일 8/31 확정 → 9/14 적용. 유동시가총액 상위 10 (유동비율은 KODEX200·MSCI ETF 비중에서 역산한 추정치, 8월 단순시총 평균 × 추정FIF)",
                     fif=fif, prefilter_top=100, holdco_rule=True)
    semi10 = run_topn(con, stocks, holdings, "fn_semitop10", "FnGuide 반도체 TOP10 (TIGER 반도체TOP10)", 10,
                      lambda st: (st.get("wics_mid") or "") == "반도체와반도체장비", "20260901", "20260930",
                      "선정 9/30(1개월 단순시총 평균) → 10월 만기 익주 첫 영업일 적용. 유니버스: FICS 반도체 ≈ WICS 반도체와반도체장비 근사")
    # 추종 AUM
    aum_k200, lst_k200 = tracking_aum(etfs, ["코스피 200", "KOSPI 200"])
    aum_kq150, lst_kq150 = tracking_aum(etfs, ["코스닥 150"])

    result = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "market_updated": meta.get("updated"), "last_daily": meta.get("last_daily"), "usdkrw": usdkrw,
        "kospi200": k200, "kosdaq150": kq150, "kospi100": k100.get("kospi100"),
        "fn_top10": top10, "fn_semitop10": semi10,
        "cap_monitor": cap_monitor(holdings, etfs),
        "ipo_monitor": ipo_monitor(con, stocks),
        "msci": msci_watch(stocks, holdings, usdkrw, fif),
        "tracking_aum": {"kospi200": {"total_eok": aum_k200, "etfs": sorted(lst_k200, key=lambda x: -x["aum_eok"])},
                         "kosdaq150": {"total_eok": aum_kq150, "etfs": sorted(lst_kq150, key=lambda x: -x["aum_eok"])}},
        "events": events_upcoming(date.today(), 300),
        "fif": {k: round(v, 3) for k, v in fif.items()},
    }
    (DATA / "analysis.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"KOSPI200: univ={k200['n_universe']} cur={k200['n_current']} sel={k200['n_selected']} adds={len(k200['adds'])} dels={len(k200['dels'])}")
    print("  adds:", [(u['name'], u['sector'], round(u['rank_ratio'] or 0, 2)) for u in k200['adds']])
    print("  dels:", [(u['name'], u['sector'], round(u['rank_ratio'] or 0, 2), u['status']) for u in k200['dels']])
    print("  excluded current:", k200['excluded_current'])
    print(f"KOSDAQ150: univ={kq150['n_universe']} cur={kq150['n_current']} sel={kq150['n_selected']} adds={len(kq150['adds'])} dels={len(kq150['dels'])} dropped={kq150['dropped_sectors']}")
    print("  adds:", [(u['name'], u['sector'], round(u['rank_ratio'] or 0, 2)) for u in kq150['adds']])
    print("  dels:", [(u['name'], u['sector'], round(u['rank_ratio'] or 0, 2), u['status']) for u in kq150['dels']])
    print("  excluded current:", kq150['excluded_current'])
    print("KOSPI100 adds/dels:", [u['name'] for u in k100['kospi100']['adds']], [u['name'] for u in k100['kospi100']['dels']])
    print("FnGuide TOP10 adds/dels:", [u['name'] for u in top10['adds']], [u['name'] for u in top10['dels']])
    print("반도체TOP10 adds/dels:", [u['name'] for u in semi10['adds']], [u['name'] for u in semi10['dels']])
    print("IPO monitor:", [(x['name'], x['rank']) for x in result['ipo_monitor'][:8]])
    print("saved data/analysis.json")


if __name__ == "__main__":
    main()
