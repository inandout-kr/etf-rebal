"""Attachment-derived KRICS scenarios on independently grouped review rows.

This module never treats the attachment forecast as a target portfolio and does
not infer an effective date. Invalid joins suppress the comparison, not the
independently calculated GICS baseline.
"""
import copy
import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from market_dates import korea_now

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


def load_collection_status(path):
    try:
        result = json.loads(Path(path).read_text(encoding="utf-8"))
        return result if isinstance(result, dict) else {"status": "HOLD"}
    except (OSError, ValueError, TypeError):
        return {"status": "HOLD", "errors": ["KRICS 공식 수집 상태를 확인할 수 없습니다."]}


def official_snapshot_contract(payload, index_key, as_of, ref_date, price_date, now, collection):
    """Current classification sensitivity; query quotes never prove historical membership."""
    errors = []
    dates = {"classification_date_status": "unverified", "current_snapshot_date": None,
             "review_ref_date": ref_date, "requested_date": payload.get("requested_date"),
             "classification_observed_at": payload.get("observed_at"), "returned_as_of": None,
             "historical_asof_supported": False, "classification_valid_from": None,
             "classification_valid_to": None, "date_warning": "공식 분류의 과거 효력일은 확인되지 않았습니다."}
    try:
        observed = datetime.fromisoformat(payload["observed_at"])
        attempted = datetime.fromisoformat(collection["attempted_at"])
        if observed.tzinfo is None or attempted.tzinfo is None:
            raise ValueError("Timezone required")
        observed, attempted, current = korea_now(observed), korea_now(attempted), korea_now(now)
        query = datetime.strptime(payload["requested_date"], "%Y%m%d").date()
        ref = datetime.strptime(ref_date, "%Y%m%d").date()
        through = datetime.strptime(as_of, "%Y%m%d").date()
        if (observed.date() != current.date() or attempted.date() != current.date()
                or observed > current or attempted > current or observed > attempted
                or query > observed.date() or through > query or through > ref
                or payload["requested_date"] != price_date):
            raise ValueError("Snapshot freshness or query condition mismatch")
        if (payload.get("mode") != "official_current_snapshot" or payload.get("historical_asof_supported") is not False
                or any(payload.get(k) is not None for k in ("returned_as_of", "valid_from", "valid_to"))):
            raise ValueError("Unsupported historical classification claim")
        if (not isinstance(collection, dict) or collection.get("schema_version") != 1
                or collection.get("status") != "ok" or collection.get("snapshot_id") != payload.get("snapshot_id")
                or collection.get("requested_date") != payload["requested_date"]):
            raise ValueError("Collection is not the current verified cache")
        records = payload["records"]
        codes, groups, sectors = [], set(), set()
        if not isinstance(records, list) or not records:
            raise ValueError("Official records unavailable")
        for row in records:
            if (not isinstance(row, dict) or not isinstance(row.get("code"), str)
                    or not re.fullmatch(r"[0-9A-Z]{6}", row["code"])
                    or not isinstance(row.get("name"), str) or not row["name"].strip()
                    or row.get("sector") not in KRICS_SECTORS
                    or not re.fullmatch(r"\d{4}", row.get("group_code") or "")
                    or row.get("sector_code") != row["group_code"][:2]
                    or not isinstance(row.get("group"), str) or not row["group"]
                    or row.get("market") not in ("KOSPI", "KOSDAQ", None)):
                raise ValueError("Official record identity or levels invalid")
            codes.append(row["code"])
            groups.add(row["group_code"])
            sectors.add(row["sector"])
        validation = payload["validation"]
        counts = validation["group_counts"]
        if (len(codes) != len(set(codes)) or len(sectors) != 9 or not isinstance(counts, dict)
                or len(counts) != 24 or not groups.issubset(counts)
                or any(not isinstance(n, int) or isinstance(n, bool) or n < 0 for n in counts.values())
                or sum(counts.values()) != len(records) or validation.get("official_records") != len(records)
                or validation.get("sector_count") != 9 or validation.get("group_count") != 24
                or validation.get("level1_union_verified") is not True or validation.get("summary_counts_verified") is not True):
            raise ValueError("Official response validation incomplete")
        actual_counts = Counter(row["group_code"] for row in records)
        if any(actual_counts.get(g, 0) != n for g, n in counts.items()):
            raise ValueError("Official group counts inconsistent")
        identifier = stable_hash({"observed_at": payload["observed_at"], "requested_date": payload["requested_date"],
                                  "records": sorted(records, key=lambda r: r["code"])})
        if payload.get("snapshot_id") != identifier:
            raise ValueError("Official snapshot integrity mismatch")
        source = payload["sources"][index_key]
        market = {"kospi200": "KOSPI", "kosdaq150": "KOSDAQ"}[index_key]
        relevant = [r for r in sorted(records, key=lambda r: r["code"]) if r["market"] == market]
        if (source.get("source_read_location") != "https://index.krx.co.kr/contents/MKD/03/0303/03030304/MKD03030304.jsp"
                or source.get("source_id") != "krx_public_krics_level1_2"
                or source.get("mapping") != [[r["code"], r["sector"]] for r in relevant]
                or source.get("level2_mapping") != [[r["code"], r["group_code"], r["group"]] for r in relevant]
                or source.get("source_sha256") != stable_hash(source["mapping"])):
            raise ValueError("Official source mapping inconsistent")
        date_status = "current_snapshot_after_review" if observed.date() > ref else "current_snapshot_during_review"
        dates.update(classification_date_status=date_status, current_snapshot_date=observed.strftime("%Y%m%d"),
                     date_warning=f"분류 관측 {observed:%Y-%m-%d} · 심사가격 {through:%Y-%m-%d}. 조회조건일은 시세 조건이며 과거 분류 효력일을 보장하지 않습니다. 현재 공식 분류 기준 민감도입니다.")
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        errors.append("KRICS 공식 스냅샷의 당일 수집·날짜·전체응답·캐시 결합 검증 실패; 이전 정상 결과와 구분합니다.")
    return dates, errors


def validate_mapping(payload, index_key, rows, as_of):
    errors = []
    if payload.get("schema_version") not in (1, 2):
        errors.append("KRICS 매핑 형식 버전 확인 필요")
    if payload.get("schema_version") == 1 and payload.get("frozen_as_of") != as_of:
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


NESTED_FIELDS = frozenset(("classification_scenarios", "smallcap_scenarios", "_classification_review_rows"))


def index_base(index):
    """Scenario rows carry no nested scenario collections or internal inputs."""
    return {k: copy.deepcopy(v) for k, v in index.items() if k not in NESTED_FIELDS}


def clear_derived(rows):
    for row in rows:
        for field in DERIVED_FIELDS:
            row.pop(field, None)


def policy_rules(rules, applied):
    phrase = "300위 초과 재량제외 적용 추정"
    if phrase in rules:
        return rules.replace(phrase, phrase if applied else "300위 초과 재량제외 미적용 추정")
    return rules + (" → 300위 초과 재량제외 적용 추정" if applied else " → 300위 초과 재량제외 미적용 추정")


def gics_smallcap_comparison(base_index, cfg, selector):
    """KQ 6.4.5 policy is independent of KRICS 6.4.2 interpretation."""
    base = index_base(base_index)
    current = {r["code"] for r in base["all"] if r["is_cur"]}
    current.update(r["code"] for r in base.get("excluded_current", []) if r.get("is_cur"))
    variants = {}
    for policy, applied in (("applied", True), ("not_applied", False)):
        rows = copy.deepcopy(base["all"])
        clear_derived(rows)
        config = dict(cfg, small_cap_exclusion=applied, rules=policy_rules(cfg["rules"], applied))
        variant = index_base(base)
        variant.update(selector(rows, current, config))
        variant.update(small_cap_policy=policy, official_result=False)
        variants[policy] = variant
    return variants


def classification_comparison(base_index, cfg, payload, selector, load_error=None, review_rows=None,
                              snapshot_now=None, collection_status=None):
    """Reclassify review-eligible rows before the optional GICS grouping filter.

    Prices, dates, liquidity and corporate-event observations stay fixed. A
    missing GICS group cannot remove a mapped KRICS candidate. Author forecast
    labels and CHECK missing-value exclusions never determine eligibility here.
    """
    base = index_base(base_index)
    rows = copy.deepcopy(base["all"] if review_rows is None else review_rows)
    baseline_rows = base["all"]
    baseline_codes = {r["code"] for r in baseline_rows}
    key, as_of = base["key"], base["data_through"]
    mapping, source, coverage, errors = validate_mapping(payload, key, rows, as_of)
    date_fields = {}
    official = payload.get("schema_version") == 2
    if official:
        date_fields, date_errors = official_snapshot_contract(
            payload, key, as_of, base["window"]["ref_date"], base.get("price_data_through", as_of),
            snapshot_now, collection_status)
        errors.extend(date_errors)
    if load_error:
        errors.insert(0, load_error)
    review_codes = {r["code"] for r in rows}
    added_rows = [r for r in rows if r["code"] not in baseline_codes]
    coverage.update(baseline_review_rows=len(baseline_rows),
                    additional_review_codes=sorted(r["code"] for r in added_rows),
                    additional_review_names=[{"code": r["code"], "name": r["name"]} for r in added_rows],
                    missing_review_data=copy.deepcopy(base.get("missing_review_data", [])),
                    universe_completeness="incomplete" if base.get("missing_review_data") else "observed_rows_only")
    if baseline_codes - review_codes:
        errors.append("KRICS 공통 심사 입력에서 GICS 심사행이 누락되었습니다.")
    source_metadata = {k: copy.deepcopy(v) for k, v in source.items() if k not in ("mapping", "level2_mapping")}
    if official:
        source_metadata.update({k: copy.deepcopy(payload.get(k)) for k in
                                ("observed_at", "requested_date", "returned_as_of", "historical_asof_supported", "valid_from", "valid_to", "snapshot_id")})
    input_fields = ("code", "name", "avg_mcap", "avg_trdval", "is_cur", "n_days", "review_start", "review_end",
                    "review_events", "market_mcap_rank", "mcap15", "mcap15_n_days", "listing_date")
    frozen_input = [{k: row.get(k) for k in input_fields} for row in sorted(rows, key=lambda r: r["code"])]
    result = {"status": "HOLD" if errors else "ready",
              "data_basis": "same_frozen_review_rows" if review_rows is None else "eligible_review_rows_before_gics_filter",
              "as_of": as_of, "input_hash": stable_hash(frozen_input), "mapping_hash": stable_hash(source.get("mapping", [])),
              "source": source_metadata, "coverage": coverage, "effective_date_status": "HOLD",
              "official_result": False, "hold_reasons": errors, "scenarios": {}, "smallcap_scenarios": {},
              "assumptions": ["동일 기준일 관측 시총·거래대금·기업이벤트·현구성을 사용한 KRICS 모의 산출",
                              "GICS 분류 존재 여부와 독립된 심사 입력; 관측 없는 원시자료는 임의 생성하지 않음",
                              "사용자 제공 분류표 추출본이며 공식 최종 분류·시행일 확인 대기",
                              "CHECK 결측을 공식 결격으로 간주하지 않으며 평균시총 원장 차이는 미확인",
                              "300위 재량제외 적용 여부는 신규편입 A/B 해석과 별도 축" if key == "kosdaq150" else
                              "KOSPI 현구성 수와 무관하게 정기선정 목표는 200종목"]}
    result.update(date_fields)
    if official:
        result["assumptions"][2] = "KRX 공개 KRICS 1·2단계 당일 스냅샷. 과거 심사기준일의 분류 또는 공식 최종 편출입 결과가 아님"
        result["assumptions"].append(date_fields["date_warning"])
    if errors:
        return result
    current = {r["code"] for r in rows if r["is_cur"]}
    current.update(r["code"] for r in base.get("excluded_current", []) if r.get("is_cur"))
    if len(current) != base["n_current"]:
        result.update(status="HOLD", scenarios={})
        result["hold_reasons"].append("현재 심사행·제외목록에서 현 구성종목 전체를 확인할 수 없음")
        return result
    policies = (("applied", True), ("not_applied", False)) if key == "kosdaq150" else (("not_applicable", False),)
    gics_policies = gics_smallcap_comparison(base, cfg, selector) if key == "kosdaq150" else {}
    for policy, applied in policies:
        policy_baseline = gics_policies.get(policy, base)
        policy_baseline_selected = {row["code"] for row in policy_baseline["all"]
                                    if (row.get("status") or "").startswith(("유지", "신규편입"))}
        variants = {}
        for label, requires_primary in (("A", True), ("B", False)):
            scenario_rows = copy.deepcopy(rows)
            for row in scenario_rows:
                row["gics_baseline_sector"] = row.get("sector")
                row["sector"] = mapping[row["code"]]
                row["sector_src"] = "KRX 공개 현재 KRICS 분류 (과거 효력일 미확인)" if official else "첨부 KRICS 분류 (적용일 확인 대기)"
            clear_derived(scenario_rows)
            config = dict(cfg)
            if key == "kosdaq150":
                config.update(small_cap_exclusion=applied, rules=policy_rules(cfg["rules"], applied))
            try:
                selected_result = selector(scenario_rows, current, config, new_requires_primary=requires_primary)
                if selected_result.get("n_selected") != cfg["N"]:
                    raise ValueError(f"선정 종목 수 {selected_result.get('n_selected')} / 목표 {cfg['N']}")
            except (ValueError, KeyError, TypeError, ZeroDivisionError) as exc:
                result.update(status="HOLD", scenarios={}, smallcap_scenarios={})
                result["hold_reasons"].append(f"KRICS {label}·{policy} 계산 검증 실패: {exc}")
                return result
            scenario = index_base(base)
            scenario.update(selected_result)
            scenario["excluded_current"] = [r for r in base.get("excluded_current", []) if r["code"] not in review_codes]
            scenario["classification"] = {"scheme": "KRICS", "status": "sensitivity_only", "scenario": label,
                                          "effective_date_status": "HOLD", "official_result": False,
                                          "input_hash": result["input_hash"], "mapping_hash": result["mapping_hash"]}
            scenario["interpretation"] = "primary_and_new_buffer" if requires_primary else "liquid_and_new_buffer"
            scenario["rules"] = config["rules"].replace("GICS 참고 10개", "KRICS 9개 비교용").replace("GICS 참고 11개", "KRICS 9개 비교용")
            scenario["rules"] += " · A: 1차선정 AND 신규순위" if requires_primary else " · B: 1차선정과 무관한 유동성 AND 신규순위"
            scenario.update(scenario_id=label, small_cap_policy=policy, official_result=False,
                            effective_date_status="HOLD", comparison_input_hash=result["input_hash"])
            scenario.update(date_fields)
            selected_codes = {r["code"] for r in scenario["all"] if (r.get("status") or "").startswith(("유지", "신규편입"))}
            by_code = {r["code"]: r for r in baseline_rows + scenario["all"]}
            describe = lambda codes: [{"code": c, "name": by_code[c]["name"]} for c in sorted(codes)]
            scenario["baseline_policy"] = policy
            scenario["baseline_changes"] = {"selected_in": describe(selected_codes - policy_baseline_selected),
                                            "selected_out": describe(policy_baseline_selected - selected_codes)}
            variants[label] = scenario
        if key == "kosdaq150":
            result["smallcap_scenarios"][policy] = variants
        if policy in ("applied", "not_applicable"):
            result["scenarios"] = variants
    return result
