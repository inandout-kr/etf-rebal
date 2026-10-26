"""Attachment-derived KRICS sensitivity on the exact saved GICS review rows.

This module never treats the attachment forecast as a target portfolio and does
not infer an effective date. Invalid joins suppress the comparison, not the
independently calculated GICS baseline.
"""
import copy
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

KRICS_SECTORS = frozenset(("에너지화학", "소재", "산업재", "모빌리티", "정보기술",
                           "금융 및 부동산", "소비재", "헬스케어", "미디어 및 콘텐츠"))
DERIVED_FIELDS = frozenset(("trd_rank", "liq_ok", "mcap_rank", "cum_share", "primary", "n_sector",
                            "n_exist", "rank_ratio", "status", "note", "confidence", "conf_note",
                            "discretionary_action", "mcap15_rank"))


def stable_hash(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_mapping(path):
    """Return data plus a load error so a missing comparison cannot erase GICS."""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("KRICS 매핑 최상위 객체가 아닙니다.")
        return value, None
    except (OSError, ValueError, TypeError) as exc:
        # This message is serialized to the public site; OS errors can contain a user path.
        return {}, f"KRICS 매핑 읽기 실패 ({type(exc).__name__}): 분류 입력을 확인하세요."


def validate_mapping(payload, index_key, rows, as_of):
    errors = []
    if payload.get("schema_version") != 1:
        errors.append("KRICS 매핑 형식 버전 확인 필요")
    if payload.get("frozen_as_of") != as_of:
        errors.append(f"고정 입력일 불일치: 매핑 {payload.get('frozen_as_of')}, 심사 {as_of}")
    sources = payload.get("sources", {})
    source = sources.get(index_key) if isinstance(sources, dict) else None
    if not isinstance(source, dict):
        source = {}
        errors.append(f"{index_key} KRICS 출처 없음")
    if any(not source.get(field) for field in ("source_id", "source_filename", "source_read_location", "source_sha256")):
        errors.append("KRICS 매핑 출처·추출본 해시 확인 필요")
    entries = source.get("mapping", [])
    mapping, duplicates, invalid = {}, [], []
    if not isinstance(entries, list):
        entries = []
        errors.append("KRICS 매핑 행 목록 없음")
    for entry in entries:
        if not isinstance(entry, list) or len(entry) != 2:
            invalid.append(repr(entry))
            continue
        code, sector = entry
        if (not isinstance(code, str) or not re.fullmatch(r"[0-9A-Z]{6}", code)
                or not isinstance(sector, str) or sector not in KRICS_SECTORS):
            invalid.append(repr(entry))
            continue
        if code in mapping:
            duplicates.append(code)
        mapping[code] = sector
    expected = source.get("expected_records")
    if expected != {"kospi200": 826, "kosdaq150": 1733}.get(index_key):
        errors.append("검토된 전체 KRICS 종목 수와 출처 메타데이터 불일치")
    if not isinstance(expected, int) or expected <= 0 or len(entries) != expected or len(mapping) != expected:
        errors.append(f"KRICS 전체 매핑 건수 불일치: 행 {len(entries)}, 고유 {len(mapping)}, 기대 {expected}")
    if duplicates:
        errors.append("KRICS 중복 종목코드: " + ", ".join(sorted(set(duplicates))))
    if invalid:
        errors.append(f"KRICS 코드·산업군 형식 오류 {len(invalid)}건")
    if len(set(mapping.values())) != 9:
        errors.append("KRICS 9개 산업군 확인 필요")
    row_codes = [row.get("code") for row in rows]
    row_duplicates = sorted(c for c, n in Counter(row_codes).items() if n > 1)
    if row_duplicates:
        errors.append("심사 입력 중복 종목코드: " + ", ".join(row_duplicates))
    missing = sorted(c for c in row_codes if c not in mapping)
    if missing:
        errors.append("KRICS 분류 누락: " + ", ".join(missing))
    coverage = {"review_rows": len(rows), "mapped_rows": len(rows) - len(missing),
                "missing_codes": missing, "duplicate_mapping_codes": sorted(set(duplicates)),
                "duplicate_review_codes": row_duplicates, "mapping_records": len(entries),
                "unique_mapping_codes": len(mapping), "expected_records": expected,
                "sector_count": len(set(mapping.values())), "invalid_records": len(invalid)}
    return mapping, source, coverage, errors


def classification_comparison(base_index, cfg, payload, selector, load_error=None):
    """Reclassify, then rerun every rank, buffer and final-count adjustment.

    selector is the same pure selection function used for the GICS calculation.
    All original eligibility, price, turnover and corporate-event observations
    stay fixed; neither author forecast labels nor author exclusions are inputs.
    """
    base = {k: copy.deepcopy(v) for k, v in base_index.items() if k != "classification_scenarios"}
    rows = base["all"]
    key, as_of = base["key"], base["data_through"]
    mapping, source, coverage, errors = validate_mapping(payload, key, rows, as_of)
    if load_error:
        errors.insert(0, load_error)
    source_metadata = {k: copy.deepcopy(v) for k, v in source.items() if k != "mapping"}
    input_fields = ("code", "name", "avg_mcap", "avg_trdval", "is_cur", "n_days", "review_start", "review_end",
                    "review_events", "market_mcap_rank", "mcap15", "mcap15_n_days", "listing_date")
    frozen_input = [{k: row.get(k) for k in input_fields} for row in sorted(rows, key=lambda r: r["code"])]
    baseline_selected = {row["code"] for row in rows if (row.get("status") or "").startswith(("유지", "신규편입"))}
    result = {"status": "HOLD" if errors else "ready", "data_basis": "same_frozen_review_rows", "as_of": as_of,
              "input_hash": stable_hash(frozen_input), "mapping_hash": stable_hash(source.get("mapping", [])),
              "source": source_metadata, "coverage": coverage, "effective_date_status": "HOLD",
              "official_result": False, "hold_reasons": errors, "scenarios": {},
              "assumptions": ["10/23 고정 심사 입력에서 산업군만 교체한 민감도 분석",
                              "사용자 제공 분류표 추출본을 사용하며 원본 통합문서 및 거래소 확정 결과가 아님",
                              "공식 시행일·종목별 분류 적용일은 확인 대기이며 거래소 확정 편출입이 아님",
                              "GICS 심사 유니버스·평균시총·거래대금·기업이벤트·현재구성을 그대로 사용",
                              "KOSDAQ 300위 초과 재량제외를 적용한 추정 시나리오" if key == "kosdaq150" else
                              "KOSPI 현재구성 수와 무관하게 정기선정 목표는 200종목"]}
    if errors:
        return result
    current = {r["code"] for r in rows if r["is_cur"]}
    current.update(r["code"] for r in base.get("excluded_current", []) if r.get("is_cur"))
    if len(current) != base["n_current"]:
        result.update(status="HOLD", scenarios={})
        result["hold_reasons"].append("고정 심사행·제외목록으로 현재 구성종목 전체를 확인할 수 없음")
        return result
    for label, requires_primary in (("A", True), ("B", False)):
        scenario_rows = copy.deepcopy(rows)
        for row in scenario_rows:
            row["gics_baseline_sector"] = row["sector"]
            row["sector"] = mapping[row["code"]]
            row["sector_src"] = "첨부 KRICS 분류 (비교용, 적용일 확인 대기)"
            for field in DERIVED_FIELDS:
                row.pop(field, None)
        try:
            selected_result = selector(scenario_rows, current, cfg, new_requires_primary=requires_primary)
            if selected_result.get("n_selected") != cfg["N"]:
                raise ValueError(f"선정 종목 수 {selected_result.get('n_selected')} / 목표 {cfg['N']}")
        except (ValueError, KeyError, TypeError, ZeroDivisionError) as exc:
            result.update(status="HOLD", scenarios={})
            result["hold_reasons"].append(f"KRICS {label} 비교 선정 보류: {exc}")
            return result
        scenario = copy.deepcopy(base)
        scenario.update(selected_result)
        scenario["classification"] = {"scheme": "KRICS", "status": "sensitivity_only", "scenario": label,
                                      "effective_date_status": "HOLD", "official_result": False,
                                      "input_hash": result["input_hash"], "mapping_hash": result["mapping_hash"]}
        scenario["interpretation"] = "primary_and_new_buffer" if requires_primary else "liquid_and_new_buffer"
        scenario["rules"] = cfg["rules"].replace("GICS 참고 10개", "KRICS 9개 비교용").replace("GICS 참고 11개", "KRICS 9개 비교용")
        scenario["rules"] += " · A: 1차선정 AND 신규순위" if requires_primary else " · B: 1차선정과 별개로 유동성 AND 신규순위"
        scenario["scenario_id"] = label
        scenario["official_result"] = False
        scenario["effective_date_status"] = "HOLD"
        scenario["comparison_input_hash"] = result["input_hash"]
        selected_codes = {r["code"] for r in scenario["all"] if (r.get("status") or "").startswith(("유지", "신규편입"))}
        by_code = {r["code"]: r for r in scenario["all"]}
        describe = lambda codes: [{"code": c, "name": by_code[c]["name"]} for c in sorted(codes)]
        scenario["baseline_changes"] = {"selected_in": describe(selected_codes - baseline_selected),
                                        "selected_out": describe(baseline_selected - selected_codes)}
        result["scenarios"][label] = scenario
    return result
