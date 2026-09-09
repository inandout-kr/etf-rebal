# -*- coding: utf-8 -*-
"""백테스트: 2026년 6월 정기변경(KRX 5/22 발표)을 analyze.simulate() 로 재현해 공식 결과와 비교

  - 심사기준일 2026-04-30, 심사기간 2025-11-01~2026-04-30 (6개월)
  - 6월 이전 구성종목 = 현재 구성종목 − 6월 편입 + 6월 편출 (+ 6월 이후 수시변경 역보정)
  - 공식 결과: 코스피200 편입 4/편출 4, 코스닥150 편입 16/편출 16 (KRX 2026-05-22 공표, 언론 보도 확인)
"""
import json
import sqlite3
import sys
from pathlib import Path

from analyze import (period_stats, eligible, gics_sector, simulate, K200_SECTOR_ETF)

sys.stdout.reconfigure(encoding="utf-8")
DATA = Path(__file__).parent / "data"

OFFICIAL = {
    "kospi200": {"adds": ["HD건설기계", "DB하이텍", "달바글로벌", "OCI"], "dels": ["GS건설", "세방전지", "GKL", "녹십자홀딩스"]},
    "kosdaq150": {"adds": ["삼표시멘트", "에이치브이엠", "현대무벡스", "쎄트렉아이", "휴림로봇", "비츠로셀", "삼현", "한라캐스트", "오름테라퓨틱", "미래에셋벤처투자",
                           "원익홀딩스", "성호전자", "아이티센글로벌", "브이엠", "로보스타", "기가비스"],
                  "dels": ["성일하이텍", "현대힘스", "에코프로에이치엔", "에코앤드림", "골프존", "콜마비앤에이치", "동국제약", "메디톡스", "원텍", "바이넥스",
                           "미코", "제우스", "서울반도체", "엠로", "셀바스AI", "솔트룩스"]},
}
# 6월 이후 수시변경 (KRX 지수운영공지 제목 기준): 분할 신설법인 편입 / 주식교환·관리종목 편출
POST_JUNE_ADDED = {"kospi200": ["한화머시너리앤서비스홀딩스"], "kosdaq150": []}
POST_JUNE_REMOVED = {"kospi200": ["동양생명", "현대홈쇼핑", "더존비즈온"], "kosdaq150": ["CMG제약"]}
WIN = {"period_start": "20251101", "period_end": "20260430", "ref_date": "20260430"}
CFG = {
    "kospi200": dict(name="KOSPI 200", proxy="kospi200", market="KOSPI", N=200, cum=0.85, liq=0.85, keep_buf=1.10, new_buf=0.90),
    "kosdaq150": dict(name="KOSDAQ 150", proxy="kosdaq150", market="KOSDAQ", N=150, cum=0.60, liq=0.80, keep_buf=1.20, new_buf=0.80, sector_min_share=0.01),
}


def main():
    results = {}
    con = sqlite3.connect(DATA / "market.sqlite")
    con.row_factory = sqlite3.Row
    stocks = {r["code"]: dict(r) for r in con.execute("SELECT * FROM stock")}
    by_name = {}
    for c, s in stocks.items():
        if s["is_common"]:
            by_name.setdefault(s["name"], c)
    holdings = json.loads((DATA / "holdings.json").read_text(encoding="utf-8"))
    sector_etf_map = {}
    for k in K200_SECTOR_ETF:
        for r in holdings.get(k, {}).get("rows", []):
            if r.get("code"):
                sector_etf_map[r["code"]] = k
    stats = period_stats(con, WIN["period_start"], WIN["period_end"])
    cov = con.execute("SELECT substr(date,1,6) m, COUNT(DISTINCT code) FROM daily WHERE date BETWEEN ? AND ? GROUP BY 1", (WIN["period_start"], WIN["period_end"])).fetchall()
    print("데이터 커버리지(월별 종목수):", [(r[0], r[1]) for r in cov])

    for key, cfg in CFG.items():
        off = OFFICIAL[key]
        cur_now = {r["code"] for r in holdings[cfg["proxy"]]["rows"] if r.get("code")}
        names_to_codes = lambda names: {by_name[n] for n in names if n in by_name}
        missing = [n for n in off["adds"] + off["dels"] + POST_JUNE_ADDED[key] + POST_JUNE_REMOVED[key] if n not in by_name]
        pre = (cur_now - names_to_codes(off["adds"]) - names_to_codes(POST_JUNE_ADDED[key])) | names_to_codes(off["dels"]) | names_to_codes(POST_JUNE_REMOVED[key])
        # 6월 이후 수시로 편입된 예비종목(정체 미상)은 pre 에 남아 있을 수 있음 → 경고만
        univ, excluded = [], []
        for code, st in stocks.items():
            if st["market"] != cfg["market"] or not st["is_common"]:
                continue
            is_cur = code in pre
            ok, why = eligible(st, WIN["ref_date"], is_cur=is_cur)
            ps = stats.get(code)
            if not ok:
                if is_cur:
                    excluded.append((st["name"], why))
                continue
            if not ps or not ps["avg_mcap"] or not ps["avg_trdval"] or ps["n"] < 30:
                if is_cur:
                    excluded.append((st["name"], f"데이터 부족 n={ps['n'] if ps else 0}"))
                continue
            sec, src = gics_sector(st, sector_etf_map if key == "kospi200" else {}, scheme=key)
            if not sec:
                if is_cur:
                    excluded.append((st["name"], "산업군 미분류"))
                continue
            univ.append({"code": code, "name": st["name"], "sector": sec, "avg_mcap": ps["avg_mcap"], "avg_trdval": ps["avg_trdval"], "is_cur": is_cur})
        selected, dropped = simulate(univ, pre, cfg["N"], cfg["cum"], cfg["liq"], cfg["keep_buf"], cfg["new_buf"],
                                     sector_min_share=cfg.get("sector_min_share"), min_sector_exist=3)
        pred_adds = {u["name"] for u in univ if u["code"] in selected and not u["is_cur"]}
        pred_dels = {u["name"] for u in univ if u["code"] not in selected and u["is_cur"]} | {n for n, _ in excluded}
        oa, od = set(off["adds"]), set(off["dels"])
        results[key] = {"name": cfg["name"], "review": "2026.06 (심사기준일 2026-04-30, 심사기간 2025-11~2026-04, KRX 5/22 공표)",
                        "adds_hit": sorted(pred_adds & oa), "adds_fp": sorted(pred_adds - oa), "adds_miss": sorted(oa - pred_adds),
                        "dels_hit": sorted(pred_dels & od), "dels_fp": sorted(pred_dels - od), "dels_miss": sorted(od - pred_dels),
                        "n_official_adds": len(oa), "n_official_dels": len(od)}
        print(f"\n===== {cfg['name']} 2026.6 정기변경 백테스트 =====")
        print(f"pre-June 구성 {len(pre)} (현재 {len(cur_now)}), 심사대상 {len(univ)}, 선정 {len(selected)}, 산업군제외 {dropped}, 코드 미매칭 {missing}")
        print(f"편입  예측 {len(pred_adds)} / 정답 {len(oa)} | 적중 {sorted(pred_adds & oa)}")
        print(f"      미적중(예측만) {sorted(pred_adds - oa)}")
        print(f"      놓침(정답만)   {sorted(oa - pred_adds)}")
        print(f"편출  예측 {len(pred_dels)} / 정답 {len(od)} | 적중 {sorted(pred_dels & od)}")
        print(f"      미적중(예측만) {sorted(pred_dels - od)}")
        print(f"      놓침(정답만)   {sorted(od - pred_dels)}")
        # 놓친 종목/오탐 종목의 지표
        byname = {u["name"]: u for u in univ}
        for n in sorted((oa - pred_adds) | (pred_adds - oa) | (od - pred_dels) | (pred_dels - od)):
            u = byname.get(n)
            if u:
                print(f"   · {n:12s} {u['sector']:10s} 시총 {u['avg_mcap']/1e8:>9,.0f}억 순위 {u.get('mcap_rank')}/{u.get('n_exist')} ({(u.get('rank_ratio') or 0)*100:.0f}%) 누적 {(u.get('cum_share') or 0)*100:.1f}% 거래대금순위 {u.get('trd_rank')}/{u.get('n_sector')} 유동성 {u.get('liq_ok')} → {u.get('status')}")
            else:
                print(f"   · {n:12s} (심사대상 아님/데이터 없음) {[e for e in excluded if e[0]==n]}")
    (DATA / "backtest_june.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
