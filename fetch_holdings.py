# -*- coding: utf-8 -*-
"""대표(프록시) ETF의 현재 구성종목(PDF) 수집 → 지수 현재 구성종목 추정

소스: wisereport ETF 페이지 내장 CU_data (종목명·수량·비중, 로그인 불필요)
종목명 → 종목코드 매핑은 data/market.sqlite 의 stock 테이블 사용.

출력: data/holdings.json  { etf_code: {"name":..., "date":..., "rows":[{"name","code","weight","count"}]} }
"""
import json
import math
import re
import sqlite3
import sys
from datetime import datetime
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
    "krx_semi_tiger": "091230",  # TIGER 반도체
    "krx_semi_lev": "494310",  # KODEX 반도체레버리지
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
    "fn_aitop2_sol": "0167A0", # SOL AI반도체TOP2플러스
    "wise_battery": "305540",  # TIGER 2차전지테마
    "is_power": "487240",      # KODEX AI전력핵심설비
    "kedi_power": "0117V0",    # TIGER 코리아AI전력기기TOP3플러스
    "fn_semitop10_lev": "488080",  # TIGER 반도체TOP10레버리지
    "kospi200_it_lev": "243880",   # TIGER 200IT레버리지
    "kosdaq150_lev": "233740",     # KODEX 코스닥150레버리지
    "kospi200_lev": "122630",      # KODEX 레버리지
    "kospi200_tiger": "102110",    # TIGER 200
    "kosdaq150_tiger": "232080",   # TIGER 코스닥150
}
CASH = ("현금", "예금", "설정현금", "USD", "달러")


def fetch_cu(session, code):
    r = session.get(f"https://navercomp.wisereport.co.kr/v2/ETF/index.aspx?cmp_cd={code}", headers=UA, timeout=20)
    r.raise_for_status()
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


def validate_holdings(entry, previous=None):
    """Cash is omitted; leveraged ETF futures can have null weights."""
    rows = entry.get("rows") or []
    if not rows or not entry.get("etf_code") or not entry.get("etf_name"):
        raise ValueError("비어 있거나 잘못된 ETF 구성 응답")
    datetime.strptime(entry.get("date") or "", "%Y-%m-%d")
    if previous and len(rows) < len(previous.get("rows", [])) * .5:
        raise ValueError("기존 구성의 절반 미만인 응답")
    total, weighted, codes = 0, 0, set()
    for row in rows:
        if not row.get("name") or row.get("date") != entry["date"]:
            raise ValueError("구성종목 이름/기준일 오류")
        if row.get("code"):
            if row["code"] in codes:
                raise ValueError("구성종목 코드 중복")
            codes.add(row["code"])
        for key in ("weight", "count"):
            value = row.get(key)
            if value is None and key == "weight" and not row.get("code"):
                continue
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"구성종목 {key} 오류")
        if row.get("weight") is not None:
            total += row["weight"]
            weighted += 1
    if not codes or not weighted or not 50 <= total <= 250:
        raise ValueError("주식 구성/비중 합계 오류")
    if previous and previous.get("date", "") > entry["date"]:
        raise ValueError("기존 자료보다 과거 기준일인 응답")


def collect_holdings(session, by_name, previous, proxies=None, fetcher=fetch_cu):
    out = dict(previous)
    status = {}
    for key, code in (proxies or PROXY).items():
        try:
            name, rows = fetcher(session, code)
            for row in rows or []:
                cands = by_name.get(row["name"]) or []
                common = [c for c in cands if c[0].endswith("0")]
                row["code"] = (common or cands)[0][0] if cands else None
            entry = {"etf_code": code, "etf_name": name, "date": rows[0].get("date") if rows else None, "rows": rows}
            validate_holdings(entry, previous.get(key))
            out[key] = entry
            status[key] = {"status": "ok", "as_of": entry["date"], "rows": len(rows)}
        except (requests.RequestException, ValueError, TypeError, KeyError, IndexError) as exc:
            status[key] = {"status": "cached" if key in previous else "missing",
                           "as_of": previous.get(key, {}).get("date"), "error": str(exc)}
        print(f"  {key:20s} {status[key]['status']} as of {status[key].get('as_of')}")
    return out, {"attempted_at": datetime.now().isoformat(timespec="seconds"), "sources": status,
                 "warning_count": sum(s["status"] != "ok" for s in status.values())}


def atomic_json(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=1, allow_nan=False), encoding="utf-8")
    temp.replace(path)


def main():
    con = sqlite3.connect(ROOT / "data" / "market.sqlite")
    by_name = {}
    for code, name, mkt in con.execute("SELECT code, name, market FROM stock"):
        by_name.setdefault(name, []).append((code, mkt))
    con.close()
    path = ROOT / "data" / "holdings.json"
    previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    with requests.Session() as session:
        out, metadata = collect_holdings(session, by_name, previous)
    atomic_json(ROOT / "data" / "holdings_collection.json", metadata)
    if any(s["status"] == "missing" for s in metadata["sources"].values()):
        raise RuntimeError("필수 ETF 구성 수집 실패: 기존 holdings.json 보존")
    atomic_json(path, out)
    print("saved data/holdings.json")


if __name__ == "__main__":
    main()
