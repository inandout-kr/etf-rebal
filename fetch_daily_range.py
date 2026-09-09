# -*- coding: utf-8 -*-
"""특정 종목군의 일별 시세를 지정 시작일부터 보강 수집 (다음 API, 400영업일 한도)
사용: python fetch_daily_range.py --since 20260201 --wics 반도체와반도체장비 [--codes 000660,005930]
"""
import argparse, sqlite3, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from fetch_market import DB, fetch_daily_daum

sys.stdout.reconfigure(encoding="utf-8")
ap = argparse.ArgumentParser()
ap.add_argument("--since", default="20260201")
ap.add_argument("--wics", default=None)
ap.add_argument("--codes", default=None)
ap.add_argument("--all-common", action="store_true")
a = ap.parse_args()
con = sqlite3.connect(DB)
codes = set()
if a.wics:
    codes |= {r[0] for r in con.execute("SELECT code FROM stock WHERE is_common=1 AND wics_mid=?", (a.wics,))}
if a.codes:
    codes |= set(a.codes.split(","))
if a.all_common:
    codes |= {r[0] for r in con.execute("SELECT code FROM stock WHERE is_common=1")}
print(f"codes: {len(codes)} since {a.since}")
def work(c):
    try:
        return c, fetch_daily_daum(c, a.since)
    except Exception:
        return c, None
n = 0
with ThreadPoolExecutor(max_workers=6) as ex:
    for fut in as_completed([ex.submit(work, c) for c in codes]):
        c, rows = fut.result()
        if rows:
            con.executemany("INSERT OR REPLACE INTO daily VALUES(?,?,?,?,?,?)", rows); n += len(rows)
con.commit()
print("rows upserted:", n, "| min date:", con.execute("SELECT min(date) FROM daily").fetchone()[0])
