"""Preserve validated KRICS calculation outputs without reusing them as live inputs."""
import copy
import hashlib
import json
import re
from pathlib import Path


ROW_FIELDS = ("code", "name", "sector", "sector_src", "avg_mcap", "avg_trdval", "n_days",
              "mcap_rank", "n_exist", "n_sector", "rank_ratio", "cum_share", "trd_rank",
              "liq_ok", "primary", "status", "listing_date", "mcap15", "note", "is_cur",
              "confidence", "conf_note", "risk", "market_mcap_rank", "discretionary_action",
              "review_start", "review_end", "review_events", "mcap15_rank", "mcap15_n_days")
RESULT_FIELDS = ("key", "name", "window", "data_through", "n_current", "n_universe", "n_selected",
                 "rules", "smallcap_rule", "small_cap_policy", "interpretation", "scenario_id",
                 "baseline_policy", "excluded_current", "sectors", "dropped_sectors")
DATE_FIELDS = ("classification_date_status", "source_date_status", "current_snapshot_date", "review_ref_date", "date_warning",
               "effective_date_status", "classification_as_of", "observed_at", "requested_date",
               "returned_as_of", "historical_asof_supported")


def _identity(value):
    value = {k: v for k, v in value.items() if k not in ("generated", "snapshot_id")}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def empty_history():
    return {"schema_version": 1, "indexes": {}, "history": {}}


def read_last_good(path):
    path = Path(path)
    if not path.exists():
        return empty_history()
    value = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(value, dict) or value.get("schema_version") != 1
            or not isinstance(value.get("indexes"), dict) or not isinstance(value.get("history"), dict)):
        raise ValueError("KRICS 과거 계산 보관 형식을 확인해야 합니다.")
    for key, snapshot in value["indexes"].items():
        validate_snapshot(snapshot, key)
        snapshot.setdefault("snapshot_id", snapshot["as_of"] + "_" + _identity(snapshot)[:16])
    for key, snapshots in value["history"].items():
        if not isinstance(snapshots, dict):
            raise ValueError("KRICS 과거 계산 목록 형식이 올바르지 않습니다.")
        for snapshot in snapshots.values():
            validate_snapshot(snapshot, key)
            snapshot.setdefault("snapshot_id", snapshot["as_of"] + "_" + _identity(snapshot)[:16])
    return value


def _compact_result(result, key, as_of):
    target = 200 if key == "kospi200" else 150
    if not isinstance(result, dict) or result.get("data_through") != as_of or result.get("n_selected") != target:
        raise ValueError("KRICS 과거 계산의 기준일·선정 수를 확인해야 합니다.")
    rows = result.get("all", [])
    codes = [r.get("code") for r in rows]
    if (not rows or len(set(codes)) != len(codes)
            or any(not isinstance(c, str) or not re.fullmatch(r"[0-9A-Z]{6}", c) for c in codes)):
        raise ValueError("KRICS 과거 계산의 심사대상 코드가 없거나 중복입니다.")
    selected = sorted(r["code"] for r in rows if (r.get("status") or "").startswith(("유지", "신규편입")))
    if len(selected) != target:
        raise ValueError("KRICS 과거 계산의 실제 선정 집합 수가 다릅니다.")
    value = {k: copy.deepcopy(result[k]) for k in RESULT_FIELDS if k in result}
    value["key"] = key
    value["selected_codes"] = selected
    for field in ("adds", "dels", "watch_keep", "watch_new", "largecap_candidates"):
        value[field] = [{k: copy.deepcopy(row[k]) for k in ROW_FIELDS if k in row}
                        for row in result.get(field, [])]
    value["official_result"] = False
    value["flow_availability"] = "hold"
    validate_result(value, key, as_of)
    return value


def validate_result(value, key, as_of):
    target = {"kospi200": 200, "kosdaq150": 150}.get(key)
    if not target or not isinstance(value, dict) or value.get("n_selected") != target or value.get("data_through") != as_of:
        raise ValueError("KRICS 과거 결과의 지수·기준일·선정 수가 올바르지 않습니다.")
    codes = value.get("selected_codes", [])
    if (len(codes) != target or len(set(codes)) != target
            or any(not isinstance(c, str) or not re.fullmatch(r"[0-9A-Z]{6}", c) for c in codes)):
        raise ValueError("KRICS 과거 결과의 선정 코드가 올바르지 않습니다.")
    adds, dels = value.get("adds", []), value.get("dels", [])
    for rows in (adds, dels):
        row_codes = [r.get("code") for r in rows]
        if (len(set(row_codes)) != len(row_codes)
                or any(not isinstance(c, str) or not re.fullmatch(r"[0-9A-Z]{6}", c) for c in row_codes)):
            raise ValueError("KRICS 과거 결과의 편출입 코드가 올바르지 않습니다.")
    if any(r["code"] not in codes for r in adds) or any(r["code"] in codes for r in dels):
        raise ValueError("KRICS 과거 편출입과 선정 집합이 다릅니다.")
    if value.get("n_current", 0) + len(adds) - len(dels) != target:
        raise ValueError("KRICS 과거 구성 수와 편출입 수가 일치하지 않습니다.")
    if value.get("flow_availability") != "hold" or value.get("official_result") is not False:
        raise ValueError("KRICS 과거 계산은 공식 확정·현재 수급으로 사용할 수 없습니다.")


def validate_snapshot(snapshot, key):
    if not isinstance(snapshot, dict) or not re.fullmatch(r"\d{8}", snapshot.get("as_of") or ""):
        raise ValueError("KRICS 과거 계산 기준일이 없습니다.")
    if snapshot.get("official_result") is not False:
        raise ValueError("KRICS 과거 계산은 공식 확정 결과가 아닙니다.")
    if any(not re.fullmatch(r"[0-9a-f]{64}", snapshot.get(field) or "") for field in ("input_hash", "mapping_hash")):
        raise ValueError("KRICS 과거 계산 입력·분류 해시를 확인해야 합니다.")
    expected = ("applied", "not_applied") if key == "kosdaq150" else ("applied",)
    for policy in expected:
        validate_result(snapshot.get("gics", {}).get(policy), key, snapshot["as_of"])
        for scenario in ("A", "B"):
            validate_result(snapshot.get("krics", {}).get(policy, {}).get(scenario), key, snapshot["as_of"])


def make_snapshot(analysis, key):
    base = analysis[key]
    comparison = base.get("classification_scenarios", {})
    as_of = comparison.get("review_as_of") or comparison.get("as_of")
    price_through = analysis.get("last_daily")
    if comparison.get("status") != "ready":
        return None
    coverage = comparison.get("coverage", {})
    if (not isinstance(as_of, str) or not re.fullmatch(r"\d{8}", as_of)
            or not isinstance(price_through, str) or not re.fullmatch(r"\d{8}", price_through)
            or as_of > price_through or comparison.get("as_of") != as_of
            or base.get("data_through") != as_of or comparison.get("hold_reasons")
            or coverage.get("missing_codes") or coverage.get("duplicate_mapping_codes")
            or coverage.get("duplicate_review_codes") or coverage.get("invalid_records", 0)
            or not isinstance(coverage.get("review_rows"), int) or coverage.get("review_rows", 0) <= 0
            or coverage.get("mapped_rows") != coverage.get("review_rows")):
        raise ValueError("KRICS 과거 보관 전 동일 기준일·매핑 검증이 필요합니다.")
    value = {"as_of": as_of, "price_data_through": price_through,
             "generated": analysis.get("generated"), "official_result": False,
             "input_hash": comparison.get("input_hash"), "mapping_hash": comparison.get("mapping_hash"),
             "data_basis": comparison.get("data_basis"), "gics": {}, "krics": {}}
    for field in DATE_FIELDS:
        if field in comparison:
            value[field] = copy.deepcopy(comparison[field])
    # Only public calculation provenance, never workbook paths or extraction identifiers.
    value["source"] = {"label": "검증된 KRICS 비교 계산 산출물", "mapping_hash": comparison.get("mapping_hash")}
    source = comparison.get("source", {})
    if str(source.get("source_id", "")).startswith("user_"):
        value["source"]["label"] = "사용자 제공 분류표로 산출한 KRICS 비교 결과"
    elif comparison.get("current_snapshot_date"):
        value["source"]["label"] = "공식 KRX 공개 현재 분류의 관측 결과를 사용한 민감도"
    for field in DATE_FIELDS:
        if field in source:
            value["source"][field] = copy.deepcopy(source[field])
    value["coverage"] = {k: copy.deepcopy(coverage[k]) for k in
        ("review_rows", "mapped_rows", "baseline_review_rows", "additional_review_names", "universe_completeness") if k in coverage}
    policies = ("applied", "not_applied") if key == "kosdaq150" else ("applied",)
    for policy in policies:
        gics = base.get("smallcap_scenarios", {}).get(policy, base if policy == "applied" else None)
        value["gics"][policy] = _compact_result(gics, key, as_of)
        scenarios = comparison.get("smallcap_scenarios", {}).get(policy,
            comparison.get("scenarios", {}) if policy == "applied" else {})
        value["krics"][policy] = {label: _compact_result(scenarios.get(label), key, as_of) for label in ("A", "B")}
    validate_snapshot(value, key)
    value["snapshot_id"] = value["as_of"] + "_" + _identity(value)[:16]
    return value


def save_last_good(analysis, path):
    """Update only validated ready indexes; HOLD/invalid candidates preserve prior success."""
    path = Path(path)
    stored = read_last_good(path)
    updated, preserved = [], {}
    for key in ("kospi200", "kosdaq150"):
        if key not in analysis:
            continue
        try:
            snapshot = make_snapshot(analysis, key)
        except (ValueError, TypeError, KeyError) as exc:
            preserved[key] = str(exc)
            continue
        if snapshot is None:
            preserved[key] = "현재 KRICS 계산 HOLD: 마지막 정상 산출물을 보존합니다."
            continue
        previous = stored["indexes"].get(key)
        if previous and _identity(previous) == _identity(snapshot):
            continue
        if previous:
            history_id = previous["as_of"] + "_" + _identity(previous)[:16]
            stored["history"].setdefault(key, {})[history_id] = previous
        stored["indexes"][key] = snapshot
        updated.append(key)
    if updated:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(stored, ensure_ascii=False, indent=1, allow_nan=False), encoding="utf-8")
        temporary.replace(path)
    return {"updated": updated, "preserved": preserved}
