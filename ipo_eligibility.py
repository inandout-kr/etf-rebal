"""First-15-session IPO checks for KRX index methodology 8.2.

Official float market caps must include exchange lock-up treatment. ETF-implied
FIF is deliberately not accepted here. A passing size estimate is never enough
to clear missing eligibility/universe evidence.
"""
import math
from datetime import datetime, timedelta

from calendar_events import is_bday


def _ymd(value):
    return datetime.strptime(str(value).replace("-", ""), "%Y%m%d").strftime("%Y%m%d")


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _nonnegative(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def first_ipo_window(con, listing_date, as_of):
    """Fix the first trading dates; missing data must not move the window."""
    day = datetime.strptime(_ymd(listing_date), "%Y%m%d").date()
    cutoff = datetime.strptime(_ymd(as_of), "%Y%m%d").date()
    sessions = []
    while day <= cutoff and len(sessions) < 15:
        if is_bday(day):
            sessions.append(day.strftime("%Y%m%d"))
        day += timedelta(days=1)
    return sessions


def assess_first15(con, stock, market_stocks, as_of, threshold_rank, official_float_history=None, formal_checks=None):
    sessions = first_ipo_window(con, stock["listing_date"], as_of)
    result = {"status": "HOLD", "eligible": False, "official_result": False, "hold_reasons": [],
              "first15_start": sessions[0] if sessions else None, "first15_end": sessions[-1] if sessions else None,
              "first15_n_days": 0, "first15_market_sessions": len(sessions), "avg_mcap15": None, "rank15": None,
              "threshold_rank": threshold_rank, "threshold_mcap": None, "official_float_status": "HOLD",
              "avg_official_float_mcap15": None, "required_float_mcap": None,
              "total_cap_condition": None, "float_condition": None, "formal_checks_status": "HOLD",
              "rank_basis": "상장일부터 첫 15개 완료 시장 매매일의 동일기간 일평균 총시총",
              "rank_universe_basis": "현재 저장된 시장 보통주 유니버스; 당시 공식 유니버스 확인 대기"}
    marks = ",".join("?" for _ in sessions)
    if sessions:
        observed_sessions = {r[0] for r in con.execute(
            f"SELECT date FROM daily WHERE date IN ({marks}) GROUP BY date HAVING MAX(trdval)>0", sessions)}
        result["first15_n_days"] = con.execute(
            f"SELECT COUNT(*) FROM daily WHERE code=? AND date IN ({marks}) AND close*shares>0",
            [stock["code"], *sessions]).fetchone()[0]
    else:
        observed_sessions = set()
    result["first15_observed_market_sessions"] = len(observed_sessions)
    if len(sessions) < 15:
        result["hold_reasons"].append(f"상장일부터 첫 15개 완료 시장 매매일 미충족 ({len(sessions)}/15)")
        return result
    if len(observed_sessions) != 15:
        result["hold_reasons"].append("첫 15개 시장 매매일 중 완료 시장자료 누락·전 시장 거래대금 0; 후속 일자로 대체하지 않음")
        return result
    stats = {r[0]: {"avg": r[1], "n": r[2]} for r in con.execute(
        f"SELECT code, AVG(close*shares), COUNT(*) FROM daily WHERE date IN ({marks}) AND close*shares>0 GROUP BY code",
        sessions)}
    candidate = stats.get(stock["code"], {})
    result["first15_n_days"] = candidate.get("n", 0)
    if candidate.get("n") != 15 or not _positive(candidate.get("avg")):
        result["hold_reasons"].append("첫 15개 완료 시장 매매일의 종목 가격·주식수 관측 누락")
        return result
    result["avg_mcap15"] = candidate["avg"]
    members = [s for s in market_stocks if not s.get("listing_date") or _ymd(s["listing_date"]) <= sessions[-1]]
    observed = [(s["code"], stats[s["code"]]["avg"]) for s in members
                if stats.get(s["code"], {}).get("n") == 15 and _positive(stats[s["code"]]["avg"])]
    missing = sorted(s["code"] for s in members if not stats.get(s["code"], {}).get("n") == 15)
    result["comparison_missing_codes"] = missing
    result["comparison_observed"] = len(observed)
    result["comparison_expected"] = len(members)
    if missing:
        result["hold_reasons"].append(f"동일기간 시장 비교종목 15일 관측 누락 {len(missing)}개")
    # Equal market caps share a rank; ticker ordering cannot disqualify a tie.
    result["rank15"] = 1 + sum(value > candidate["avg"] for _, value in observed)
    if len(observed) < threshold_rank:
        result["hold_reasons"].append(f"동일기간 시장 {threshold_rank}위 시총 관측 부족")
        return result
    threshold = sorted((value for _, value in observed), reverse=True)[threshold_rank - 1]
    result["threshold_mcap"] = threshold
    result["required_float_mcap"] = threshold * 0.5
    result["total_cap_condition"] = result["rank15"] <= threshold_rank if not missing else None
    float_record = (official_float_history or {}).get(stock["code"], {})
    observations = float_record.get("observations", {}) if isinstance(float_record, dict) else {}
    observations = observations if isinstance(observations, dict) else {}
    if (isinstance(float_record, dict) and float_record.get("official") is True and float_record.get("source")
            and all(_nonnegative(observations.get(day)) for day in sessions)):
        avg_float = sum(observations[day] for day in sessions) / 15
        result.update(official_float_status="verified", avg_official_float_mcap15=avg_float,
                      float_condition=avg_float >= result["required_float_mcap"])
    else:
        result["hold_reasons"].append("거래소 의무보유 처리를 반영한 첫 15일 공식 일평균 유동시총 확인 필요")
    formal = (formal_checks or {}).get(stock["code"], {})
    formal = formal if isinstance(formal, dict) else {}
    if formal.get("verified") is True and formal.get("universe_verified") is True and formal.get("source"):
        result["formal_checks_status"] = "verified"
        result["rank_universe_basis"] = formal["source"]
    else:
        result["hold_reasons"].append("당시 공식 보통주 유니버스·기타 편입자격 확인 필요")
    if result["total_cap_condition"] is False or result["float_condition"] is False:
        result["status"] = "NOT_ELIGIBLE"
    elif not result["hold_reasons"] and result["total_cap_condition"] and result["float_condition"]:
        result.update(status="ELIGIBLE", eligible=True)
    return result
