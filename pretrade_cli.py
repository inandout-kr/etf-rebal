"""Freeze existing residual ETF demand and run the offline pretrade estimator."""
import argparse
import hashlib
import json
import math
import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


KST = timezone(timedelta(hours=9))


def _read(path):
    with Path(path).open(encoding="utf-8-sig") as stream:
        return json.load(stream)


def _aware(value, name):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an ISO timestamp with timezone") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed


def _source_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=KST)


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def _price_date(value):
    if not value:
        raise ValueError("Source has no price date; supply --price-date YYYY-MM-DD")
    value = str(value)
    if len(value) == 8 and value.isdigit():
        value = f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return date.fromisoformat(value).strftime("%Y%m%d")


def _select(source, event_id, scenario):
    if "events" in source:
        matches = [event for event in source["events"] if event.get("key") == event_id]
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one event {event_id!r}; found {len(matches)}")
        event = matches[0]
    else:
        source_id = source.get("key")
        if source_id is None and source.get("index") == "KRX 반도체":
            source_id = "krx_semi"
        if source_id != event_id:
            raise ValueError(f"Source event is {source_id!r}, not {event_id!r}")
        event = source
    scenarios = event.get("scenarios")
    if scenarios is not None:
        scenario = scenario or event.get("default_scenario") or "current"
        if scenario not in scenarios:
            raise ValueError(f"Unknown scenario {scenario!r}; choose from {list(scenarios)}")
        selected = scenarios[scenario]
    else:
        if scenario not in (None, "base"):
            raise ValueError("This source has only the base scenario")
        scenario, selected = "base", event
    if not isinstance(selected.get("rows"), list):
        raise ValueError("Selected source must contain a rows list")
    return event, scenario, selected


def freeze_plan(source_path, event_id, window_start, available_at, prices_db,
                scenario=None, event_kind="regular", price_date=None, target_known_at=None):
    """Return a plan; the source is residual demand, never original gross demand."""
    start = _aware(window_start, "window_start")
    available = _aware(available_at, "available_at")
    target_known = _aware(target_known_at, "target_known_at") if target_known_at is not None else available
    if target_known > start:
        raise ValueError("Target was not known at window_start; supply an actual prior --target-known-at or move --window-start")
    if target_known > available:
        raise ValueError("target_known_at must not be later than plan available_at")
    if event_kind not in ("regular", "ad_hoc"):
        raise ValueError("event_kind must be regular or ad_hoc")
    source_bytes = Path(source_path).read_bytes()
    source = json.loads(source_bytes.decode("utf-8-sig"))
    event, scenario, selected = _select(source, event_id, scenario)
    snapshot = event.get("snapshot") or {}
    stamps = {"generated": source.get("generated"),
              "event_generated": event.get("generated"),
              "snapshot_generated": snapshot.get("generated"),
              "snapshot_saved": snapshot.get("saved")}
    for key, value in stamps.items():
        stamp = _source_time(value)
        if stamp and available < stamp:
            raise ValueError(f"available_at precedes source {key} ({value}); backdating is prohibited")
    price_day = _price_date(price_date or event.get("data_through") or snapshot.get("data_through")
                            or source.get("data_through") or event.get("date") or source.get("date"))
    close_known = datetime.strptime(price_day, "%Y%m%d").replace(hour=15, minute=30, tzinfo=KST)
    if available < close_known:
        raise ValueError("available_at precedes the reference price date's closing time")
    window = event.get("window") or {}
    trade_date = event.get("trade_date") or window.get("trade_date")
    trade_end = event.get("trade_end") or event.get("settle_date") or window.get("trade_end") or trade_date
    if not trade_date or not trade_end:
        raise ValueError("Source must identify trade_date and trade_end (or settle_date)")
    source_trade_end = trade_end
    trade_date = date.fromisoformat(trade_date).isoformat()
    trade_end = datetime.combine(date.fromisoformat(trade_end), datetime.min.time(), tzinfo=KST).replace(hour=15, minute=20).isoformat()
    warnings = ["residual_latest_pdf_not_original_gross_demand",
                "pretrade_before_window_start_unknown",
                "source_notional_may_include_derivative_exposure_not_cash_orders",
                "no_reversal_and_no_overtrade_are_unverified_model_assumptions"]
    if target_known_at is not None:
        warnings.append("target_known_at_is_user_attested_not_independently_verified")
    if any(value and _source_time(value) is None for value in stamps.values()):
        warnings.append("unparseable_source_timestamp_availability_not_verified")
    if not any(_source_time(value) for value in stamps.values()):
        warnings.append("source_timestamp_missing_availability_not_verified")
    rows, provenance_rows, missing, seen = [], [], [], set()
    uri = Path(prices_db).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as con:
        for original in selected["rows"]:
            code = original.get("code")
            if not isinstance(code, str) or not code or code in seen:
                raise ValueError(f"Security codes must be unique nonempty strings: {code!r}")
            seen.add(code)
            flow = _number(original.get("flow_total"), f"{code}.flow_total")
            by_etf = original.get("flow_by_etf") or {}
            if not isinstance(by_etf, dict):
                raise ValueError(f"{code}.flow_by_etf must be an object")
            decomposition = {etf: _number(value, f"{code}.{etf}") for etf, value in by_etf.items()}
            buy = sum(value for value in decomposition.values() if value > 0)
            sell = sum(value for value in decomposition.values() if value < 0)
            provenance_rows.append({"code": code, "flow_total_eok": flow, "flow_by_etf_eok": decomposition,
                                    "gross_etf_buy_eok": buy, "gross_etf_sell_eok": sell})
            if buy and sell and "offsetting_etf_flows" not in warnings:
                warnings.append("offsetting_etf_flows")
            if flow == 0:
                continue
            price_rows = con.execute("SELECT close FROM daily WHERE code = ? AND date = ?", (code, price_day)).fetchall()
            if len(price_rows) != 1:
                missing.append(code)
                continue
            price = _number(price_rows[0][0], f"{code}.close")
            if price <= 0:
                missing.append(code)
                continue
            rows.append({"code": code, "name": original.get("name", code),
                         "required_shares": flow * 100_000_000 / price, "reference_price": price})
    if missing:
        raise ValueError(f"Missing/ambiguous/nonpositive closing prices on {price_day}: {', '.join(missing)}")
    if not rows:
        raise ValueError("Selected source has no nonzero net demand rows to model")
    return {"event_id": event_id, "event_kind": event_kind,
            "window_start": start.isoformat(), "available_at": available.isoformat(),
            "target_known_at": target_known.isoformat(),
            "trade_date": trade_date, "trade_end": trade_end, "scope": "remaining_at_window_start",
            "assumption_no_reversal": True, "assumption_no_overtrade": True,
            "assumption_cash_completion_at_close": False, "rows": rows, "warnings": warnings,
            "provenance": {"source_file": str(Path(source_path).resolve()),
                           "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
                           "scenario": scenario, "source_timestamps": stamps,
                           "target_known_at": target_known.isoformat(),
                           "target_known_at_basis": "user_attestation" if target_known_at is not None else "plan_available_at_default",
                           "target_known_at_independently_verified": False,
                           "legacy_naive_source_timezone": "Asia/Seoul",
                           "price_date": price_day,
                           "source_trade_date": trade_date, "source_trade_end": source_trade_end,
                           "intraday_cutoff": "15:20:00 Asia/Seoul; closing auction excluded",
                           "source_pdf_date": event.get("pdf_date") or snapshot.get("pdf_date"),
                           "event_note": event.get("note"), "source_rules": event.get("rules"),
                           "source_etfs": selected.get("etfs") or event.get("etfs"),
                           "rows": provenance_rows}}


def _write(path, result, *, inputs=(), immutable=False):
    target = Path(path).resolve()
    if any(target == Path(item).resolve() for item in inputs if item):
        raise ValueError("Output must not overwrite an input file")
    payload = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if immutable:
        with target.open("x", encoding="utf-8") as stream:
            stream.write(payload)
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                         prefix=target.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            stream.write(payload)
        os.replace(temporary, target)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze", help="Create an immutable residual-demand plan")
    freeze.add_argument("--source", default="data/flows.json")
    freeze.add_argument("--event", required=True)
    freeze.add_argument("--event-kind", choices=("regular", "ad_hoc"), default="regular")
    freeze.add_argument("--window-start", required=True)
    freeze.add_argument("--available-at", required=True)
    freeze.add_argument("--target-known-at", help="Assert when this target was actually known; defaults to available-at")
    freeze.add_argument("--prices-db", default="data/market.sqlite")
    freeze.add_argument("--price-date")
    freeze.add_argument("--scenario")
    freeze.add_argument("--out", required=True)
    estimate = commands.add_parser("estimate", help="Estimate using latest available cumulative observations")
    estimate.add_argument("--plan", required=True)
    estimate.add_argument("--observations")
    estimate.add_argument("--as-of", required=True)
    estimate.add_argument("--out", required=True)
    composition = commands.add_parser("composition", help="Compare two verified daily compositions")
    composition.add_argument("--baseline", required=True)
    composition.add_argument("--current", required=True)
    composition.add_argument("--as-of", required=True)
    composition.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "freeze":
            result = freeze_plan(args.source, args.event, args.window_start, args.available_at,
                                 args.prices_db, args.scenario, args.event_kind, args.price_date, args.target_known_at)
            _write(args.out, result, inputs=(args.source, args.prices_db), immutable=True)
        elif args.command == "estimate":
            from pretrade_estimator import estimate_pretrade
            _aware(args.as_of, "as_of")
            result = estimate_pretrade(_read(args.plan), _read(args.observations) if args.observations else None,
                                       as_of=args.as_of)
            _write(args.out, result, inputs=(args.plan, args.observations))
        else:
            from pretrade_estimator import composition_transition
            _aware(args.as_of, "as_of")
            result = composition_transition(_read(args.baseline), _read(args.current), as_of=args.as_of)
            _write(args.out, result, inputs=(args.baseline, args.current))
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(f"{args.command}: {result.get('status', 'saved')} -> {args.out}")
    print("Actual ETF-attributed intraday executions are not observed; estimates are conditional.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
