"""Conditional rebalance basket alignment; public flows do not identify ETF trades.

All observation rows are cumulative net shares since the frozen plan's window_start.
Error bounds are supplied sensitivity assumptions, never statistical confidence levels.
This module is deliberately independent of the existing daily pipeline.
"""

import math
from datetime import date, datetime


def _time(value, field):
    try:
        result = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be an aware ISO8601 timestamp") from None
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(f"{field} must include a UTC offset")
    return result


def _number(value, field, *, positive=False, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    try:
        value = float(value)
    except OverflowError:
        raise ValueError(f"{field} has an unsupported numeric scale") from None
    if not math.isfinite(value) or (positive and value <= 0) or (nonnegative and value < 0):
        raise ValueError(f"{field} has an invalid numeric value")
    return value


def _identifier(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    return value


def _rows(snapshot):
    rows = snapshot.get("rows")
    if not isinstance(rows, list):
        raise ValueError("rows must be a list")
    result = {}
    for row in rows:
        code = _identifier(row.get("code"), "code")
        if code in result:
            raise ValueError(f"duplicate code: {code}")
        result[code] = row
    return result


def _known(snapshot, as_of, prefix, *, allow_future=False):
    measured = _time(snapshot.get("as_of"), prefix + ".as_of")
    published = _time(snapshot.get("available_at"), prefix + ".available_at")
    if measured > published or (published > as_of and not allow_future):
        raise ValueError(f"{prefix} is future or was not available at the requested as_of")
    return measured, published


def _projection(rows):
    # Scaling the inverse-variance weights avoids unnecessarily huge 1/error**2.
    minimum_error = min(row["error"] for row in rows)
    scale = max(abs(row["required"]) for row in rows)
    weighted = [((minimum_error / r["error"]) ** 2,
                 r["required"] / scale, r["signal"] / scale) for r in rows]
    denominator = math.fsum(w * x * x for w, x, _ in weighted)
    if not denominator:
        raise ValueError("projection numeric scale is not supported")
    alpha = math.fsum(w * x * y for w, x, y in weighted) / denominator
    if not math.isfinite(alpha):
        raise ValueError("projection numeric scale is not supported")
    return alpha


def estimate_pretrade(plan, observations=None, *, as_of):
    """Return basket alignment and attribution scenarios, never actual ETF execution.

    observations may be one cumulative snapshot or a list; the latest snapshot
    replaces all earlier snapshots, including rows absent from the latest one.
    Future publications are skipped for both single snapshots and snapshot lists;
    malformed timestamps and measurements later than publication are rejected.
    """
    requested = _time(as_of, "as_of")
    event_id = _identifier(plan.get("event_id"), "event_id")
    if plan.get("event_kind") not in ("regular", "ad_hoc"):
        raise ValueError("event_kind must be regular or ad_hoc")
    if plan.get("scope") != "remaining_at_window_start":
        raise ValueError("plan scope must be remaining_at_window_start")
    start = _time(plan.get("window_start"), "window_start")
    end = _time(plan.get("trade_end"), "trade_end")
    published = _time(plan.get("available_at"), "available_at")
    target_known = _time(plan.get("target_known_at", plan.get("available_at")), "target_known_at")
    try:
        trade_date = date.fromisoformat(plan.get("trade_date"))
    except (TypeError, ValueError):
        raise ValueError("trade_date must be YYYY-MM-DD") from None
    if start > requested or published > requested or end < start:
        raise ValueError("plan is future, unavailable, or has an inverted window")
    if target_known > start or target_known > published:
        raise ValueError("target_known_at must not follow window_start or plan publication")
    if trade_date > end.date():
        raise ValueError("trade_date must not follow trade_end's local date")
    plan_rows = _rows(plan)
    target = {}
    for code, row in plan_rows.items():
        target[code] = {"required": _number(row.get("required_shares"), "required_shares"),
                        "price": _number(row.get("reference_price"), "reference_price", positive=True)}
    total_notional = math.fsum(abs(r["required"]) * r["price"] for r in target.values())
    if not total_notional or not math.isfinite(total_notional):
        raise ValueError("plan needs finite nonzero target support")
    bounded = (plan.get("assumption_no_reversal") is True
               and plan.get("assumption_no_overtrade") is True)
    plan_warnings = plan.get("warnings", [])
    if not isinstance(plan_warnings, list) or any(not isinstance(w, str) for w in plan_warnings):
        raise ValueError("plan warnings must be a list of strings")
    output = {
        "event_id": event_id, "as_of": requested.isoformat(),
        "plan_scope": plan["scope"], "plan_available_at": published.isoformat(),
        "target_known_at": target_known.isoformat(),
        "plan_warnings": list(plan_warnings), "unavailable_snapshot_count": 0,
        "window_start": start.isoformat(), "observed_through": None,
        "stale_seconds": None, "status": "no_observation",
        "estimand": "conditional_common_fraction_basket_alignment",
        "actual_etf_pretrade_shares": None, "actual_etf_completion_fraction": None,
        "basket_aligned_fraction": None, "projection_fraction_raw": None,
        "projection_fraction_clipped": None, "common_fraction_feasible_interval": None,
        "buy_side_fraction_raw": None, "sell_side_fraction_raw": None,
        "normalized_residual_rms": None, "observed_notional_coverage": 0.0,
        "supported_stock_count": 0, "excluded_rows": [], "attribution_scenarios": [],
        "assumptions": {"common_fraction_across_stocks": True,
                        "no_reversal_and_no_overtrade": bounded,
                        "frozen_remaining_target_at_window_start": True},
        "warnings": [
            "Untagged flows cannot distinguish ETF executions from other investors or front-runners.",
            "Error bounds are user-supplied sensitivity inputs, not confidence intervals.",
            "Separate event fits may reuse overlapping flows; do not sum event attributions.",
            "A remaining exposure gap is not a guaranteed closing-auction cash order; creations, redemptions and derivatives remain unresolved.",
        ],
        "rows": [],
    }
    if not bounded:
        output["warnings"].append("No bounded completion or remaining-share range without both no-reversal and no-overtrade assumptions.")
    snapshots = [] if observations is None else (observations if isinstance(observations, list) else [observations])
    candidates = []
    for snapshot in snapshots:
        measured, available = _known(snapshot, requested, "observation", allow_future=True)
        if available > requested:
            output["unavailable_snapshot_count"] += 1
            continue
        if snapshot.get("event_id") != event_id or _time(snapshot.get("window_start"), "observation.window_start") != start:
            raise ValueError("observation event_id/window_start does not match the frozen plan")
        if snapshot.get("kind") != "cumulative" or measured < start or measured > end:
            raise ValueError("observation must be cumulative within the trading window")
        _rows(snapshot)  # A duplicate in a superseded snapshot is still invalid input.
        candidates.append((measured, available, snapshot))
    selected = max(candidates, key=lambda item: (item[0], item[1])) if candidates else None
    supported = []
    selected_snapshot = None
    if selected:
        measured, _, selected_snapshot = selected
        output["observed_through"] = measured.isoformat()
        output["stale_seconds"] = (requested - measured).total_seconds()
        for code, row in _rows(selected_snapshot).items():
            if code not in target or not target[code]["required"]:
                output["excluded_rows"].append({"code": code, "reason": "outside_nonzero_plan_support"})
                continue
            needed = ("net_buy_shares", "background_shares", "error_bound_shares")
            if any(row.get(field) is None for field in needed):
                output["excluded_rows"].append({"code": code, "reason": "missing_flow_background_or_error"})
                continue
            net = _number(row["net_buy_shares"], "net_buy_shares")
            background = _number(row["background_shares"], "background_shares")
            error = _number(row["error_bound_shares"], "error_bound_shares", positive=True)
            signal = _number(net - background, "background-adjusted signal")
            supported.append({"code": code, **target[code], "signal": signal, "error": error})
        output["status"] = "insufficient_support"
    output["supported_stock_count"] = len(supported)
    output["observed_notional_coverage"] = math.fsum(abs(r["required"]) * r["price"] for r in supported) / total_notional
    if supported:
        raw = _projection(supported)
        output["projection_fraction_raw"] = raw
        residual = math.hypot(*((r["signal"] - raw * r["required"]) / r["error"] for r in supported)) / math.sqrt(len(supported))
        output["normalized_residual_rms"] = _number(residual, "normalized_residual_rms")
        buys = [r for r in supported if r["required"] > 0]
        sells = [r for r in supported if r["required"] < 0]
        output["buy_side_fraction_raw"] = _projection(buys) if buys else None
        output["sell_side_fraction_raw"] = _projection(sells) if sells else None
        if len(supported) >= 3 and buys and sells:
            intervals = [sorted(((r["signal"] - r["error"]) / r["required"],
                                 (r["signal"] + r["error"]) / r["required"])) for r in supported]
            lower = max(i[0] for i in intervals)
            upper = min(i[1] for i in intervals)
            if lower > upper:
                output["status"] = "model_conflict"
                output["warnings"].append("No common fraction satisfies every observed row's supplied error bound.")
            else:
                output["status"] = "basket_alignment_only"
                output["common_fraction_feasible_interval"] = [lower, upper]
                # A feasible representative may differ from the unconstrained WLS fit.
                output["basket_aligned_fraction"] = min(upper, max(lower, raw))
                if output["observed_notional_coverage"] < 1.0:
                    output["warnings"].append("Unobserved stocks are extrapolated only under the common-fraction assumption.")
    alpha = output["basket_aligned_fraction"]
    interval = output["common_fraction_feasible_interval"]
    for code, row in target.items():
        required = row["required"]
        output["rows"].append({
            "code": code, "name": plan_rows[code].get("name"), "required_shares": required,
            "actual_etf_pretrade_shares": None,
            "basket_aligned_shares": None if alpha is None else alpha * required,
            "conditional_common_fraction_remaining_range": None,
            "remaining_shares_unidentified_range": sorted((0.0, required)) if bounded else None,
        })
    if selected_snapshot:
        for scenario in selected_snapshot.get("attribution_scenarios", []):
            label = _identifier(scenario.get("label"), "scenario.label")
            rho = _number(scenario.get("etf_share"), "scenario.etf_share", nonnegative=True)
            if rho > 1:
                raise ValueError("scenario.etf_share must be between 0 and 1")
            scenario_interval, scenario_fraction = None, None
            scenario_status = "model_unavailable"
            if interval is not None:
                lower, upper = (rho * a for a in interval)
                if bounded:
                    lower, upper = max(0.0, lower), min(1.0, upper)
                if lower > upper:
                    scenario_status = "model_conflict"
                else:
                    scenario_status = "conditional_attribution"
                    scenario_interval = [lower, upper]
                    scenario_fraction = min(upper, max(lower, rho * output["projection_fraction_raw"]))
            output["attribution_scenarios"].append({
                "label": label, "etf_share": rho, "interpretation": "conditional_attribution_not_identified_execution",
                "status": scenario_status, "conditional_completion_fraction": scenario_fraction,
                "conditional_completion_interval": scenario_interval,
                "rows": [{"code": code, "status": scenario_status,
                          "conditional_pretrade_shares": None if scenario_fraction is None else scenario_fraction * r["required"],
                          "conditional_remaining_shares": None if scenario_fraction is None else (1 - scenario_fraction) * r["required"],
                          "conditional_pretrade_range": None if scenario_interval is None else sorted(a * r["required"] for a in scenario_interval),
                          "conditional_remaining_range": None if scenario_interval is None else sorted((1 - a) * r["required"] for a in scenario_interval)}
                         for code, r in target.items()],
            })
    return output


def composition_transition(baseline, current, *, as_of):
    """Compare complete per-unit baskets after verified corporate-action corrections.

    Current factors map baseline stock/ETF units into current units. A composition
    change is exposure evidence; it cannot prove exchange trading or its timing.
    """
    requested = _time(as_of, "as_of")
    times = []
    snapshots = []
    for label, snapshot in (("baseline", baseline), ("current", current)):
        times.append(_known(snapshot, requested, label)[0])
        if snapshot.get("complete") is not True:
            raise ValueError(f"{label} must explicitly declare complete:true")
        if snapshot.get("corporate_actions_verified") is not True:
            raise ValueError(f"{label} needs verified corporate actions")
        if snapshot.get("kind") not in ("pdf", "holdings"):
            raise ValueError("snapshot kind must be pdf or holdings")
        _identifier(snapshot.get("etf_code"), "etf_code")
        cu = _number(snapshot.get("cu_units"), "cu_units", positive=True)
        units = _number(snapshot.get("etf_units"), "etf_units", positive=True)
        counts = {code: _number(row.get("count"), "count", nonnegative=True) for code, row in _rows(snapshot).items()}
        snapshots.append((cu, units, counts))
    if baseline["etf_code"] != current["etf_code"] or times[1] <= times[0]:
        raise ValueError("snapshots need the same ETF and strictly increasing as_of times")
    old_cu, _, old_counts = snapshots[0]
    new_cu, new_units, new_counts = snapshots[1]
    etf_split = _number(current.get("etf_split_factor", 1), "etf_split_factor", positive=True)
    stock_splits = {code: _number(factor, "stock_split_factor", positive=True)
                    for code, factor in current.get("stock_split_factors", {}).items()}
    result = []
    for code in sorted(set(old_counts) | set(new_counts)):
        before = _number(old_counts.get(code, 0.0) / old_cu * stock_splits.get(code, 1.0) / etf_split, "adjusted baseline exposure")
        after = _number(new_counts.get(code, 0.0) / new_cu, "current exposure")
        delta = after - before
        result.append({"code": code, "baseline_shares_per_etf_share_adjusted": before,
                       "current_shares_per_etf_share": after, "per_etf_share_change": delta,
                       "exposure_transition_shares": _number(new_units * delta, "exposure_transition_shares"),
                       "actual_market_execution_shares": None})
    return {"etf_code": current["etf_code"], "as_of": requested.isoformat(),
            "baseline_as_of": times[0].isoformat(), "current_as_of": times[1].isoformat(),
            "classification": "composition_proxy" if "pdf" in (baseline["kind"], current["kind"]) else "holdings_exposure",
            "actual_market_execution_shares": None,
            "warnings": ["Complete basket composition changes do not distinguish market trading from in-kind flows or identify intraday execution timing."],
            "rows": result}
