# -*- coding: utf-8 -*-
"""대표(프록시) ETF의 현재 구성종목(PDF) 수집 → 지수 현재 구성종목 추정

소스: wisereport ETF 페이지 내장 CU_data (종목명·수량·비중, 로그인 불필요)
종목명 → 종목코드 매핑은 data/market.sqlite 의 stock 테이블 사용.

출력: data/holdings.json  { etf_code: {"name":..., "date":..., "rows":[{"name","code","weight","count"}]} }
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).parent
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126"}

# 지수 프록시 ETF (지수키: ETF코드)
PROXY = {
    "kospi200": "069500",      # KODEX 200
    "kosdaq150": "229200",     # KODEX 코스닥150
    "kospi100": "237350",      # KODEX 코스피100
    "kospi": "226490",         # KODEX 코스피
    "kospi200_it": "139260",   # TIGER 200 IT
    "k200s_fin": "139270",     # TIGER 200 금융
    "k200s_enerchem": "139250",  # TIGER 200 에너지화학
    "k200s_ind": "227550",     # TIGER 200 산업재
    "k200s_const": "139220",   # TIGER 200 건설
    "k200s_heavy": "139230",   # TIGER 200 중공업
    "k200s_steel": "139240",   # TIGER 200 철강소재
    "k200s_staples": "227560", # TIGER 200 생활소비재
    "k200s_discr": "139290",   # TIGER 200 경기소비재
    "k200s_comm": "315270",    # TIGER 200 커뮤니케이션서비스
    "k200s_health": "227540",  # TIGER 200 헬스케어
    "fn_top10": "292150",      # TIGER 코리아TOP10
    "msci_korea": "310970",    # TIGER MSCI Korea TR
    "krx_semi": "091160",      # KODEX 반도체
    "fn_semitop10": "396500",  # TIGER 반도체TOP10
    "fn_aitop2": "395160",     # KODEX AI반도체TOP2플러스
    "fn_ksemi": "395270",      # HANARO Fn K-반도체
    "fn_top5plus": "315930",   # KODEX Top5PlusTR
    "fn_battery": "305720",    # KODEX 2차전지산업
    "fn_defense": "449450",    # PLUS K방산
    "fn_ship": "466920",       # SOL 조선TOP3플러스
    "fn_aitop3": "469150",     # ACE AI반도체TOP3+
    "mkf_samsung": "102780",   # KODEX 삼성그룹
    "mkf_hyundai": "138540",   # TIGER 현대차그룹플러스
    "valueup": "495050",       # RISE 코리아밸류업
    "fn_sobujang": "455850",   # SOL AI반도체소부장
    "fn_dividend": "161510",   # PLUS 고배당주
}
CASH = ("현금", "예금", "설정현금", "USD", "달러")


def fetch_cu(session, code):
    r = session.get(f"https://navercomp.wisereport.co.kr/v2/ETF/index.aspx?cmp_cd={code}", headers=UA, timeout=20)
    r.encoding = "utf-8"
    m = re.search(r"var CU_data = (\{.*?\});", r.text, re.S)
    if not m:
        return None, None
    data = json.loads(m.group(1))
    name = None
    mm = re.search(r"<title>([^<]+)</title>", r.text)
    if mm:
        name = mm.group(1).split("|")[0].strip()
    rows = []
    for row in data.get("grid_data") or []:
        nm = (row.get("STK_NM_KOR") or "").strip()
        if not nm or any(c in nm for c in CASH):
            continue
        rows.append({"name": nm, "count": row.get("AGMT_STK_CNT"), "weight": row.get("ETF_WEIGHT"), "date": row.get("TRD_DT")})
    return name, rows


def main():
    con = sqlite3.connect(ROOT / "data" / "market.sqlite")
    by_name = {}
    for code, name, mkt in con.execute("SELECT code, name, market FROM stock"):
        by_name.setdefault(name, []).append((code, mkt))
    s = requests.Session()
    out = {}
    for key, code in PROXY.items():
        name, rows = fetch_cu(s, code)
        if rows is None:
            print(f"  ! {key} {code}: CU_data 없음")
            continue
        unmatched = []
        for r in rows:
            cands = by_name.get(r["name"]) or []
            if len(cands) == 1:
                r["code"] = cands[0][0]
            elif len(cands) > 1:
                # 보통주(끝자리 0) 우선
                common = [c for c in cands if c[0].endswith("0")]
                r["code"] = (common or cands)[0][0]
            else:
                r["code"] = None
                unmatched.append(r["name"])
        date = rows[0]["date"] if rows else None
        out[key] = {"etf_code": code, "etf_name": name, "date": date, "rows": rows}
        print(f"  {key:14s} {code} {name} rows={len(rows)} date={date} unmatched={unmatched[:6]}")
    (ROOT / "data" / "holdings.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved data/holdings.json")


if __name__ == "__main__":
    main()
