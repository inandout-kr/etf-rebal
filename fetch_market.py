# -*- coding: utf-8 -*-
"""시장 데이터 수집 (무료 소스, 로그인 불필요)

  1. 종목 유니버스  : 네이버 m.stock.naver.com/api/stocks/marketValue/{KOSPI,KOSDAQ}
  2. 일별 시세      : 다음 finance.daum.net/api/quote/A{code}/days  (종가·거래대금·상장주식수 일별)
                      실패 시 네이버 api.stock.naver.com/chart/domestic/item/{code}/day (종가·거래량)
  3. 종목 기본정보  : 다음 finance.daum.net/api/quotes/A{code} (상장일, WICS 업종, 외국인비율)
  4. WICS 섹터      : wiseindex.com GetIndexComponets (10개 대분류)

출력: data/market.sqlite
사용: python fetch_market.py [--since 20260401] [--skip-info]
"""
import argparse
import json
import math
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from market_dates import completed_session_date, current_universe, latest_completed_date

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).parent
DB = ROOT / "data" / "market.sqlite"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126"}
DAUM = {**UA, "Referer": "https://finance.daum.net/"}
WORKERS = 6
PREF_SUFFIX = ("우", "우B", "우C", "2우B", "3우B", "우(전환)")


def num(s):
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return s
    s = str(s).replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def is_common(code, name):
    """보통주 판별: 코드 끝자리 0 + 우선주 명칭 패턴 제외"""
    if not code.endswith("0"):
        return False
    return not any(name.endswith(suf) for suf in PREF_SUFFIX)


def fetch_universe():
    rows = {}
    duplicates = 0
    for mkt in ("KOSPI", "KOSDAQ"):
        page = 1
        while True:
            r = requests.get(
                f"https://m.stock.naver.com/api/stocks/marketValue/{mkt}?page={page}&pageSize=100",
                headers=UA, timeout=20)
            r.raise_for_status()
            j = r.json()
            stocks = j.get("stocks", [])
            for s in stocks:
                if s.get("stockEndType") != "stock":
                    continue
                row = {
                    "code": s["itemCode"], "name": s["stockName"].strip(), "market": mkt,
                    "close": num(s.get("closePrice")),
                    "mktcap": (num(s.get("marketValue")) or 0) * 1e8,   # 원 (marketValue 단위 = 억원)
                    "trdval": (num(s.get("accumulatedTradingValue")) or 0) * 1e6,   # 원 (단위 = 백만원)
                    "volume": num(s.get("accumulatedTradingVolume")),
                }
                old = rows.get(row["code"])
                if old is not None:
                    if (old["name"], old["market"]) != (row["name"], row["market"]):
                        raise ValueError(f"시장 유니버스 중복 코드의 이름/시장 불일치: {row['code']} "
                                         f"({old['name']}/{old['market']} → {row['name']}/{row['market']}); 기존 자료를 보존합니다")
                    duplicates += 1
                # 시총 순위가 페이지 요청 사이에 바뀌면 같은 종목이 다시 나타날 수 있다.
                # 동일 종목은 마지막 응답 시세를 사용하며, 종목 수 검증은 고유 코드 기준이다.
                rows[row["code"]] = row
            if not stocks or page * 100 >= int(j.get("totalCount", 0)):
                break
            page += 1
    if duplicates:
        print(f"universe: merged {duplicates} repeated pagination rows ({len(rows)} unique stocks)")
    return list(rows.values())


def validate_universe(rows, previous_count=0):
    """Reject a broken/paginated-empty response before changing the stock table."""
    codes = [r.get("code") for r in rows]
    if not rows:
        raise ValueError("시장 유니버스가 비었습니다; 기존 자료를 보존합니다")
    if len(set(codes)) != len(codes):
        raise ValueError(f"시장 유니버스 중복 코드: 전체 {len(codes)}행 / 고유 {len(set(codes))}종목; 기존 자료를 보존합니다")
    markets = {r.get("market") for r in rows}
    if markets != {"KOSPI", "KOSDAQ"}:
        raise ValueError(f"시장 유니버스 시장 누락/오류: {sorted(map(str, markets))}; 기존 자료를 보존합니다")
    if previous_count and len(rows) < previous_count * .9:
        raise ValueError(f"시장 유니버스 고유 {len(rows)}종목이 기존 {previous_count}종목의 90% 미만입니다; 기존 자료를 보존합니다")
    for r in rows:
        if not r.get("name") or not r.get("code") or not r.get("close") or not r.get("mktcap"):
            raise ValueError("시장 유니버스 필수 값이 누락되었습니다")
        for key in ("close", "mktcap", "trdval", "volume"):
            value = r.get(key)
            if value is not None and (not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
                raise ValueError(f"시장 유니버스 {key} 값 오류")


def store_daily_rows(con, code, rows, since, through=None):
    """Validate one response completely, then upsert; failed responses never erase history."""
    through = through or completed_session_date().strftime("%Y%m%d")
    staged = []
    seen = set()
    for row in rows or []:
        if len(row) != 6 or row[0] != code:
            raise ValueError(f"{code}: 잘못된 시세 행")
        dt = row[1]
        datetime.strptime(dt, "%Y%m%d")
        if not since <= dt <= through:
            continue
        if dt in seen:
            raise ValueError(f"{code}: 중복 시세 일자")
        for idx in range(2, 6):
            value = row[idx]
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{code}: 유효하지 않은 시세 값")
        if row[2] <= 0 or row[5] == 0:
            raise ValueError(f"{code}: 종가/상장주식수 오류")
        staged.append(row)
        seen.add(dt)
    if not staged:
        return False
    with con:
        con.executemany("INSERT OR REPLACE INTO daily VALUES(?,?,?,?,?,?)", staged)
    return True


def store_universe(con, rows, observed_at):
    """Commit validated membership with its stock rows; never delete departed history."""
    cursor = con.execute("SELECT * FROM stock")
    names = [col[0] for col in cursor.description]
    stocks = {r[0]: dict(zip(names, r)) for r in cursor}
    previous, _ = current_universe(con, stocks)
    validate_universe(rows, len(previous) or len(stocks))
    snapshot = {"version": 1, "observed_at": observed_at, "source": "Naver marketValue KOSPI/KOSDAQ",
                "codes": sorted(u["code"] for u in rows)}
    with con:
        for u in rows:
            con.execute(
                """INSERT INTO stock(code,name,market,is_common,close,mktcap,trdval,volume,updated) VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(code) DO UPDATE SET name=excluded.name,market=excluded.market,is_common=excluded.is_common,
                   close=excluded.close,mktcap=excluded.mktcap,trdval=excluded.trdval,volume=excluded.volume,updated=excluded.updated""",
                (u["code"], u["name"], u["market"], int(is_common(u["code"], u["name"])),
                 u["close"], u["mktcap"], u["trdval"], u["volume"], observed_at))
        con.execute("INSERT OR REPLACE INTO meta VALUES('current_universe',?)", (json.dumps(snapshot, ensure_ascii=False),))


def fetch_daily_daum(code, since):
    """다음: 최근 400영업일 (종가, 거래대금, 거래량, 상장주식수)"""
    r = requests.get(
        f"https://finance.daum.net/api/quote/A{code}/days?symbolCode=A{code}&page=1&perPage=400&pagination=true",
        headers=DAUM, timeout=20)
    if r.status_code != 200:
        return None
    out = []
    for d in r.json().get("data", []):
        dt = d["date"][:10].replace("-", "")
        if dt < since:
            continue
        out.append((code, dt, d.get("tradePrice"), d.get("accTradePrice"),
                    d.get("accTradeVolume"), d.get("listedSharesCount")))
    return out


def fetch_daily_naver(code, since, shares):
    r = requests.get(
        f"https://api.stock.naver.com/chart/domestic/item/{code}/day?startDateTime={since}0000&endDateTime={date.today():%Y%m%d}0000",
        headers=UA, timeout=20)
    if r.status_code != 200:
        return None
    out = []
    for d in r.json():
        close = d.get("closePrice")
        vol = d.get("accumulatedTradingVolume")
        out.append((code, d["localDate"], close, (close or 0) * (vol or 0), vol, shares))
    return out


def fetch_info_daum(code):
    r = requests.get(f"https://finance.daum.net/api/quotes/A{code}?summary=false&changeStatistics=true",
                     headers=DAUM, timeout=20)
    if r.status_code != 200:
        return None
    j = r.json()
    return (code, j.get("listingDate"), j.get("wicsSectorName"), j.get("sectorName"),
            j.get("foreignRatio"), j.get("listedShareCount"))


def fetch_wics():
    """WICS 대분류 (최근 7일 중 데이터가 있는 가장 최근 일자 사용)"""
    out = {}
    for back in range(0, 8):
        dt = f"{date.today() - timedelta(days=back):%Y%m%d}"
        for sec in ["G10", "G15", "G20", "G25", "G30", "G35", "G40", "G45", "G50", "G55"]:
            url = f"https://www.wiseindex.com/Index/GetIndexComponets?ceil_yn=0&dt={dt}&sec_cd={sec}"
            try:
                j = requests.get(url, headers=UA, timeout=30).json()
            except Exception:
                continue
            for x in j.get("list") or []:
                out[x["CMP_CD"]] = (sec, x["SEC_NM_KOR"])
        if out:
            print(f"  WICS as of {dt}")
            break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="20260401")
    ap.add_argument("--skip-info", action="store_true")
    ap.add_argument("--only-wics", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    DB.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB)
    con.executescript("""
      CREATE TABLE IF NOT EXISTS stock(code TEXT PRIMARY KEY, name TEXT, market TEXT, is_common INT,
        close REAL, mktcap REAL, trdval REAL, volume REAL, listing_date TEXT, wics_mid TEXT, krx_sector TEXT,
        foreign_ratio REAL, shares REAL, wics_sec TEXT, wics_sec_name TEXT, updated TEXT);
      CREATE TABLE IF NOT EXISTS daily(code TEXT, date TEXT, close REAL, trdval REAL, volume REAL, shares REAL,
        PRIMARY KEY(code, date));
      CREATE INDEX IF NOT EXISTS ix_daily_date ON daily(date);
      CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
    """)

    if a.only_wics:
        wics = fetch_wics()
        for code, (sec, nm) in wics.items():
            con.execute("UPDATE stock SET wics_sec=?, wics_sec_name=? WHERE code=?", (sec, nm, code))
        con.commit(); con.close()
        print(f"WICS sectors: {len(wics)}")
        return

    uni = fetch_universe()
    n_kospi = sum(1 for u in uni if u["market"] == "KOSPI")
    print(f"universe: {len(uni)} (KOSPI {n_kospi}, KOSDAQ {len(uni) - n_kospi})")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    store_universe(con, uni, now)

    wics = fetch_wics()
    print(f"WICS sectors: {len(wics)}")
    for code, (sec, nm) in wics.items():
        con.execute("UPDATE stock SET wics_sec=?, wics_sec_name=? WHERE code=?", (sec, nm, code))
    con.commit()

    codes = [u["code"] for u in uni if is_common(u["code"], u["name"])]
    shares_now = {u["code"]: (u["mktcap"] / u["close"] if u["close"] else None) for u in uni}

    print(f"daily: {len(codes)} common stocks since {a.since} ...")
    fails = []
    sources = {"daum": 0, "naver": 0}
    through = completed_session_date().strftime("%Y%m%d")

    def work(code):
        rows = None
        source = "daum"
        try:
            rows = fetch_daily_daum(code, a.since)
        except Exception:
            rows = None
        if not rows:
            source = "naver"
            try:
                rows = fetch_daily_naver(code, a.since, shares_now.get(code))
            except Exception:
                rows = None
        return code, rows, source

    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for fut in as_completed([ex.submit(work, c) for c in codes]):
            code, rows, source = fut.result()
            try:
                saved = store_daily_rows(con, code, rows, a.since, through)
            except (ValueError, TypeError):
                saved = False
            if saved:
                sources[source] += 1
            else:
                fails.append(code)
            done += 1
            if done % 300 == 0:
                con.commit()
                print(f"  {done}/{len(codes)} ({time.time() - t0:.0f}s)")
    con.commit()
    print(f"daily done, fails={len(fails)} {fails[:10]}")

    info_fails = 0
    if not a.skip_info:
        print("info (listing date / WICS mid) ...")
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            for fut in as_completed([ex.submit(fetch_info_daum, c) for c in codes]):
                try:
                    r = fut.result()
                except Exception:
                    r = None
                if r and any(value is not None for value in r[1:]):
                    con.execute("""UPDATE stock SET listing_date=COALESCE(?,listing_date), wics_mid=COALESCE(?,wics_mid),
                                   krx_sector=COALESCE(?,krx_sector), foreign_ratio=COALESCE(?,foreign_ratio),
                                   shares=COALESCE(?,shares) WHERE code=?""",
                                (r[1], r[2], r[3], r[4], r[5], r[0]))
                else:
                    info_fails += 1
        con.commit()

    last = latest_completed_date(con)
    collection = {"attempted_at": now, "as_of": last, "status": "warning" if fails else "ok",
                  "requested": len(codes), "succeeded": len(codes) - len(fails),
                  "failed_codes": fails, "sources": sources, "info_failed": info_fails,
                  "info_skipped": a.skip_info, "wics_matched": len(wics)}
    con.execute("INSERT OR REPLACE INTO meta VALUES('market_collection',?)",
                (json.dumps(collection, ensure_ascii=False),))
    con.execute("INSERT OR REPLACE INTO meta VALUES('last_daily',?)", (last,))
    con.execute("INSERT OR REPLACE INTO meta VALUES('updated',?)", (now,))
    con.commit()
    con.close()
    print(f"done in {time.time() - t0:.0f}s, last daily = {last}")


if __name__ == "__main__":
    main()
