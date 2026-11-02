"""Collect current KRICS level1/2 membership from the public KRX Index page.

The query date controls quote fields, not historical classification membership.
No classification effective date is supplied by this endpoint.
"""
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import requests

from classification_scenarios import KRICS_SECTORS, stable_hash
from market_dates import anchor_stocks, current_universe, korea_now, latest_completed_date

PAGE = "/contents/MKD/03/0303/03030304/MKD03030304.jsp"
PAGE_URL = "https://index.krx.co.kr" + PAGE
ENDPOINT = "https://index.krx.co.kr/contents/IDX/99/IDX99000001.jspx"
BLD = "/IDX/03/0303/03030304/mkd03030304_"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": PAGE_URL, "X-Requested-With": "XMLHttpRequest"}
SECTOR_ALIASES = {"미디어·콘텐츠": "미디어 및 콘텐츠"}


def day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}", value):
        raise ValueError("Invalid KRICS query date")
    return datetime.strptime(value, "%Y%m%d").date()


def fetch_block(session, kind, group, requested_date):
    bld = BLD + kind
    otp_response = session.get("https://index.krx.co.kr/contents/COM/GenerateOTP.jspx",
                               params={"bld": bld, "name": "form"}, headers=HEADERS, timeout=30)
    otp_response.raise_for_status()
    otp = otp_response.text.strip()
    if not otp or len(otp) > 512 or "<" in otp:
        raise ValueError("KRX public request token unavailable")
    response = session.post(ENDPOINT, headers=HEADERS, timeout=60,
                            data={"mkt_tp_cd": "ALL", "krics_grp_cd": group, "date": requested_date,
                                  "lang": "ko", "pagePath": PAGE, "code": otp, "bldcode": bld})
    response.raise_for_status()
    return response.json()


def block(payload):
    rows = payload.get("block1") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise ValueError("KRX response block missing or invalid")
    return rows


def options(payload):
    sectors, groups = {}, {}
    for row in block(payload):
        code, label = row.get("value"), row.get("label")
        if not isinstance(code, str) or not isinstance(label, str):
            raise ValueError("KRICS option identity missing")
        if re.fullmatch(r"\d{2}", code):
            match = re.fullmatch(r"(.+)\((\d{2})\)", label)
            if not match or match[2] != code or code in sectors:
                raise ValueError("KRICS sector option invalid or duplicate")
            sectors[code] = match[1]
        elif re.fullmatch(r"\d{4}", code):
            if code in groups:
                raise ValueError("KRICS group option duplicated")
            groups[code] = label
        else:
            raise ValueError("KRICS option code changed")
    if len(sectors) != 9 or {SECTOR_ALIASES.get(s, s) for s in sectors.values()} != KRICS_SECTORS or len(groups) != 24:
        raise ValueError("KRICS level1/2 option coverage incomplete")
    for code, label in list(groups.items()):
        prefix = sectors.get(code[:2])
        suffix = f"({code[:2]}-{code[2:]})"
        if not prefix or not label.startswith(prefix + "-") or not label.endswith(suffix):
            raise ValueError("KRICS group parent or label mismatch")
        groups[code] = label[len(prefix) + 1:-len(suffix)]
    if {g[:2] for g in groups} != set(sectors):
        raise ValueError("KRICS sector has no child groups")
    return {code: SECTOR_ALIASES.get(name, name) for code, name in sectors.items()}, groups


def members(payload):
    result = {}
    for row in block(payload):
        code, name = row.get("isu_cd"), row.get("isu_abbr")
        if (not isinstance(code, str) or not re.fullmatch(r"[0-9A-Z]{6}", code)
                or not isinstance(name, str) or not name.strip() or code in result):
            raise ValueError("KRICS membership invalid or duplicated")
        result[code] = name.strip()
    return result


def checked_members(fetch, group, requested_date):
    summary = block(fetch("02", group, requested_date))
    if len(summary) != 1 or not isinstance(summary[0].get("isu_cnt"), str):
        raise ValueError("KRICS count summary unavailable")
    text = summary[0]["isu_cnt"].replace(",", "")
    if not text.isdigit():
        raise ValueError("KRICS count summary invalid")
    rows = members(fetch("03", group, requested_date))
    if len(rows) != int(text):
        raise ValueError(f"KRICS group {group} response count mismatch")
    return rows


def collect_snapshot(fetch, requested_date, observed_at, stocks, expected_codes):
    observed = datetime.fromisoformat(observed_at)
    if observed.tzinfo is None or day(requested_date) > korea_now(observed).date():
        raise ValueError("KRICS observation/query date invalid")
    if not expected_codes or not stocks:
        raise ValueError("KRICS review universe unavailable")
    response_hashes = {}
    def read(kind, group, date):
        value = fetch(kind, group, date)
        response_hashes[f"{kind}:{group}"] = stable_hash(value)
        return value
    option_response = read("01", "1010", requested_date)
    sectors, groups = options(option_response)
    records, sector_codes, group_counts = {}, {s: set() for s in sectors}, {}
    for group in sorted(groups):
        rows = checked_members(read, group, requested_date)
        group_counts[group] = len(rows)
        for code, name in rows.items():
            if code in records:
                raise ValueError("KRICS membership duplicated across groups")
            records[code] = {"code": code, "name": name, "sector_code": group[:2],
                             "sector": sectors[group[:2]], "group_code": group, "group": groups[group]}
            sector_codes[group[:2]].add(code)
    for sector in sorted(sectors):
        control = checked_members(read, sector, requested_date)
        if set(control) != sector_codes[sector] or any(control[c] != records[c]["name"] for c in control):
            raise ValueError("KRICS level1 membership differs from level2 union")
    missing = sorted(set(expected_codes) - set(records))
    if not records:
        raise ValueError("KRICS complete response contains no membership")
    if missing:
        raise ValueError("KRICS current review coverage missing: " + ", ".join(missing))
    identities = {c: r for c, r in stocks.items() if r.get("universe_active", True)}
    mismatched = [c for c in set(records) & set(expected_codes)
                  if c not in identities or identities[c]["name"].strip() != records[c]["name"]]
    if mismatched:
        raise ValueError("KRICS review security identity mismatch: " + ", ".join(sorted(mismatched)))
    for code, record in records.items():
        record["market"] = identities.get(code, {}).get("market")
    content = sorted(records.values(), key=lambda r: r["code"])
    snapshot_id = stable_hash({"observed_at": observed_at, "requested_date": requested_date, "records": content})
    sources = {}
    for key, market in (("kospi200", "KOSPI"), ("kosdaq150", "KOSDAQ")):
        subset = [r for r in content if r["market"] == market]
        if not subset:
            raise ValueError("KRICS market membership unavailable")
        mapping = [[r["code"], r["sector"]] for r in subset]
        sources[key] = {"source_id": "krx_public_krics_level1_2", "source_filename": "KRX public current KRICS snapshot",
                        "source_read_location": PAGE_URL, "source_sha256": stable_hash(mapping),
                        "source_hash_basis": "canonical exported code-sector pairs",
                        "expected_records": len(mapping), "mapping": mapping,
                        "level2_mapping": [[r["code"], r["group_code"], r["group"]] for r in subset]}
    return {"schema_version": 2, "mode": "official_current_snapshot", "snapshot_id": snapshot_id,
            "requested_date": requested_date, "observed_at": observed_at, "returned_as_of": None,
            "historical_asof_supported": False, "valid_from": None, "valid_to": None,
            "records": content, "sources": sources,
            "validation": {"sector_count": len(sectors), "group_count": len(groups), "group_counts": group_counts,
                           "official_options": block(option_response),
                           "response_sha256": response_hashes,
                           "response_hash_basis": "canonical JSON response, quotes included; membership effective date unavailable",
                           "level1_union_verified": True, "summary_counts_verified": True,
                           "official_records": len(content), "review_codes_checked": sorted(expected_codes),
                           "unassigned_market_codes": [r["code"] for r in content if r["market"] not in ("KOSPI", "KOSDAQ")]}}


def atomic_json(path, payload):
    path = Path(path)
    pending = path.with_name(path.name + ".pending")
    pending.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    pending.replace(path)


def refresh_mapping(mapping_path, status_path, fetch, requested_date, observed_at, stocks, expected_codes):
    try:
        payload = collect_snapshot(fetch, requested_date, observed_at, stocks, expected_codes)
        atomic_json(mapping_path, payload)
        status = {"schema_version": 1, "status": "ok", "attempted_at": observed_at,
                  "requested_date": requested_date, "snapshot_id": payload["snapshot_id"], "errors": []}
    except Exception as exc:
        status = {"schema_version": 1, "status": "HOLD", "attempted_at": observed_at,
                  "requested_date": requested_date, "snapshot_id": None,
                  "errors": [f"KRICS 공식 수집·전체 검증 실패 ({type(exc).__name__}); 이전 검증 캐시 보존"]}
    atomic_json(status_path, status)
    return status


def expected_review_codes(con, stocks, holdings, as_of, now):
    # Reuse the existing eligibility and review-period contract, never author forecasts.
    from analyze import adjust_review_periods, eligible, load_review_events, period_stats
    from calendar_events import review_windows
    windows = review_windows(now.date())
    expected = set()
    for key, market in (("kospi200", "KOSPI"), ("kosdaq150", "KOSDAQ")):
        win = windows[key]
        stats = period_stats(con, win["period_start"], win["period_end"], as_of)
        stats = adjust_review_periods(con, stats, win["period_start"], win["period_end"], as_of, load_review_events())
        review_as_of = min(as_of, win["ref_date"], win["period_end"])
        review_prices = anchor_stocks(con, stocks, review_as_of) if as_of > review_as_of else None
        current = {r["code"] for r in holdings[key]["rows"] if r.get("code")}
        for code, st in stocks.items():
            if st["market"] != market or not st["is_common"]:
                continue
            review_st = st if review_prices is None else dict(st, review_ready=st.get("universe_active", True)
                                                              and review_prices[code]["price_status"] == "current")
            if (eligible(review_st, win["ref_date"], is_cur=code in current)[0]
                    and stats.get(code, {}).get("avg_mcap") and stats[code].get("avg_trdval")):
                expected.add(code)
    return expected


def run_collection():
    root = Path(__file__).resolve().parent
    data = root / "data"
    con = sqlite3.connect(data / "market.sqlite")
    con.row_factory = sqlite3.Row
    stocks = {r["code"]: dict(r) for r in con.execute("SELECT * FROM stock")}
    meta = {k: v for k, v in con.execute("SELECT k,v FROM meta")}
    now = korea_now()
    as_of = latest_completed_date(con, now)
    codes, quality = current_universe(con, stocks, meta)
    if quality["status"] == "unavailable":
        raise ValueError("Current market universe unavailable; KRICS cache not changed")
    stocks = anchor_stocks(con, stocks, as_of, current=True, metadata=meta)
    holdings = json.loads((data / "holdings.json").read_text(encoding="utf-8"))
    expected = expected_review_codes(con, stocks, holdings, as_of, now)
    con.close()
    session = requests.Session()
    status = refresh_mapping(data / "krics_mapping.json", data / "krics_collection.json",
                             lambda kind, group, date: fetch_block(session, kind, group, date),
                             as_of, now.isoformat(), stocks, expected)
    print("KRICS", status["status"], "query quotes", as_of, "classification observed", now.isoformat())
    # A durably recorded HOLD is a valid pipeline state. An unwritten status
    # must raise and stop the pipeline rather than reusing an old same-day ok.
    return 0


def main():
    try:
        return run_collection()
    except Exception as exc:
        # Setup/DB/eligibility failures must also invalidate a prior same-day
        # success, while retaining its fully validated mapping bytes.
        status = {"schema_version": 1, "status": "HOLD", "attempted_at": korea_now().isoformat(),
                  "requested_date": None, "snapshot_id": None,
                  "errors": [f"KRICS 입력 준비·수집 실패 ({type(exc).__name__}); 이전 검증 캐시 보존"]}
        atomic_json(Path(__file__).resolve().parent / "data" / "krics_collection.json", status)
        print("KRICS HOLD; input preparation or collection failed")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
