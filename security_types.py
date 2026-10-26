"""Security type metadata from KIS public masters, with identity-checked fallback.

Only the published fixed-width security-group and preferred-share fields are
used. Names and ticker suffixes never infer a share class.
"""
import hashlib
import io
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path

MASTER_URLS = {m: f"https://new.real.download.dws.co.kr/common/master/{m.lower()}_code.mst.zip"
               for m in ("KOSPI", "KOSDAQ")}
SCHEMA_URLS = {m: f"https://github.com/koreainvestment/open-trading-api/blob/main/stocks_info/kis_{m.lower()}_code_mst.py"
               for m in MASTER_URLS}
# Widths are transcribed from the KIS schema. Its 228/222 tail slices include LF.
FIELD_WIDTHS = {
    "KOSPI": (2,1,4,4,4,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,9,5,5,1,
              1,1,2,1,1,1,2,2,2,3,1,3,12,12,8,15,21,2,7,1,1,1,1,1,9,9,9,5,9,8,9,3,1,1,1),
    "KOSDAQ": (2,1,4,4,4,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,9,5,5,1,1,1,2,
               1,1,1,2,2,2,3,1,3,12,12,8,15,21,2,7,1,1,1,1,9,9,9,5,9,8,9,3,1,1,1),
}
PREFERRED_FIELD = {"KOSPI": 54, "KOSDAQ": 49}


def parse_master_zip(blob, market):
    """Validate every fixed-width line before accepting any parsed records."""
    if market not in FIELD_WIDTHS or not isinstance(blob, bytes) or len(blob) > 16_000_000:
        raise ValueError("Unsupported or oversized security master")
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        expected = f"{market.lower()}_code.mst"
        if archive.namelist() != [expected] or archive.getinfo(expected).file_size > 16_000_000:
            raise ValueError("Unexpected security master archive contents")
        raw = archive.read(expected)
    widths = FIELD_WIDTHS[market]
    tail_width = sum(widths)
    preferred_offset = sum(widths[:PREFERRED_FIELD[market]])
    result, seen = [], set()
    for number, line in enumerate(raw.splitlines(), 1):
        if len(line) != 61 + tail_width:
            raise ValueError(f"Security master line width mismatch ({market}, row {number})")
        prefix, tail = line[:61], line[61:]
        try:
            code = prefix[:9].decode("ascii").strip()
            isin = prefix[9:21].decode("ascii").strip()
            name = prefix[21:61].decode("cp949").strip()
            tail.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValueError("Security master encoding mismatch") from exc
        if (not re.fullmatch(r"[0-9A-Z]{6,9}", code)
                or not re.fullmatch(r"[0-9A-Z]{12}", isin) or not name or code in seen):
            raise ValueError("Security master invalid or duplicate identity")
        group = tail[:2].decode("ascii").strip()
        preferred = tail[preferred_offset:preferred_offset + 1].decode("ascii").strip()
        if (not re.fullmatch(r"[A-Z]{2}", group) or not re.fullmatch(r"[0-9]?", preferred)
                or (group == "ST" and len(code) != 6)):
            raise ValueError("Security master type fields changed; schema confirmation required")
        result.append(dict(code=code, name=name, market=market, isin=isin,
                           security_group=group, preferred_code=preferred))
        seen.add(code)
    if not result:
        raise ValueError("Empty security master")
    return result


def validate_metadata(payload):
    """Return a validated code map; no partial or duplicate cache acceptance."""
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("Security type schema version unavailable")
    observed = payload.get("observed_at")
    if not isinstance(observed, str):
        raise ValueError("Security type observation timestamp unavailable")
    datetime.fromisoformat(observed)
    sources = payload.get("sources")
    if not isinstance(sources, dict) or set(sources) != set(MASTER_URLS):
        raise ValueError("Security type market provenance incomplete")
    for market, source in sources.items():
        if (not isinstance(source, dict) or source.get("url") != MASTER_URLS[market]
                or source.get("schema_url") != SCHEMA_URLS[market]
                or not re.fullmatch(r"[0-9a-f]{64}", source.get("sha256") or "")):
            raise ValueError("Security type provenance invalid")
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("Security type records unavailable")
    result, markets = {}, set()
    for row in records:
        if not isinstance(row, dict):
            raise ValueError("Security type record must be an object")
        code = row.get("code")
        if (not isinstance(code, str) or not re.fullmatch(r"[0-9A-Z]{6,9}", code)
                or code in result or row.get("market") not in MASTER_URLS
                or not isinstance(row.get("name"), str) or not row["name"].strip()
                or not re.fullmatch(r"[0-9A-Z]{12}", row.get("isin") or "")
                or not re.fullmatch(r"[A-Z]{2}", row.get("security_group") or "")
                or (row.get("security_group") == "ST" and len(code) != 6)
                or not isinstance(row.get("preferred_code"), str) or not re.fullmatch(r"[0-9]?", row["preferred_code"])):
            raise ValueError("Security type identity, type or duplicate validation failed")
        result[code] = row
        markets.add(row["market"])
    if markets != set(MASTER_URLS):
        raise ValueError("Security type records missing a market")
    return result


def common_decision(code, name, market, metadata):
    """True/False only for matching identity and explicit published type fields."""
    row = metadata.get(code) if isinstance(metadata, dict) else None
    if not row or row.get("market") != market or row.get("name", "").strip() != name.strip():
        return None
    group, preferred = row.get("security_group"), row.get("preferred_code")
    if group == "ST":
        return preferred == "0" if isinstance(preferred, str) and re.fullmatch(r"[0-9]", preferred) else None
    return False if isinstance(group, str) and re.fullmatch(r"[A-Z]{2}", group) else None


def refresh_metadata(path, observed_at, get):
    """Two public downloads per refresh; any failure preserves the verified cache."""
    path = Path(path)
    cached, cached_map = None, {}
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        cached_map = validate_metadata(cached)
    except (OSError, ValueError, TypeError):
        cached = None
    try:
        records, sources = [], {}
        for market, url in MASTER_URLS.items():
            response = get(url, timeout=30)
            response.raise_for_status()
            blob = response.content
            records.extend(parse_master_zip(blob, market))
            sources[market] = dict(url=url, sha256=hashlib.sha256(blob).hexdigest(), schema_url=SCHEMA_URLS[market])
        payload = dict(schema_version=1, observed_at=observed_at, sources=sources, records=records)
        metadata = validate_metadata(payload)
        # A single incomplete market file must not silently replace a complete cache.
        if cached_map and len(metadata) < len(cached_map) * .9:
            raise ValueError("Security type record count unexpectedly shrank")
        staged = path.with_name(path.name + ".pending")
        staged.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        staged.replace(path)
        return metadata, dict(status="ok", observed_at=observed_at, records=len(metadata), warnings=[])
    except Exception as exc:
        # Source exception strings can contain local paths; publish only its type.
        quality = dict(status="fallback" if cached else "unavailable",
                       observed_at=cached.get("observed_at") if cached else None,
                       records=len(cached_map), warnings=[f"종목종류 메타데이터 갱신 실패 ({type(exc).__name__}); 검증 캐시 또는 기존 DB 판정 보존"])
        return cached_map, quality


def metadata_quality(path, stocks, codes, now, collection_quality=None):
    """Public summary distinguishes current reference metadata from price dates."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        metadata = validate_metadata(payload)
        observed = datetime.fromisoformat(payload["observed_at"])
        age_days = (now.date() - observed.date()).days
        if age_days < 0:
            raise ValueError("Security type observation is in the future")
    except (OSError, ValueError, TypeError):
        return {"status": "unavailable", "observed_at": None, "age_days": None,
                "verified_universe_rows": 0, "unverified_count": len(codes),
                "warnings": ["종목종류 메타데이터 검증 불가; 기존 DB 판정이며 공식 보통주 여부 확인 필요"]}
    unverified = [{"code": c, "name": stocks[c]["name"]} for c in sorted(codes)
                  if c in stocks and common_decision(c, stocks[c]["name"], stocks[c]["market"], metadata) is None]
    warnings = []
    if unverified:
        warnings.append(f"종목종류 메타데이터와 신원·분류 미확인 {len(unverified)}개; 기존 DB 판정 또는 신규 HOLD")
    if age_days:
        warnings.append(f"종목종류 참조자료 관측 {age_days}일 경과; 최신 분류 재확인 필요")
    if isinstance(collection_quality, str):
        try:
            collection_quality = json.loads(collection_quality)
        except ValueError:
            collection_quality = None
    collection_status = collection_quality.get("status") if isinstance(collection_quality, dict) else None
    if collection_status in ("fallback", "unavailable", "warning"):
        warnings.append("최근 종목종류 수집은 검증 캐시 또는 기존 DB 판정을 사용했습니다.")
    return {"status": "warning" if warnings else "ok", "observed_at": payload["observed_at"],
            "age_days": age_days, "scope": "current_reference_metadata", "records": len(metadata),
            "sources": payload["sources"], "verified_universe_rows": len(codes) - len(unverified),
            "unverified_count": len(unverified), "unverified": unverified,
            "collection_status": collection_status, "warnings": warnings}
