# -*- coding: utf-8 -*-
"""Read-only quality gate used before building and deploying the site."""
import argparse
import hashlib
import json
import math
import sqlite3
from contextlib import closing
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from market_dates import anchor_stocks, completed_session_date, current_universe, latest_completed_date, usable_fx

ROOT = Path(__file__).parent
REQUIRED = ("analysis", "holdings", "etf_master", "methodology", "flows", "krx_semi_rebal", "backtest_june")


class DataQualityError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise DataQualityError(message)


def finite_tree(value, path="data"):
    if isinstance(value, float):
        require(math.isfinite(value), f"{path}: NaN/Infinity 값")
    elif isinstance(value, dict):
        for key, child in value.items():
            finite_tree(child, f"{path}.{key}")
    elif isinstance(value, list):
        for i, child in enumerate(value):
            finite_tree(child, f"{path}[{i}]")


def parse_date(value):
    try:
        return datetime.strptime(value, "%Y%m%d").date() if len(value) == 8 else date.fromisoformat(value[:10])
    except (ValueError, TypeError, AttributeError):
        raise DataQualityError(f"잘못된 기준일: {value!r}") from None


def validate_flow_rows(rows, label, target_count=None, total_buy=None, total_sell=None):
    require(bool(rows), f"{label}: 수급 행 없음")
    codes = [r.get("code") for r in rows]
    require(all(codes) and len(set(codes)) == len(codes), f"{label}: 누락/중복 종목코드")
    # Published weights have three decimals; flow amounts have one decimal (억원).
    weight_tolerance = len(rows) * .00051 + .01
    require(abs(sum(r["w_target"] for r in rows) - 100) <= weight_tolerance,
            f"{label}: 목표 비중 합계가 100%가 아님")
    if target_count is not None:
        positive = sum(r["w_target"] > 0 for r in rows)
        require(positive == target_count, f"{label}: 목표 종목 수 불일치 ({positive}/{target_count})")
    for row in rows:
        require(0 <= row["w_target"] <= 100, f"{label}/{row['code']}: 목표 비중 범위 오류")
        current = row.get("w_cur_adj", row.get("w_cur"))
        if current is not None:
            require(0 <= current <= 100, f"{label}/{row['code']}: 현재 비중 범위 오류")
            require(abs(row["delta"] - (row["w_target"] - current)) <= .0016,
                    f"{label}/{row['code']}: 비중 변화량 불일치")
        by_etf = row.get("flow_by_etf", {})
        require(bool(by_etf), f"{label}/{row['code']}: ETF별 수급 없음")
        require(abs(sum(by_etf.values()) - row["flow_total"]) <= .051 * (len(by_etf) + 1),
                f"{label}/{row['code']}: ETF별 수급 합계 불일치")
    for expected, sign in ((total_buy, 1), (total_sell, -1)):
        if expected is not None:
            actual = sum(r["flow_total"] for r in rows if r["flow_total"] * sign > 0)
            require(abs(actual - expected) <= .051, f"{label}: 매수/매도 합계 불일치")
    balance_tolerance = sum(.051 * (len(r["flow_by_etf"]) + 1) for r in rows)
    require(abs(sum(r["flow_total"] for r in rows)) <= balance_tolerance,
            f"{label}: 매수/매도 수급 보존 불일치")


def validate_artifacts(artifacts, now=None):
    """Validate serialized contracts without opening a live source or modifying files."""
    from fetch_holdings import PROXY, validate_holdings

    for name in REQUIRED:
        require(bool(artifacts.get(name)), f"필수 자료 누락: {name}.json")
        finite_tree(artifacts[name], name)
    a, hold, flows = (artifacts[k] for k in ("analysis", "holdings", "flows"))
    msci = a.get("msci")
    if msci and not usable_fx(a.get("usdkrw"), a.get("data_quality", {}).get("fx", {}), now):
        require(msci.get("availability") == "unavailable" and not msci.get("candidates")
                and not msci.get("smallest_current") and msci.get("reason") and msci.get("usdkrw") is None,
                "미검증 환율로 계산된 MSCI 결과: 재분석 필요")
    as_of = parse_date(a.get("last_daily"))
    cutoff = completed_session_date(now)
    require(as_of <= cutoff, "미완료 거래일이 분석에 포함됨")
    require((cutoff - as_of).days <= 7, "시장 기준일이 7일 이상 지연됨")
    warnings = []
    if as_of < cutoff:
        warnings.append(f"시장 자료 기준일 {as_of.isoformat()} (최근 완료 거래일 확인 필요)")
    generated = parse_date(a.get("generated"))
    require(generated >= as_of and (cutoff - generated).days <= 7, "분석 생성일 오류 또는 장기 지연")
    require(a.get("generated") == flows.get("generated"), "분석과 수급의 생성 시각이 다름; 수급 재계산 필요")
    require(flows.get("data_through") == a["last_daily"], "수급 기준일이 분석과 다름; 수급 재계산 필요")
    for key, count in (("kospi200", 200), ("kosdaq150", 150)):
        result = a.get(key) or {}
        require(result.get("n_selected") == count, f"{key}: 목표 종목 수 오류")
        excluded = result.get("excluded_current", [])
        require(result.get("n_current", 0) - len(excluded) + len(result.get("adds", [])) - len(result.get("dels", [])) == count,
                f"{key}: 현재/편입/편출 종목 수 불일치")
        if excluded:
            warnings.append(f"{key}: 현 구성 {len(excluded)}종목 심사 대상 제외 (상세 사유 확인)")
        require(len(result.get("all", [])) >= count, f"{key}: 분석 종목 누락")
        require(parse_date(result.get("data_through")) <= as_of, f"{key}: 미완료 분석 기준일")
    require(set(PROXY) <= set(hold), "필수 프록시 ETF 구성 자료 누락")
    stale = []
    for key, entry in hold.items():
        try:
            validate_holdings(entry)
        except (ValueError, TypeError) as exc:
            raise DataQualityError(f"{key}: {exc}") from exc
        lag = (as_of - parse_date(entry["date"])).days
        require(lag <= 7, f"{key}: ETF 구성 자료가 7일 이상 지연됨")
        if lag > 1:
            stale.append(key)
    if stale:
        warnings.append(f"ETF 구성 자료 지연 {len(stale)}개: {', '.join(stale[:5])}")
    require(isinstance(artifacts["etf_master"], list) and artifacts["methodology"], "ETF/방법론 자료 오류")
    events = flows.get("events") or []
    require(bool(events), "수급 이벤트 없음")
    require(len({e["key"] for e in events}) == len(events), "수급 이벤트 중복")
    expected_flows = defaultdict(float)
    for event in events:
        validate_flow_rows(event["rows"], event["key"], event["n_target"], event["total_buy"], event["total_sell"])
        if not event.get("passed"):
            for row in event["rows"]:
                expected_flows[row["code"]] += row["flow_total"]
    for code, value in flows.get("by_stock", {}).items():
        require(abs(sum(e["flow"] for e in value["events"]) - value["total"]) <= .051,
                f"{code}: 종목별 합산 수급 불일치")
    semi = artifacts["krx_semi_rebal"]
    require(semi.get("data_through") == a["last_daily"], "KRX 반도체 기준일이 분석과 다름; 재계산 필요")
    require(bool(semi.get("scenarios")), "KRX 반도체 시나리오 누락")
    for key, scenario in semi["scenarios"].items():
        if scenario.get("unavailable"):
            require(semi.get("availability") == "pending_review" and key in ("cap10", "literal")
                    and not scenario.get("rows") and scenario.get("n_selected") is None and scenario.get("reason"),
                    f"krx_semi/{key}: 잘못된 계산 대기 상태")
            continue
        validate_flow_rows(scenario["rows"], f"krx_semi/{key}", scenario["n_selected"],
                           scenario["total_buy_eok"], scenario["total_sell_eok"])
    if semi.get("availability") == "pending_review":
        warnings.append("KRX 반도체: 다음 심사기간 시작 전; 현재 구성 유지 가정으로 수급 계산")
    if parse_date(semi["expiry"]) >= generated:
        for row in semi["scenarios"]["current"]["rows"]:
            if row["flow_total"]:
                expected_flows[row["code"]] += row["flow_total"]
    require(set(expected_flows) == set(flows.get("by_stock", {})), "종목별 합산 수급의 종목 누락/중복")
    for code, expected in expected_flows.items():
        require(abs(expected - flows["by_stock"][code]["total"]) <= .051,
                f"{code}: 이벤트와 종목별 합산 수급이 다름")
    return warnings


def read_artifacts(data_dir):
    artifacts = {}
    for name in REQUIRED:
        path = data_dir / f"{name}.json"
        require(path.is_file(), f"필수 파일 없음: {path.name}")
        try:
            artifacts[name] = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise DataQualityError(f"{path.name}: 읽기 실패 ({exc})") from exc
    return artifacts


def validate_directory(data_dir=ROOT / "data", now=None, artifacts=None):
    data_dir = Path(data_dir).resolve()
    artifacts = artifacts or read_artifacts(data_dir)
    warnings = validate_artifacts(artifacts, now)
    a = artifacts["analysis"]
    db = data_dir / "market.sqlite"
    require(db.is_file(), "시장 데이터베이스 없음")
    with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        last = latest_completed_date(con, now)
        require(last == a["last_daily"], "분석 기준일과 완료된 시장 데이터가 다름; 분석 재실행 필요")
        stocks = {r["code"]: dict(r) for r in con.execute("SELECT * FROM stock")}
        metadata = dict(con.execute("SELECT k,v FROM meta"))
        codes, universe = current_universe(con, stocks, metadata)
        require(universe["status"] != "unavailable", "현재 유니버스 기록 확인 불가: 검증된 수집·재분석 필요")
        current = {c for c in codes if stocks[c]["is_common"]}
        anchored = anchor_stocks(con, stocks, last, current=True, metadata=metadata)
        total = len(current)
        present = sum(anchored[c]["review_ready"] for c in current)
        require(total > 0 and present / total >= .9, f"완료 거래일 시세 수집 범위 부족 ({present}/{total})")
        if present < total:
            missing = sorted(c for c in current if not anchored[c]["review_ready"])
            warnings.append(f"현재 유니버스 기준일 시세 누락 {total - present}종목 (수집률 {present / total:.2%}): {', '.join(missing)}")
        departed = sum(s["is_common"] and c not in codes for c, s in stocks.items())
        if departed:
            warnings.append(f"현재 유니버스 밖 {departed}종목은 커버리지에서 제외하고 과거 기록을 보존했습니다.")
        if a.get("stock_data"):
            require(all(a["stock_data"].get(c, {}).get("universe_active") == s.get("universe_active")
                        and a["stock_data"].get(c, {}).get("price_as_of") == s.get("price_as_of")
                        for c, s in anchored.items() if s["is_common"]), "종목별 데이터 기준일/유니버스 불일치: 재분석 필요")
        for key, title, field in (("market_collection", "일별 시세", "failed_codes"),
                                  ("gics_collection", "GICS 분류", "failed_groups"),
                                  ("range_collection", "과거 시세", "failed_codes")):
            if key in metadata:
                state = json.loads(metadata[key])
                if state.get(field):
                    warnings.append(f"{title} 수집 실패 {len(state[field])}개: 마지막 정상 자료 사용")
                if key == "market_collection" and state.get("info_failed"):
                    warnings.append(f"종목 기본정보 수집 실패 {state['info_failed']}개: 마지막 정상 자료 사용")
                if key == "market_collection" and state.get("wics_matched") == 0:
                    warnings.append("WICS 분류 수집 실패: 마지막 정상 자료 사용")
            elif key == "market_collection":
                warnings.append("이전 수집 자료: 소스별 수집 상태 기록 없음")
    collection = data_dir / "holdings_collection.json"
    if collection.exists():
        state = json.loads(collection.read_text(encoding="utf-8"))
        for key, value in state.get("sources", {}).items():
            require(value.get("status") != "missing", f"{key}: ETF 수집 자료 없음")
        if state.get("warning_count"):
            warnings.append(f"ETF 구성 수집 실패 {state['warning_count']}개: 마지막 정상 자료 사용")
    quality = dict(a.get("data_quality") or {})
    warnings = list(dict.fromkeys(list(quality.get("warnings", [])) + warnings))
    quality.update(status="warning" if warnings else "ok", warnings=warnings, as_of=parse_date(a["last_daily"]).isoformat())
    return quality


def validate_dist(data_dir, dist_dir, now=None):
    artifacts = read_artifacts(data_dir)
    quality = validate_directory(data_dir, now, artifacts)
    payload_path = Path(dist_dir) / "data.json"
    html_path = Path(dist_dir) / "index.html"
    require(payload_path.is_file() and html_path.is_file(), "빌드 결과 없음")
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    finite_tree(payload)
    require(payload["analysis"].get("data_quality") == quality, "빌드 후 수집 품질 상태 변경; 재빌드 필요")
    sources = payload.get("_build", {}).get("sources", {})
    require(bool(sources), "빌드 원본 검증 정보 없음; 재빌드 필요")
    root = Path(data_dir).resolve().parent
    for filename, digest in sources.items():
        source = (root / filename).resolve()
        require(source.is_relative_to(root) and source.is_file(), "빌드 원본 파일 누락 또는 잘못된 경로")
        require(hashlib.sha256(source.read_bytes()).hexdigest() == digest, f"빌드 후 원본 변경: {filename}")
    require(payload["analysis"]["generated"] == artifacts["analysis"]["generated"], "빌드 결과가 최신 분석과 다름")
    require(payload["analysis"]["last_daily"] == artifacts["analysis"]["last_daily"], "빌드 결과 기준일 불일치")
    html = html_path.read_text(encoding="utf-8")
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
    require(serialized in html and "/*__DATA__*/null" not in html, "HTML과 데이터 파일이 다름; 재빌드 필요")
    return quality


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--dist-dir", type=Path)
    args = parser.parse_args()
    try:
        quality = validate_dist(args.data_dir, args.dist_dir) if args.dist_dir else validate_directory(args.data_dir)
    except (DataQualityError, KeyError, TypeError, sqlite3.Error) as exc:
        raise SystemExit(f"DATA QUALITY FAILED: {exc}") from exc
    print(f"data quality: {quality['status']} ({quality['as_of']})")
    for warning in quality["warnings"]:
        print(f"  ! {warning}")


if __name__ == "__main__":
    main()
