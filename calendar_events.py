# -*- coding: utf-8 -*-
"""지수 정기변경 일정 계산 (공식 방법론의 날짜 규칙 → 실제 달력 일자)

핵심 기준일:
  - KOSPI200 선물 결제월 최종거래일 = 해당 월 두 번째 목요일 (휴장 시 직전 영업일)
  - 정기변경일(KRX 대표지수) = 6·12월 최종거래일의 다음 매매거래일
  - 심사기준일 = 정기변경일이 속한 월의 전전월 최종 매매거래일
"""
from datetime import date, timedelta

# KRX 휴장일 (2026~2027, 확인된 공휴일/대체공휴일 기준. 임시공휴일 발생 시 갱신 필요)
HOLIDAYS = {
    # 2026
    "2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18", "2026-03-02", "2026-05-01", "2026-05-05", "2026-05-25",
    "2026-06-03", "2026-08-17", "2026-09-24", "2026-09-25", "2026-09-28", "2026-10-05", "2026-10-09", "2026-12-25", "2026-12-31",
    # 2027
    "2027-01-01", "2027-02-08", "2027-02-09", "2027-03-01", "2027-05-05", "2027-05-13", "2027-06-07", "2027-08-16",
    "2027-09-14", "2027-09-15", "2027-09-16", "2027-10-04", "2027-10-11", "2027-12-31",
}


def is_bday(d):
    return d.weekday() < 5 and d.isoformat() not in HOLIDAYS


def next_business_day(d, n=1):
    while n > 0:
        d += timedelta(days=1)
        if is_bday(d):
            n -= 1
    return d


def prev_business_day(d, n=1):
    while n > 0:
        d -= timedelta(days=1)
        if is_bday(d):
            n -= 1
    return d


def last_business_day_of_month(y, m):
    d = (date(y + (m // 12), (m % 12) + 1, 1) - timedelta(days=1))
    while not is_bday(d):
        d -= timedelta(days=1)
    return d


def first_business_day_of_month(y, m):
    d = date(y, m, 1)
    while not is_bday(d):
        d += timedelta(days=1)
    return d


def futures_last_trading_day(y, m):
    """KOSPI200 선물·옵션 만기일: 두 번째 목요일 (휴장이면 직전 영업일)"""
    d = date(y, m, 1)
    thursdays = [d + timedelta(days=i) for i in range(31) if (d + timedelta(days=i)).month == m and (d + timedelta(days=i)).weekday() == 3]
    e = thursdays[1]
    while not is_bday(e):
        e -= timedelta(days=1)
    return e


def next_week_first_bday(d):
    """익주 첫 영업일"""
    nd = d + timedelta(days=(7 - d.weekday()))
    while not is_bday(nd):
        nd += timedelta(days=1)
    return nd


def krx_regular(y, m):
    """KRX 대표지수 정기변경 (m=6 or 12): 심사기준일·심사기간·정기변경일"""
    ref = last_business_day_of_month(y, m - 2)
    start_m = m - 7
    sy = y
    if start_m <= 0:
        start_m += 12
        sy -= 1
    return {"period_start": date(sy, start_m, 1).strftime("%Y%m%d"), "period_end": ref.strftime("%Y%m%d"),
            "ref_date": ref.strftime("%Y%m%d"), "expiry": futures_last_trading_day(y, m).isoformat(),
            "apply": next_business_day(futures_last_trading_day(y, m)).isoformat(),
            "announce": f"{y}-{m-1:02d} 중 (주가지수운영위원회 심의 후 KRX 공표)"}


REVIEW_WINDOWS = {
    "kospi200": krx_regular(2026, 12),
    "kosdaq150": krx_regular(2026, 12),
    "kospi200_next": krx_regular(2027, 6),
}


def _ev(d, index, event, etfs, source, kind="정기", note=""):
    return {"date": d.isoformat() if isinstance(d, date) else d, "index": index, "event": event, "etfs": etfs, "source": source, "kind": kind, "note": note}


def build_events(y0=2026, y1=2027):
    ev = []
    for y in (y0, y1):
        for m in (6, 12):
            r = krx_regular(y, m)
            ev += [
                _ev(r["ref_date"][:4] + "-" + r["ref_date"][4:6] + "-" + r["ref_date"][6:], "KOSPI 200 / KOSDAQ 150 / KOSPI 100·50 / 코스피200 섹터", "심사기준일 (6개월 심사기간 종료)",
                    "KODEX 200·TIGER 200·KODEX 코스닥150·KODEX 코스피100·TIGER 200 IT 등", "KRX KOSPI 200·KOSDAQ 150 지수 방법론 6.1/6.3"),
                _ev(f"{y}-{m-1:02d}-15", "KOSPI 200 / KOSDAQ 150", "정기변경 결과 공표 (5·11월 중, 일자는 KRX 공지)", "동일", "KRX 방법론 7.2", note="예시일자(월 중)"),
                _ev(r["apply"], "KOSPI 200 / KOSDAQ 150 / KOSPI 100·50 / 코스피200 섹터 / 코스닥150 섹터", "정기변경 적용일 (선물 최종거래일 다음 매매거래일) — 전일 종가 리밸런싱",
                    "KODEX 200·TIGER 200·RISE 200·KODEX 레버리지·KODEX 코스닥150·TIGER 200 IT·커버드콜 계열 등", "KRX 방법론 7.1"),
            ]
            if m == 6:
                ev.append(_ev(r["apply"], "코리아 밸류업 지수", "정기변경 적용 (연1회, 6월 선물만기 다음 매매거래일)", "RISE 코리아밸류업", "KRX 코리아 밸류업 지수 방법론"))
        # KRX 섹터지수 (KRX 반도체 등): 연1회 9월
        e9 = futures_last_trading_day(y, 9)
        ev.append(_ev(last_business_day_of_month(y, 7), "KRX 반도체 (KRX 섹터지수)", "심사기준일 (KRX 중대형 TMI 9월 정기변경 심사와 동일)", "KODEX 반도체·TIGER 반도체·KODEX 반도체레버리지", "KRX 섹터지수 방법론 2.1 (2025.07)"))
        ev.append(_ev(next_business_day(e9), "KRX 반도체 (KRX 섹터지수)", "정기변경 적용 (9월 결제월 최종거래일 다음 매매거래일), 20% CAP 재조정", "KODEX 반도체·TIGER 반도체·KODEX 반도체레버리지", "KRX 섹터지수 방법론 3, 5"))
        # FnGuide TOP10: 선정 2·8월 말 → 3·9월 만기 D+2
        for m in (3, 9):
            e = futures_last_trading_day(y, m)
            ev.append(_ev(last_business_day_of_month(y, m - 1), "FnGuide TOP10", "종목 선정 기준일 (전월 말)", "TIGER 코리아TOP10", "FnGuide SizeStyle Index Series Methodology (2026.02)"))
            ev.append(_ev(next_business_day(e, 2), "FnGuide TOP10", "정기변경 적용 (선물옵션 만기일 D+2), 비중확정 만기일 종가", "TIGER 코리아TOP10", "FnGuide 방법론"))
        # FnGuide 반도체 TOP10: 선정 3·9월 말 → 4·10월 만기 익주 첫 영업일, 비중확정 T-2
        for m in (4, 10):
            e = futures_last_trading_day(y, m)
            ev.append(_ev(last_business_day_of_month(y, m - 1), "FnGuide 반도체 TOP10", "종목 선정 기준일 (1개월 단순시총 평균 상위 10)", "TIGER 반도체TOP10·TIGER 반도체TOP10레버리지", "FnGuide Semiconductor TOP10 Methodology 2.4"))
            ev.append(_ev(next_week_first_bday(e), "FnGuide 반도체 TOP10", "정기변경 적용 (만기일 익주 첫 영업일), TOP2 각 25% 재설정", "TIGER 반도체TOP10·TIGER 반도체TOP10레버리지", "FnGuide 방법론 2.4"))
        # FnGuide AI반도체 TOP2+: 선정 3·6·9·12월 말 → 1·4·7·10월 옵션만기 D+2
        for m in (1, 4, 7, 10):
            e = futures_last_trading_day(y, m)
            pm = 12 if m == 1 else m - 1
            py = y - 1 if m == 1 else y
            ev.append(_ev(last_business_day_of_month(py, pm), "FnGuide AI반도체 TOP2+", "종목 선정 기준일", "KODEX AI반도체TOP2플러스·SOL AI반도체TOP2플러스", "FnGuide AI Semiconductor TOP2 Methodology v2.5 (2026.04)"))
            ev.append(_ev(next_business_day(e, 2), "FnGuide AI반도체 TOP2+", "정기변경 적용 (옵션만기 D+2)", "KODEX AI반도체TOP2플러스·SOL AI반도체TOP2플러스", "FnGuide 방법론"))
        # FnGuide 2차전지 산업: 선정 2·5·8·11월 말 → 3·6·9·12월 만기 익주 1~3영업일 점진
        for m in (3, 6, 9, 12):
            e = futures_last_trading_day(y, m)
            ev.append(_ev(next_week_first_bday(e), "FnGuide 2차전지 산업", "정기변경 적용 시작 (만기 익주 첫~세번째 영업일 점진, 1위 20%/그외 15% 실링)", "KODEX 2차전지산업", "FnGuide Secondary Battery Industry Methodology v2.1"))
        # ACE AI반도체TOP3+: 3·6·9·12월 만기 D+2
        for m in (3, 6, 9, 12):
            e = futures_last_trading_day(y, m)
            ev.append(_ev(next_business_day(e, 2), "FnGuide AI 반도체 TOP3+", "정기변경 적용 (만기일 D+2), TOP3 각 25%", "ACE AI반도체TOP3+", "FnGuide AI Semiconductor TOP3 Plus Methodology v1.5"))
        # FnGuide 조선 TOP3+: 선정 1·4·7·10월 말 → 2·5·8·11월 만기 D+2
        for m in (2, 5, 8, 11):
            e = futures_last_trading_day(y, m)
            ev.append(_ev(next_business_day(e, 2), "FnGuide 조선 TOP3 플러스", "정기변경 적용 (만기일 D+2), TOP3 25%/그외 20%", "SOL 조선TOP3플러스", "FnGuide Shipbuilding TOP3 Plus Methodology v1.4"))
        # FnGuide K-반도체: 선정 5·11월 말 → 6·12월 만기 D+4·D+5
        for m in (6, 12):
            e = futures_last_trading_day(y, m)
            ev.append(_ev(next_business_day(e, 4), "FnGuide K-반도체", "정기변경 적용 (만기일 D+4·D+5 분할), 전자/닉스 각 25%", "HANARO Fn K-반도체", "FnGuide K-Semiconductor Methodology v1.0"))
            ev.append(_ev(next_business_day(e, 2), "FnGuide K-방위산업 / AI 반도체 소부장", "정기변경 적용 (만기일 D+2)", "PLUS K방산·SOL AI반도체소부장", "FnGuide K-Defense v1.2 / AI Semi Material&Equipment v1.3"))
            ev.append(_ev(next_business_day(e, 4), "FnGuide 배당주", "정기변경 적용 (만기일 T+4)", "PLUS 고배당주", "PLUS 고배당주 투자설명서"))
            ev.append(_ev(next_week_first_bday(e), "MKF 현대차그룹+ FW / FnGuide TOP 5 Plus(6월) / iSelect AI전력핵심설비", "정기변경 적용 (만기일 익주 첫 영업일)", "TIGER 현대차그룹플러스·KODEX Top5PlusTR·KODEX AI전력핵심설비", "각 지수 방법론·투자설명서"))
            ev.append(_ev(next_business_day(first_business_day_of_month(y, m), 1), "MKF 삼성그룹지수", "정기변경 적용 시작 (2~6번째 영업일 5일 점진)", "KODEX 삼성그룹", "Maekyung FnGuide Theme Indices Methodology"))
            ev.append(_ev(next_business_day(e, 3), "KEDI 코리아AI전력기기TOP3+", "정기변경 (만기일 기준 선정, D+3 수행)", "TIGER 코리아AI전력기기TOP3플러스", "TIGER 투자설명서"))
        # iSelect 글로벌HBM: 2·5·8·11월 첫 영업일
        for m in (2, 5, 8, 11):
            ev.append(_ev(first_business_day_of_month(y, m), "iSelect 글로벌HBM반도체", "정기변경/점검 적용 (첫 영업일)", "PLUS 글로벌HBM반도체", "PLUS 월간보고서"))
        # WISE 2차전지테마: 1·4·7·10월 옵션만기 익일
        for m in (1, 4, 7, 10):
            e = futures_last_trading_day(y, m)
            ev.append(_ev(next_business_day(e), "WISE 2차전지 테마", "정기변경 적용 (옵션만기 익일), 상위4 15% 실링", "TIGER 2차전지테마", "WISEfn 방법론"))
    # MSCI (공식 발표 ir_dates, 2026-08-12 공지)
    for ann, eff, q in (("2026-11-11", "2026-12-01", "Nov 2026 Index Review (SAIR)"), ("2027-02-09", "2027-03-01", "Feb 2027 Index Review (QIR)"),
                        ("2027-05-10", "2027-05-28", "May 2027 Index Review (SAIR)"), ("2027-08-12", "2027-09-01", "Aug 2027 Index Review (QIR)"),
                        ("2027-11-11", "2027-12-01", "Nov 2027 Index Review (SAIR)")):
        ev.append(_ev(ann, "MSCI Korea", f"{q} 발표", "TIGER MSCI Korea TR·KODEX MSCI Korea TR", "MSCI ir_dates.csv (2026-08-12)"))
        ev.append(_ev(prev_business_day(date.fromisoformat(eff)), "MSCI Korea", f"{q} 효력 (해당일 종가 리밸런싱, 익일 effective)", "TIGER MSCI Korea TR·KODEX MSCI Korea TR", "MSCI ir_dates.csv"))
    # 월간 선물옵션 만기 (신규상장 특례편입 교체일 후보)
    for y in (y0, y1):
        for m in range(1, 13):
            e = futures_last_trading_day(y, m)
            ev.append(_ev(next_business_day(e), "KOSPI 200 / KOSDAQ 150 (신규상장 특례)", "선물 최근월물 최종거래일 다음 매매거래일 — 특례편입 교체 후보일", "-", "KRX 방법론 8.2", kind="수시"))
    ev.sort(key=lambda x: x["date"])
    return ev


def events_upcoming(today, days=300):
    lo = today.isoformat()
    hi = (today + timedelta(days=days)).isoformat()
    out = []
    for e in build_events():
        if lo <= e["date"] <= hi:
            e["dday"] = (date.fromisoformat(e["date"]) - today).days
            out.append(e)
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(REVIEW_WINDOWS, ensure_ascii=False, indent=1))
    for e in events_upcoming(date.today(), 120):
        print(e["date"], f"D+{e['dday']:>3}", e["index"], "|", e["event"])
