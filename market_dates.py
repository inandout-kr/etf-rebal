"""한국 정규장 완료일에 맞춘 분석용 시세 조회 (원본 데이터는 보존)."""
import json
import math
from datetime import datetime, time, timedelta, timezone

from calendar_events import is_bday, prev_business_day

KST = timezone(timedelta(hours=9))
MARKET_CLOSE = time(15, 30)


def korea_now(now=None):
    now = now or datetime.now(KST)
    return now.replace(tzinfo=KST) if now.tzinfo is None else now.astimezone(KST)


def completed_session_date(now=None):
    """장중에는 직전 거래일, 정규장 종료 뒤에는 당일. naive now는 한국시간."""
    now = korea_now(now)
    today = now.date()
    return today if is_bday(today) and now.time() >= MARKET_CLOSE else prev_business_day(today)


def latest_completed_date(con, now=None):
    cutoff = completed_session_date(now).strftime("%Y%m%d")
    # 전 시장 거래대금 0은 장 시작 전 스냅샷. 개별 거래정지 종목의 0은 유지한다.
    dates = con.execute("SELECT date FROM daily WHERE date<=? GROUP BY date HAVING MAX(trdval)>0 ORDER BY date DESC", (cutoff,))
    for row in dates:
        if is_bday(datetime.strptime(row[0], "%Y%m%d").date()):
            return row[0]
    raise ValueError("거래가 완료된 유효 일별 시세가 없습니다. 시세 수집 상태를 확인하세요.")


def completed_adv(con, as_of=None, ndays=20):
    as_of = as_of or latest_completed_date(con)
    return {r[0]: r[1] for r in con.execute(
        """WITH sessions AS (SELECT date FROM daily WHERE date<=? GROUP BY date HAVING MAX(trdval)>0),
        ranked AS (SELECT code, trdval, ROW_NUMBER() OVER (PARTITION BY code ORDER BY date DESC) rn
                   FROM daily WHERE date IN (SELECT date FROM sessions))
        SELECT code, AVG(trdval) FROM ranked WHERE rn<=? GROUP BY code""", (as_of, ndays))}


def current_universe(con, stocks, metadata=None):
    """Read the last validated snapshot; legacy inference never writes/migrates history."""
    metadata = metadata if metadata is not None else dict(con.execute("SELECT k,v FROM meta"))
    try:
        snapshot = json.loads(metadata.get("current_universe", "null"))
        if snapshot is not None:
            codes = snapshot["codes"]
            if (snapshot.get("version") != 1 or not isinstance(codes, list) or not codes
                    or len(codes) != len(set(codes)) or not set(codes) <= stocks.keys()
                    or not snapshot.get("observed_at") or not snapshot.get("source")):
                raise ValueError("invalid current universe snapshot")
            return set(codes), dict(snapshot, status="ok")
        collection = json.loads(metadata.get("market_collection", "null")) or {}
        updated = metadata.get("updated")
        codes = {c for c, s in stocks.items() if updated and s.get("updated") == updated}
        common = sum(bool(stocks[c].get("is_common")) for c in codes)
        if (codes and collection.get("attempted_at") == updated
                and collection.get("requested") == common and common > 0):
            return codes, {"status": "legacy_inferred", "observed_at": updated,
                           "source": "stock.updated + market_collection", "codes": sorted(codes)}
    except (ValueError, TypeError, KeyError):
        pass
    return set(), {"status": "unavailable", "observed_at": None, "source": None, "codes": []}


def anchor_stocks(con, stocks, as_of, current=False, metadata=None):
    """주식 메타데이터에 종목별 마지막 완료 종가·시총을 덮어쓴 메모리 사본."""
    out = {c: dict(s, close=None, mktcap=None, price_as_of=None, price_status="missing") for c, s in stocks.items()}
    rows = con.execute(
        """SELECT d.code, d.close, d.shares, d.date, d.trdval FROM daily d JOIN
           (SELECT code, MAX(date) dt FROM daily WHERE date<=? GROUP BY code) last
           ON d.code=last.code AND d.date=last.dt""", (as_of,))
    for code, close, shares, dt, trdval in rows:
        if code in out:
            valid = (close is not None and shares is not None and close > 0 and shares > 0
                     and trdval is not None and trdval >= 0)
            out[code].update(close=close if valid else None, mktcap=close * shares if valid else None,
                             price_as_of=dt, price_status="current" if valid and dt == as_of else "stale" if valid else "invalid")
    if current:
        codes, state = current_universe(con, stocks, metadata)
        for code, st in out.items():
            st.update(universe_active=code in codes, universe_as_of=state["observed_at"],
                      review_ready=code in codes and st["price_status"] == "current")
            if not st["review_ready"]:
                st.update(last_close=st["close"], last_mktcap=st["mktcap"], close=None, mktcap=None)
    return out


def require_current_prices(stocks, codes, label):
    """A missing holding must never become a fabricated zero target/forced sale."""
    missing = sorted(c for c in codes if not stocks.get(c, {}).get("review_ready"))
    if missing:
        raise ValueError(f"{label}: 현재 유니버스/기준일 가격 확인 필요: {', '.join(missing)}; 수급 계산을 보류합니다.")


def usable_fx(value, quality, now=None):
    """Only a known observation source with a real, nonfuture timestamp permits USD calculations."""
    if (not isinstance(quality, dict) or not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0
            or quality.get("status") not in ("ok", "fallback") or quality.get("source") != "Naver FX_USDKRW"):
        return False
    observed = quality.get("as_of")
    if not isinstance(observed, str) or len(observed) <= 10 or observed[10] not in ("T", " "):
        return False
    try:
        dt = datetime.fromisoformat(observed.replace("Z", "+00:00"))
        return korea_now(dt) <= korea_now(now)
    except ValueError:
        return False
