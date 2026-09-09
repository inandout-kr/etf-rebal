# -*- coding: utf-8 -*-
"""KRX 공식 GICS 산업분류 수집 (index.krx.co.kr > 산업분류 > GICS 산업분류 > 산업별 종목현황)

KRX가 KOSPI 200 / KOSDAQ 150 / KRX 섹터지수 심사에 참고하는 GICS 분류(2단계 산업그룹 24개)를
종목별로 받아 stock 테이블(gics_ig, gics_ig_nm, gics_sec)에 저장한다.
호출: GenerateOTP.jspx(bld=/IDX/03/0303/03030204/mkd03030204_03) → IDX99000001.jspx (JSON block1)
사용: python fetch_gics.py
"""
import sqlite3
import sys
import time
from datetime import date

import requests

from fetch_market import DB

sys.stdout.reconfigure(encoding="utf-8")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126",
      "Referer": "https://index.krx.co.kr/contents/MKD/03/0303/03030204/MKD03030204.jsp", "X-Requested-With": "XMLHttpRequest"}
PAGE = "/contents/MKD/03/0303/03030204/MKD03030204.jsp"
BLD = "/IDX/03/0303/03030204/mkd03030204_03"
GROUPS = {
    "1010": "에너지", "1510": "소재", "2010": "자본재", "2020": "상업전문서비스", "2030": "운송",
    "2510": "자동차및부품", "2520": "내구소비재및의류", "2530": "소비자서비스", "2550": "자유소비재유통및소매",
    "3010": "필수소비재유통및소매", "3020": "음식료담배", "3030": "가정및개인용품",
    "3510": "건강관리서비스및장비", "3520": "제약·생명공학·생명과학",
    "4010": "은행", "4020": "금융서비스", "4030": "보험",
    "4510": "소프트웨어및IT서비스", "4520": "하드웨어및IT장비", "4530": "반도체및반도체장비",
    "5010": "통신서비스", "5020": "미디어와엔터테인먼트", "5510": "유틸리티", "6010": "부동산리츠", "6020": "부동산관리및개발",
}
SECTOR_NM = {"10": "에너지", "15": "소재", "20": "산업재", "25": "자유소비재", "30": "필수소비재", "35": "헬스케어", "40": "금융",
             "45": "정보기술", "50": "커뮤니케이션서비스", "55": "유틸리티", "60": "부동산"}


def fetch_group(s, code, dt):
    otp = s.get("https://index.krx.co.kr/contents/COM/GenerateOTP.jspx", params={"bld": BLD, "name": "form"}, headers=UA, timeout=30).text.strip()
    r = s.post("https://index.krx.co.kr/contents/IDX/99/IDX99000001.jspx", headers=UA, timeout=60,
               data={"mkt_tp_cd": "ALL", "gics_ind_grp_cd": code, "date": dt, "lang": "ko", "pagePath": PAGE, "code": otp, "bldcode": BLD})
    j = r.json()
    return j.get("block1") or []


def main():
    s = requests.Session()
    s.get("https://index.krx.co.kr" + PAGE, headers=UA, timeout=30)
    dt = date.today().strftime("%Y%m%d")
    con = sqlite3.connect(DB)
    for col in ("gics_ig", "gics_ig_nm", "gics_sec"):
        try:
            con.execute(f"ALTER TABLE stock ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass
    con.execute("UPDATE stock SET gics_ig=NULL, gics_ig_nm=NULL, gics_sec=NULL")
    total, unmatched = 0, []
    for code, nm in GROUPS.items():
        rows = None
        for attempt in range(3):
            try:
                rows = fetch_group(s, code, dt)
                break
            except Exception as e:
                print("  retry", code, e)
                time.sleep(1.5)
        if rows is None:
            print("FAILED", code, nm)
            continue
        n = 0
        for r in rows:
            c = r.get("isu_cd")
            cur = con.execute("UPDATE stock SET gics_ig=?, gics_ig_nm=?, gics_sec=? WHERE code=?", (code, nm, code[:2], c))
            if cur.rowcount:
                n += 1
            else:
                unmatched.append((c, r.get("isu_abbr")))
        total += n
        print(f"{code} {nm:14s} rows={len(rows):4d} matched={n}")
        time.sleep(0.4)
    con.execute("INSERT OR REPLACE INTO meta VALUES('gics_date',?)", (dt,))
    con.commit()
    print(f"total matched {total}; unmatched(비상장/우선주 등) {len(unmatched)} e.g. {unmatched[:8]}")
    print("common stocks without GICS:", con.execute("SELECT COUNT(*) FROM stock WHERE is_common=1 AND gics_ig IS NULL").fetchone()[0])
    con.close()


if __name__ == "__main__":
    main()
