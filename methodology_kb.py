# -*- coding: utf-8 -*-
"""지수 방법론 지식베이스 (공식 문서 원문 기준)

KRX  : index.krx.co.kr 정보센터 > 지수산출방법 (docs/methodology/krx/*.pdf 로 저장, 2026-09-08 다운로드)
FnGuide: fnindex.co.kr 각 지수 페이지의 Methodology Book (data/fnguide_rules_v1.json 에 항목별 추출)
MSCI : MSCI GIMI Methodology (May 2026), ir_dates.csv (2026-08-12)
"""
import json
from pathlib import Path

ROOT = Path(__file__).parent

KRX_DOC_URL = "https://index.krx.co.kr/contents/MKD/01/0111/01110600/MKD01110600.jsp"

INDEX_KB = {
    "kospi200": {
        "name": "KOSPI 200", "provider": "KRX", "doc": "KOSPI 200 지수 방법론 (KRX 지수산출방법 게시본, seq 126)", "doc_url": KRX_DOC_URL,
        "universe": "심사기준일 현재 코스피지수 구성종목(보통주) 중 관리종목·정리매매종목, 부동산투자회사·선박투자회사·사회기반시설투융자회사·기업인수목적회사, 유동주식비율 10% 미만, 신규상장 6개월 미만(이전상장 합산·특례편입·분할신설 예외) 제외. 기타 부적합 종목(기본방법론 8.2: 실질심사 장기 거래정지 등) 제외 가능",
        "sector": "GICS 참고 10개 산업군: 에너지·소재·산업재·자유소비재·필수소비재·헬스케어·금융및부동산·정보기술·커뮤니케이션서비스·유틸리티",
        "criteria": "심사대상기간(심사기준일이 속한 달 포함 최근 6개월) 일평균시가총액·일평균거래대금",
        "selection": ["1차: 산업군별 일평균시총 큰 순으로 누적시총이 산업군 전체의 85%에 처음 도달하는 종목까지. 단 일평균거래대금 순위가 산업군 심사대상종목수의 85% 이내가 아니면 제외(유동성 기준)",
                      "2차: 기존종목은 유동성 충족 & 시총순위 ≤ 산업군 기존 구성종목수×110% 이면 유지. 신규는 시총순위 ≤ 기존 구성종목수×90% 이어야 편입(기존 구성종목 3개 미만 산업군 예외)",
                      "3차: 200 미달 시 산업군 무관 미선정 종목 중 유동성 충족·시총 높은 순 추가, 초과 시 시총 낮은 순 제외",
                      "대형주 특례: 최근 15매매일 평균시총 상위 50위 이내 종목은 기준 미충족이어도 편입 가능(시총 최소 종목 제외)",
                      "예비종목: 산업군별 유동성 충족 종목 중 시총 순 10종목 이내"],
        "regular": "연 2회. 정기변경일 = KOSPI200 선물 6·12월 결제월 최종거래일(둘째 목요일)의 다음 매매거래일. 심사기준일 = 정기변경월의 전전월 최종 매매거래일(4월말/10월말). 결과는 5월·11월 중 주가지수운영위원회 심의 후 공표",
        "adhoc": ["상장폐지 결정: 결정일 이후 2매매일 경과한 날의 다음 매매일 제외(합병·주식교환 등은 매매거래정지 개시일). 관리종목 지정: 지정일 이후 2매매일 경과 다음 매매일 제외. 예비종목 1순위 편입(정기변경 1개월 이내면 미편입 가능)",
                  "신규상장 특례: 상장 후 15매매일 평균시총이 유가증권 보통주 상위 50위 이내 & 유동시총 ≥ 50위 종목 시총×0.5 → 정기변경 전 편입 가능. 교체일 = 15매매일 경과 후 최초 도래 KOSPI200 선물 최근월물 최종거래일의 다음 매매일. 직전 정기변경 시 시총 최소 종목 제외",
                  "합병: 비구성종목에 피흡수 시 매매거래정지 개시일 교체(합병법인 30매매일 이상 정지 후 재개 시 재개일 익일). 기업분할: 존속법인 시총<구성종목 최소면 제외(재개일+2매매일 익일), 신설법인 시총>구성종목 80%위 종목이면 편입(상장 익일)"],
        "cap": "없음(2020.4 30% CAP 폐지). 30% 분산요건은 코스피200 비중상한(30/25/20%) 지수 별도 산출",
        "weighting": "유동시가총액 가중",
    },
    "kosdaq150": {
        "name": "KOSDAQ 150", "provider": "KRX", "doc": "KOSDAQ 150 지수 방법론 (2023.12, seq 106)", "doc_url": KRX_DOC_URL,
        "universe": "심사기준일 현재 코스닥지수 구성종목 중 관리종목·투자주의환기종목·정리매매종목, 부동산·선박·인프라투자회사·SPAC, 유동주식비율 10% 미만, 신규상장 6개월 미만(특례편입·분할신설 예외) 제외. 산업군 시총이 전체의 1% 미만인 산업군 소속 종목은 심사대상 제외",
        "sector": "GICS 참고 11개 산업군: 정보기술·헬스케어·커뮤니케이션서비스·소재·산업재·필수소비재·자유소비재·금융·에너지·유틸리티·부동산",
        "criteria": "심사대상기간(심사기준일이 속한 달 포함 최근 6개월) 일평균시가총액·일평균거래대금",
        "selection": ["1차: 산업군별 누적시총 60% 이상 되는 종목까지. 거래대금 순위가 산업군 심사대상종목수의 80% 이내가 아니면 제외",
                      "2차: 기존종목 유지 버퍼 120%, 신규 편입 80%(산업군 기존 구성종목 3개 미만 예외)",
                      "3차: 150 미달/초과 시 산업군 무관 시총 순 추가/제외",
                      "대형주 특례: 최근 15매매일 평균시총 상위 50위 이내 편입 가능",
                      "소형주 제외: 일평균시총 순위가 코스닥 보통주 300위 초과 종목은 제외 가능(유동성 충족 차순위로 대체)",
                      "예비종목: 산업군별 5종목 이내"],
        "regular": "연 2회. KOSPI200 선물 6·12월 결제월 최종거래일의 다음 매매거래일. 심사기준일 4월말/10월말, 5·11월 중 공표",
        "adhoc": ["상장폐지 결정·관리종목·투자주의환기종목 지정: 2매매일 경과 다음 매매일 제외, 예비종목 1순위 편입",
                  "신규상장 특례: 상장 후 15매매일 평균시총이 코스닥 보통주 상위 30위 이내 → 정기변경 전 편입 가능(교체일은 KOSPI200과 동일 규칙)",
                  "합병·기업분할: KOSPI 200 방법론과 동일"],
        "cap": "없음", "weighting": "유동시가총액 가중",
    },
    "kospi100": {
        "name": "KOSPI 100 / KOSPI 50", "provider": "KRX", "doc": "KOSPI 100·50 지수 방법론 (seq 107)", "doc_url": KRX_DOC_URL,
        "universe": "6·12월 정기심사에서 선정된 KOSPI 200 구성종목", "sector": "산업군 구분 없음",
        "criteria": "KOSPI 200 정기심사에서 산정한 일평균시가총액",
        "selection": ["1단계: 일평균시총 상위 100(50)종목", "2단계: 기존 구성종목은 순위 ≤ 구성종목수×120%면 유지, 신규는 ≤ 80% 이내", "3단계: 초과 시 기존종목 우선, 미달 시 미선정 기존종목 중 시총 순 추가", "예비종목 20(10)종목"],
        "regular": "연 2회, KOSPI 200과 동일 정기변경일",
        "adhoc": ["KOSPI 200 수시변경 제외 종목이 구성종목이면 제외 후 예비 1순위 편입", "KOSPI 200 특례편입 신규상장종목의 15매매일 평균시총이 100(50)위 종목보다 크면 편입, 시총 최소 종목 제외", "기업분할 시 존속법인만 잔류"],
        "cap": "30% CAP(정기변경일 CAP Factor 조정, 수시 조정 가능)", "weighting": "유동시가총액 가중",
    },
    "k200_sector": {
        "name": "KOSPI 200 섹터지수 (정보기술 등 11개) / KOSDAQ 150 섹터지수", "provider": "KRX", "doc": "섹터지수 방법론 (seq 119)", "doc_url": KRX_DOC_URL,
        "universe": "6·12월 정기심사에서 선정된 KOSPI 200(KOSDAQ 150·KRX 300) 구성종목",
        "sector": "GICS 기반 별표 분류: 헬스케어·건설·금융·산업재·에너지화학·경기소비재·정보기술·중공업·철강소재·생활소비재(유틸리티+필수소비재)·커뮤니케이션서비스. 지주회사는 주요 자회사 섹터",
        "criteria": "모지수 심사기준", "selection": ["모지수 구성종목을 GICS 분류별로 전부 편입(산업별 대표성 고려해 달리 선정 가능)"],
        "regular": "연 2회, KOSPI 200 정기변경일과 동일",
        "adhoc": ["모지수 수시변경(부적합·신규상장·합병·분할)에 연동. 신규상장종목이 모지수에 편입되는 날 섹터지수 편입 가능"],
        "cap": "20% CAP (구성종목 5종목 미만 섹터는 미적용). CAP Factor는 정기변경일 조정, 연계상품 운용 곤란 시 수시 조정 가능", "weighting": "유동시가총액 가중",
    },
    "krx_sector": {
        "name": "KRX 반도체 (KRX 섹터지수 17종)", "provider": "KRX", "doc": "KRX 섹터지수 방법론 (2025.07, seq 130)", "doc_url": KRX_DOC_URL,
        "universe": "심사기준일(정기변경월 전전월 말=7월말) 기준 KRX 중대형 TMI 구성종목(9월 정기변경 예정종목) 중 부동산투자회사·사회기반시설투융자회사 제외",
        "sector": "GICS 별표: KRX 반도체 = 정보기술 > 반도체 및 반도체장비",
        "criteria": "심사대상기간 일평균거래대금·일평균시가총액 (KRX 규모별 TMI 방법론과 동일)",
        "selection": ["섹터 심사대상종목 중 일평균시총 순으로 누적시총 95%까지 & 일평균거래대금 상위 90% 이내(유동성)", "20종목 미달 시 유동성 충족 차순위 종목 추가하여 20종목", "은행·방송통신·보험·유틸리티는 심사대상 전부 편입"],
        "regular": "연 1회. KOSPI200 선물 9월 결제월 최종거래일의 다음 매매거래일 (2026: 9/11)",
        "adhoc": ["KRX TMI의 부적합종목 제외·신규상장·합병·기업분할 수시변경 기준 동일 적용"],
        "cap": "20% CAP Level (구성종목 5종목 미만 섹터 미적용)", "weighting": "유동시가총액 가중",
    },
    "valueup": {
        "name": "코리아 밸류업 지수", "provider": "KRX", "doc": "코리아 밸류업 지수 방법론 (seq 12) + 2024.9.24 보도자료", "doc_url": KRX_DOC_URL,
        "universe": "심사기준일 현재 코스피·코스닥 구성종목 중 관리·투자주의환기·정리매매, 부동산·선박·인프라·SPAC, 유동비율 10% 미만, 최근 사업연도 자본잠식, 신규상장 12개월 미만 제외",
        "sector": "GICS 참고 10개 산업군(상대평가용)",
        "criteria": "5단계 스크리닝: ①시장대표성(유가+코스닥 시총 400위 이내) ②수익성(2년 연속 적자·2년 합산 적자 제외) ③주주환원(2년 연속 배당 또는 자사주 소각) ④시장평가(2년 평균 PBR 순위 전체 또는 산업군 내 50% 이내) ⑤자본효율성(2년 평균 ROE 산업군별 순위 상위) 순으로 100종목",
        "selection": ["밸류업 공시 이행기업 우대·미공시기업 패널티 (2026.6월 정기변경부터 공시이행기업 중심 구성)", "표창기업 특례편입(2년 유지)"],
        "regular": "연 1회. 6월 선물만기일 다음 매매거래일 (심사기준일 4월말)",
        "adhoc": ["KOSPI 200과 유사한 부적합종목 제외 규정", "정기변경 전이라도 개발목적 조기달성 위해 주가지수운영위원회 심의로 변경 가능(보칙)"],
        "cap": "15% CAP (정기변경일 CAP Factor 조정, 수시 조정 가능)", "weighting": "유동시가총액 가중",
    },
    "msci_korea": {
        "name": "MSCI Korea (Standard) Index", "provider": "MSCI", "doc": "MSCI Global Investable Market Indexes Methodology (May 2026) §2.3, §3; ir_dates.csv (2026-08-12)",
        "doc_url": "https://www.msci.com/eqb/methodology/meth_docs/MSCI_GIMIMethodology_May2026.pdf",
        "universe": "Korea Investable Equity Universe (풀시총 ≥ Equity Universe Minimum Size, 유동시총 ≥ 50%×EM 최소규모, 12개월 ATVR·거래빈도 등 유동성, 외국인 보유가능 여부(Foreign Room), 최소 3개월 상장(대형 IPO 예외))",
        "sector": "GICS 11 섹터(선정에는 미사용, 극단적 가격상승 판정의 비교군)",
        "criteria": "풀시총 기준 시장별 사이즈 세그먼트 컷오프(Standard = Large+Mid, DM 85% 커버리지 기준 Global Minimum Size Reference USD 15.75bn, EM은 50% = USD 7.87bn; 허용범위 0.5~1.15배 → EM Standard USD 3.94~9.06bn). 편입 후보는 시장 컷오프와 버퍼존(현행 구성종목 유지 버퍼) 적용",
        "selection": ["극단적 가격상승 종목(5D~60D 100~400%, 90D~250D 500~2500% 초과수익)은 Standard 편입 제외(2.3.6.3)",
                      "SAIR(5·11월): 사이즈 세그먼트 전면 재평가. QIR(2·8월): 유니버스 업데이트·버퍼존 적용 등 제한적 변경"],
        "regular": "연 4회 (2·5·8·11월). 2026-11 리뷰: 발표 11/11, 효력 12/1 (11/30 종가 리밸런싱). 가격 기준일(Price cutoff)은 발표 전 10영업일 중 임의일",
        "adhoc": ["IPO 조기편입(사이즈 컷오프 상회 대형 IPO는 상장 후 10영업일 내 편입 가능)", "합병·분할·상장폐지 등 이벤트 시 즉시 반영", "Foreign Room 15% 미만 시 편입 제한, 3.75% 미만 시 제외"],
        "cap": "없음(표준지수)", "weighting": "유동시총(FIF) 가중",
    },
}

# ETF → 지수 키 (분석·방법론 연결)
ETF_INDEX_KEY = {
    "코스피 200": "kospi200", "코스피 200 TR지수": "kospi200", "코스피 200 레버리지지수": "kospi200",
    "코스피 200 타겟 15% 위클리 커버드콜 지수": "kospi200", "코스피 200 커버드콜 5% OTM 지수(PR 지수)": "kospi200",
    "코스피 200 위클리 커버드콜 ATM 지수": "kospi200", "코스피 200 미국채혼합지수": "kospi200",
    "코스피 200 정보기술": "k200_sector", "코스피 200 정보기술 레버리지지수": "k200_sector",
    "코스피 100": "kospi100", "코스닥 150": "kosdaq150", "코스닥 150 레버리지지수": "kosdaq150",
    "KRX 반도체": "krx_sector", "KRX 반도체 레버리지 지수": "krx_sector", "코리아 밸류업 지수": "valueup",
    "MSCI Korea TR Index": "msci_korea", "코스피지수": None,
    "FnGuide 반도체TOP10 지수": "fn_semitop10", "FnGuide 반도체TOP10레버리지(2x) 지수(PR)": "fn_semitop10",
    "FnGuide AI반도체 TOP2 플러스 지수(PR)": "fn_aitop2", "FnGuide AI반도체 TOP2+ 지수": "fn_aitop2",
    "FnGuide K-반도체 지수 (시장가격)": "fn_ksemi", "FnGuide TOP10 지수": "fn_top10", "FnGuide TOP 5 Plus Total Return 지수": "fn_top5plus",
    "FnGuide 2차전지 산업 지수": "fn_battery", "FnGuide K-방위산업 지수": "fn_defense", "FnGuide 조선TOP3플러스지수(PR)": "fn_ship",
    "FnGuide AI 반도체 TOP3+ 지수": "fn_aitop3", "FnGuide AI 반도체 소부장 지수": "fn_sobujang", "MKF 삼성그룹지수": "mkf_samsung",
    "MKF 현대차그룹+ FW": "mkf_hyundai", "FnGuide 배당주 지수": "fn_dividend",
}
# FnGuide 룰북(JSON) 항목 ↔ 지수 키
FN_RULE_KEY = {
    "fn_top10": "TOP10", "fn_top5plus": "TOP 5 Plus", "fn_ksemi": "K-Semiconductor", "fn_aitop2": "AI Semiconductor TOP2",
    "fn_aitop3": "AI Semiconductor TOP3", "fn_battery": "Secondary Battery Industry", "fn_defense": "K-Defense",
    "fn_ship": "Shipbuilding", "mkf_samsung": "Samsung Group", "mkf_hyundai": "Maekyung", "fn_sobujang": "Material",
    "fn_semitop10": "Semiconductor_TOP10_Index_Methodology_Book_MR",
}


def build():
    fn = json.loads((ROOT / "data" / "fnguide_rules_v1.json").read_text(encoding="utf-8"))
    kb = dict(INDEX_KB)
    for key, needle in FN_RULE_KEY.items():
        hit = next((x for x in fn if needle in (x.get("doc_file") or "") or needle in (x.get("index_name") or "")), None)
        if hit:
            kb[key] = {"name": hit.get("index_name"), "provider": "FnGuide", "doc": f'{hit.get("doc_file")} ({hit.get("doc_version_date")})',
                       "doc_url": "https://www.fnindex.co.kr/overview/detail/C/FI00.WLT." + {"fn_top10": "T10", "fn_top5plus": "SBD", "fn_ksemi": "NHS", "fn_aitop2": "NMS", "fn_aitop3": "HBM", "fn_battery": "SBI", "fn_defense": "HBI", "fn_ship": "STP", "mkf_samsung": "SAM", "mkf_hyundai": "HFW", "fn_sobujang": "SSC", "fn_semitop10": "MSE"}[key],
                       "universe": hit.get("universe"), "sector": None, "criteria": hit.get("selection_date"),
                       "selection": [hit.get("selection")], "regular": f'{hit.get("selection_date")} → {hit.get("rebalance_date")}',
                       "adhoc": [hit.get("adhoc")], "cap": hit.get("weighting"), "weighting": hit.get("weighting"), "notes": hit.get("notes")}
    return {"indexes": kb, "etf_index_key": ETF_INDEX_KEY}


if __name__ == "__main__":
    out = build()
    (ROOT / "data" / "methodology.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("indexes:", list(out["indexes"].keys()))
