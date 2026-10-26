"""Pure flow calculations. Official FFR/CF inputs are never inferred from capped PDFs.

Sector membership uses the reviewed document's dedicated hierarchy and its holding
company exception. Parent-index sector labels cannot substitute for that input.
"""
import calendar
import math
from datetime import date


SECTOR_RULES = {
    "kospi200_it": {
        "rule_id": "P17_appendix1_kospi200_it",
        "clause": "섹터지수 방법론 6.1/6.2/6.4, 별표1(8-9쪽)",
        "level1": "정보기술",
        "level2": {"IT소프트웨어", "IT하드웨어", "반도체및디스플레이", "IT서비스"},
        "holding_company_exception": "주요 자회사가 속한 섹터로 분류",
    },
    "krx_semi": {
        "rule_id": "P14_appendix1_krx_semi",
        "clause": "KRX 섹터지수 방법론 2.1-2.4, 별표1",
        "level1": "정보기술", "level2": {"반도체및디스플레이"}, "level3": "반도체",
        "holding_company_exception": None,
    },
}


def reason(code, message, clause=None):
    return {"code": code, "message": message, **({"clause": clause} if clause else {})}


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _label(value):
    return "".join(str(value or "").split())


def _input_date(value):
    """External dates must be real calendar dates in ISO or compact form."""
    if not isinstance(value, str) or not value.isascii():
        return None
    if len(value) == 8 and value.isdigit():
        value = value[:4] + "-" + value[4:6] + "-" + value[6:]
    elif (len(value) != 10 or value[4] != "-" or value[7] != "-"
          or not (value[:4] + value[5:7] + value[8:]).isdigit()):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _provenance(payload, as_of):
    if not isinstance(payload, dict):
        return False
    observed, requested = _input_date(payload.get("as_of")), _input_date(as_of)
    source = payload.get("source_url")
    return bool(payload.get("status") == "official" and isinstance(source, str) and source.strip()
                and observed is not None and requested is not None and observed <= requested)


def sector_membership(key, parent_codes, supplied, as_of):
    """Complete official hierarchy, including an explicit holding-company decision.

    supplied: status/source_url/as_of/rule_id/classifications. Each classification
    has krics1/2(/3), is_holding_company. A holding company also needs a sourced,
    verified major_subsidiary classification. Missing rows hold the whole basket.
    """
    rule = SECTOR_RULES[key]
    output = {"codes": None, "status": "hold", "basis": rule["clause"],
              "hold_reasons": [], "rule": {k: sorted(v) if isinstance(v, set) else v for k, v in rule.items()}}
    if not _provenance(supplied, as_of) or supplied.get("rule_id") != rule["rule_id"]:
        output["hold_reasons"].append(reason("official_sector_mapping_missing",
            "공식 세부 KRICS 매핑과 적용 별표 근거가 필요합니다.", rule["clause"]))
        return output
    classifications = supplied.get("classifications", {})
    if not isinstance(classifications, dict):
        classifications = {}
    selected = []
    for code in sorted(set(parent_codes)):
        row = classifications.get(code)
        if not isinstance(row, dict) or not row.get("krics1") or not row.get("krics2"):
            output["hold_reasons"].append(reason("sector_mapping_incomplete", f"{code}: KRICS 세부 분류 미확인", rule["clause"]))
            continue
        if rule["holding_company_exception"]:
            if not isinstance(row.get("is_holding_company"), bool):
                output["hold_reasons"].append(reason("holding_company_status_missing", f"{code}: 지주회사 여부 미확인", rule["clause"]))
                continue
            if row["is_holding_company"]:
                subsidiary = row.get("major_subsidiary") or {}
                if (not isinstance(subsidiary, dict) or subsidiary.get("verified") is not True or not subsidiary.get("source_url")
                        or not subsidiary.get("code") or not subsidiary.get("krics1") or not subsidiary.get("krics2")):
                    output["hold_reasons"].append(reason("holding_company_exception_unverified",
                        f"{code}: 주요 자회사 섹터와 근거 미확인", rule["clause"]))
                    continue
                row = subsidiary
        included = (_label(row["krics1"]) == rule["level1"] and _label(row["krics2"]) in rule["level2"])
        if included and rule.get("level3"):
            if not row.get("krics3"):
                output["hold_reasons"].append(reason("sector_level3_missing", f"{code}: 반도체/디스플레이 세분류 미확인", rule["clause"]))
                continue
            included = _label(row["krics3"]) == rule["level3"]
        if included:
            selected.append(code)
    if not output["hold_reasons"]:
        output.update(codes=selected, status="ready", mapping_source={k: supplied[k] for k in ("source_url", "as_of", "rule_id")})
    return output


def adjusted_weights(holding, stocks, original_prices):
    weights = {}
    for row in holding.get("rows", []):
        code, weight = row.get("code"), row.get("weight")
        if not code or weight is None or code not in stocks:
            continue
        p0, p1 = original_prices.get(code), stocks[code].get("close")
        weights[code] = weight * p1 / p0 if _positive(p0) and _positive(p1) else weight
    total = sum(weights.values())
    return {c: value / total * 100 for c, value in weights.items()} if total else weights


def notional_metadata(aum, multiplier, holding_rows, included_etf_names):
    """Deduct an underlying ETF only when its AUM is already in this basket."""
    included = {_label(n) for n in included_etf_names}
    matched, unmatched = [], []
    for row in holding_rows:
        if row.get("code") or not _positive(row.get("weight")) or "선물" in row.get("name", ""):
            continue
        target = matched if _label(row.get("name")) in included else unmatched
        target.append({"name": row.get("name"), "weight_percent": row["weight"]})
    weight = sum(r["weight_percent"] for r in matched) if multiplier > 1 else 0
    if not _positive(aum) or not _positive(multiplier) or weight > multiplier * 100:
        raise ValueError("추종 명목 입력이 유효하지 않습니다.")
    deduction = aum * weight / 100
    return {"aum_eok": aum, "leverage_multiplier": multiplier,
            "gross_notional_eok": aum * multiplier, "underlying_etf_weight_percent": weight,
            "deduction_eok": deduction, "notional_eok": aum * multiplier - deduction,
            "deducted_holdings": matched if multiplier > 1 else [],
            "unmatched_holdings_not_deducted": unmatched,
            "notional_basis": "순자산×배수−합산에 포함된 기초ETF 실물보유분(파생 헤지 포함 가정)"}


def relative_coefficients(codes, cur_weights, stocks, fallback=None):
    """Explicit uncapped weight model; this result is not an official FFR."""
    ratios = {c: value / stocks[c]["mktcap"] for c, value in cur_weights.items()
              if c in stocks and _positive(stocks[c].get("mktcap"))
              and isinstance(value, (int, float)) and math.isfinite(value) and value >= 0}
    values = sorted(ratios.values(), reverse=True)
    ref = values[int(len(values) * .1)] if values else None
    median = round(sorted(ratios.values())[len(ratios) // 2] / ref, 3) if ref else .6
    result = {}
    for code in codes:
        if code in ratios and ref:
            result[code] = (max(.05, min(1.25, ratios[code] / ref)), "비상한 프록시 상대가중 역산")
        elif _positive((fallback or {}).get(code)):
            result[code] = (fallback[code], "기존 비상한 프록시 상대가중 추정")
        else:
            result[code] = (median, "상대가중 중앙값 가정")
    return result


def official_weight_inputs(inputs, codes, as_of, apply, cap_required):
    """Validate supplied FFR/CF separately; malformed external input holds."""
    errors, ffr, factors = [], {}, {}
    if inputs is not None and not isinstance(inputs, dict):
        return {}, {}, [reason("official_weight_input_invalid", "공식 수급 입력은 객체여야 합니다.")]
    inputs = inputs or {}
    supplied_ffr = inputs.get("official_ffr")
    if not _provenance(supplied_ffr, as_of):
        code = "official_ffr_missing" if supplied_ffr is None else "official_ffr_input_invalid"
        errors.append(reason(code, "공식 유동주식비율의 날짜·출처·자료형을 확인해야 합니다.", "KRX 기본방법론 6, 섹터방법론9.3"))
    else:
        values = supplied_ffr.get("values")
        if not isinstance(values, dict):
            errors.append(reason("official_ffr_values_invalid", "공식 유동주식비율 values는 종목코드별 객체여야 합니다."))
        else:
            ffr = values
            if any(not _positive(ffr.get(c)) or ffr[c] > 1 for c in codes):
                errors.append(reason("official_ffr_incomplete", "목표 종목의 공식 유동주식비율이 누락되거나 범위를 벗어났습니다."))
    cap_input = inputs.get("official_cap_factors")
    if cap_required:
        apply_date, observed_date = _input_date(apply), _input_date(as_of)
        cap_dict = cap_input if isinstance(cap_input, dict) else {}
        valid_from, valid_to = _input_date(cap_dict.get("valid_from")), _input_date(cap_dict.get("valid_to"))
        ref = cap_dict.get("reference_period")
        ref = ref if isinstance(ref, dict) else {}
        ref_start, ref_end = _input_date(ref.get("start")), _input_date(ref.get("end"))
        published = _input_date(cap_dict.get("as_of"))
        valid = (_provenance(cap_input, as_of) and apply_date is not None and observed_date is not None
                 and valid_from is not None and valid_to is not None
                 and ref_start is not None and ref_end is not None and published is not None)
        if valid:
            first = apply_date.replace(day=1)
            month = first.month - 3
            year = first.year
            if month <= 0:
                year, month = year - 1, month + 12
            prior_month = first.month - 1 or 12
            prior_year = first.year - (first.month == 1)
            expected_start = date(year, month, 1)
            expected_end = date(prior_year, prior_month, calendar.monthrange(prior_year, prior_month)[1])
            valid = (valid_from <= apply_date <= valid_to and ref_start <= ref_end
                     and ref_start == expected_start and ref_end == expected_end
                     and ref_end <= published <= observed_date)
        if not valid:
            code = "official_cap_factor_missing" if cap_input is None else "official_cap_factor_input_invalid"
            errors.append(reason(code, "CAP Factor 적용구간·직전3개월 산정기간의 날짜와 자료형을 확인해야 합니다.", "KRX 기본방법론7.2-7.4"))
        else:
            values = cap_dict.get("values")
            if not isinstance(values, dict):
                errors.append(reason("official_cap_factor_values_invalid", "CAP Factor values는 종목코드별 객체여야 합니다."))
            else:
                factors = values
                if any(not _positive(factors.get(c)) or factors[c] > 1 or abs(factors[c] - round(factors[c], 4)) > 1e-10 for c in codes):
                    errors.append(reason("official_cap_factor_incomplete", "CAP Factor 누락·범위 또는 소수4자리 검증에 실패했습니다.", "KRX 기본방법론7.3"))
    else:
        factors = {c: 1.0 for c in codes}
    return ffr, factors, errors


def _capped_weights(weights, caps):
    if not weights or any(not math.isfinite(v) or v <= 0 for v in weights.values()):
        raise ValueError("가중치 계산에는 유효한 양수 시가총액이 필요합니다.")
    if any(not math.isfinite(v) or v <= 0 for v in caps.values()) or sum(caps.values()) < 1 - 1e-12:
        raise ValueError("구성종목 수와 비중 상한으로 100%를 배분할 수 없습니다.")
    total = sum(weights.values())
    w = {k: v / total for k, v in weights.items()}
    capped = set()
    for _ in range(len(w) + 1):
        over = {k for k in w if k not in capped and w[k] > caps[k] + 1e-12}
        if not over:
            return w, capped
        capped |= over
        free = 1 - sum(caps[k] for k in capped)
        rest = sum(weights[k] for k in w if k not in capped)
        if not rest:
            raise ValueError("비중 상한 적용 후 잔여 비중을 배분할 종목이 없습니다.")
        w = {k: caps[k] if k in capped else weights[k] / rest * free for k in w}
    raise ValueError("비중 상한 계산이 수렴하지 않았습니다.")


def water_fill(weights, cap):
    return _capped_weights(weights, {k: cap if cap is not None else 1.0 for k in weights})


def tiered_ceiling(weights, first, others):
    """가장 큰 종목 first, 나머지 others 실링 (초과분 비례 재배분 반복)"""
    if not weights:
        raise ValueError("가중치를 계산할 구성종목이 없습니다.")
    top = max(weights, key=weights.get)
    return _capped_weights(weights, {k: first if k == top else others for k in weights})[0]


def calculate_event(cfg, membership, stocks, holdings, etf_master, analysis, adv, prices_at, cycle, inputs=None):
    """One precomputed API for every screen; missing inputs stay null."""
    malformed_input = inputs is not None and not isinstance(inputs, dict)
    inputs = inputs if isinstance(inputs, dict) else {}
    h = holdings[cfg["proxy"]]
    by_etf = {entry["etf_code"]: entry for entry in holdings.values()}
    cur_w = adjusted_weights(h, stocks, prices_at(h["date"]))
    cur, target = set(cur_w), membership.get("codes")
    target_codes = set(target or [])
    reasons = list(membership.get("hold_reasons", []))
    if malformed_input:
        reasons.append(reason("official_weight_input_invalid", "공식 수급 입력은 객체여야 합니다."))
    wt = cfg["weighting"]
    krx_cap = cfg["key"] in ("kospi100", "kospi200_it", "krx_semi")
    requires_official = bool(wt.get("cap")) or wt["type"] in ("topn_fixed", "score_tiered")
    ffr, factors, relative = {}, {}, {}
    if requires_official:
        ffr, factors, errors = official_weight_inputs(inputs, target_codes, analysis["last_daily"],
            cycle["apply"], krx_cap and bool(wt.get("cap")) and len(target_codes) >= 5)
        reasons.extend(errors)
    if target is not None:
        missing = [c for c in target if c not in stocks or not _positive(stocks[c].get("mktcap"))]
        if missing:
            reasons.append(reason("target_prices_missing", "목표 종목의 완료 시총 누락: " + ", ".join(missing)))
        if not target:
            reasons.append(reason("target_empty", "확인된 목표 종목이 없습니다."))
    if target is None and not reasons:
        reasons.append(reason("membership_unknown", "목표 구성종목 확인이 필요합니다."))
    all_codes = cur | target_codes
    included_names = [etf_master[c]["name"] for c, _ in cfg["etfs"] if c in etf_master and _positive(etf_master[c].get("aum_eok"))]
    etfs, etf_weights = [], {}
    for code, multiplier in cfg["etfs"]:
        meta = etf_master.get(code, {})
        if not _positive(meta.get("aum_eok")):
            continue
        actual = by_etf.get(code)
        notional = notional_metadata(meta["aum_eok"], multiplier, (actual or {}).get("rows", []), included_names)
        weights = adjusted_weights(actual, stocks, prices_at(actual["date"])) if actual and multiplier == 1 else cur_w
        etf_weights[code] = weights
        all_codes |= set(weights)
        etfs.append({"code": code, "name": meta["name"], **notional,
                     "pdf_date": (actual or h)["date"], "note": notional["notional_basis"]})
    if not etfs:
        reasons.append(reason("tracking_aum_missing", "추종 ETF 순자산 입력이 없습니다."))
    weights, capped, fixed = {}, set(), set()
    if not reasons:
        if not requires_official:
            relative = relative_coefficients(all_codes, cur_w, stocks, analysis.get("fif", {}))
        basis = {c: stocks[c]["mktcap"] * (ffr[c] if requires_official else relative[c][0]) for c in target}
        if krx_cap:
            adjusted = {c: value * factors[c] for c, value in basis.items()}
            total = sum(adjusted.values())
            weights = {c: value / total for c, value in adjusted.items()}
            capped = {c for c in target if factors[c] < 1}
        elif wt["type"] == "float":
            weights, capped = water_fill(basis, wt.get("cap"))
        elif wt["type"] == "score_tiered":
            weights = tiered_ceiling({c: cur_w.get(c) or .01 for c in target}, wt["first"], wt["others"])
        elif wt["type"] == "topn_fixed":
            pick = (lambda c: cur_w.get(c, 0)) if wt.get("pick") == "weight" else (lambda c: stocks[c]["mktcap"])
            top = sorted(target, key=pick, reverse=True)[:wt["n"]]
            fixed = set(top)
            rest = set(target) - fixed
            free = 1 - wt["w"] * len(top)
            if free < 0 or (free > 1e-12 and not rest):
                raise ValueError("고정 비중 적용 후 잔여 종목이 부족합니다.")
            if wt["rest"] == "equal":
                rw = {c: free / len(rest) for c in rest}
            elif wt["rest"] == "current":
                total = sum(cur_w.get(c) or .01 for c in rest)
                rw = {c: free * (cur_w.get(c) or .01) / total for c in rest}
            else:
                rw, capped = water_fill({c: basis[c] for c in rest}, wt.get("cap") / free if wt.get("cap") else None)
                rw = {c: value * free for c, value in rw.items()}
            weights = {**{c: wt["w"] for c in top}, **rw}
    held = bool(reasons)
    rows = []
    for code in sorted(all_codes):
        stock = stocks.get(code, {})
        target_weight = None if held else weights.get(code, 0) * 100
        per_etf = {e["code"]: None if held else e["notional_eok"] * (target_weight - etf_weights[e["code"]].get(code, 0)) / 100 for e in etfs}
        flow = None if held else sum(per_etf.values())
        coefficient = relative.get(code)
        status = "검토보류" if held else "편출" if code not in target_codes else "유지" if code in cur else "신규편입"
        rows.append({"code": code, "name": stock.get("name", code), "status": status,
            "availability": "hold" if held else "estimated", "membership_status": membership.get("status"),
            "w_cur": round(cur_w.get(code, 0), 3), "w_target": None if held else round(target_weight, 3),
            "w_target_raw": target_weight, "delta": None if held else round(target_weight - cur_w.get(code, 0), 3),
            "fixed": code in fixed, "capped": code in capped,
            "flow_by_etf": {c: None if v is None else round(v, 1) for c, v in per_etf.items()},
            "flow_total": None if held else round(flow, 1), "adv20": adv.get(code),
            "adv_mult": abs(flow) * 1e8 / adv[code] if flow is not None and _positive(adv.get(code)) else None,
            "official_ffr": ffr.get(code) if requires_official else None,
            "cap_factor": factors.get(code) if requires_official and krx_cap else None,
            "relative_coefficient": coefficient[0] if coefficient else None,
            "relative_coefficient_source": coefficient[1] if coefficient else None,
            "fif": None, "fif_src": "공식 FFR과 상대가중계수를 별도 표시",
            "mktcap_now": stock.get("mktcap"), "price_as_of": stock.get("price_as_of"),
            "price_status": stock.get("price_status"), "universe_as_of": stock.get("universe_as_of")})
    rows.sort(key=lambda r: (r["flow_total"] is None, -abs(r["flow_total"] or 0), r["code"]))
    return {"key": cfg["key"], "name": cfg["name"], "apply": cfg.get("apply", cycle["apply"]),
        "fix": cfg.get("fix"), "proxy_etf": h["etf_code"], "pdf_date": h["date"], "window": cycle,
        "date_status": cycle.get("date_status"), "apply_end": cycle.get("apply_end", cycle["apply"]),
        "availability": "hold" if held else "estimated", "hold_reasons": reasons,
        "review_availability": "pending_review" if any(r["code"] == "review_not_started" for r in reasons) else "in_review",
        "membership_status": membership.get("status"), "membership_evidence": membership,
        "official_result": False, "target_src": membership.get("basis", ""), "weighting": wt,
        "note": cfg.get("note"), "etfs": etfs, "notional_total": sum(e["notional_eok"] for e in etfs),
        "aum_total": sum(e["aum_eok"] for e in etfs), "n_current": len(cur), "n_target": None if held else len(target_codes),
        "adds": None if held else [r["name"] for r in rows if r["status"] == "신규편입"],
        "dels": None if held else [r["name"] for r in rows if r["status"] == "편출"],
        "total_buy": None if held else round(sum(r["flow_total"] for r in rows if r["flow_total"] > 0), 1),
        "total_sell": None if held else round(sum(r["flow_total"] for r in rows if r["flow_total"] < 0), 1),
        "rows": rows, "trade_date": cycle.get("trade_date"), "trade_end": cycle.get("trade_end"),
        "weight_basis": "official_ffr_and_fixed_cap_factors" if krx_cap and not held else "official_ffr_model" if requires_official and not held else "uncapped_relative_coefficient_estimate" if not held else "hold",
        "adv_basis": {"sessions": 20, "as_of": analysis["last_daily"], "unit": "원/완료거래일"},
        "data_basis": analysis["last_daily"], "passed": False}


def aggregate_events(events):
    """Known subtotal and held-event coverage remain distinct, including all-held rows."""
    output = {}
    for event in events:
        if event.get("passed"):
            continue
        for row in event.get("rows", []):
            item = output.setdefault(row["code"], {"name": row["name"], "events": [], "held_events": [], "total": None,
                "known_total": None, "held_count": 0, "known_count": 0, "complete": True})
            if row.get("flow_total") is None:
                item["held_events"].append({"key": event["key"], "index": event["name"], "apply": event["apply"],
                    "hold_reasons": event.get("hold_reasons", [])})
                item["held_count"] += 1
                item["complete"] = False
            else:
                item["events"].append({"key": event["key"], "index": event["name"], "apply": event["apply"],
                    "flow": row["flow_total"], "status": row["status"], "adv_mult": row.get("adv_mult")})
                item["known_count"] += 1
                item["known_total"] = round((item["known_total"] or 0) + row["flow_total"], 1)
                item["total"] = item["known_total"]
    return output
