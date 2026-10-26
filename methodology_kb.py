# -*- coding: utf-8 -*-
"""지수 방법론 지식베이스 (공식 문서 원문 기준)

KRX  : 기존 게시본 + 사용자가 공유한 대표지수 개정 방법론 및 대표지수 外 24개 PDF 검토
       개정 시행일은 본문 근거 미확인(HOLD). 파일명과 외부 안내일을 시행일로 확정하지 않음.
FnGuide: fnindex.co.kr 각 지수 페이지의 Methodology Book (data/fnguide_rules_v1.json 에 항목별 추출)
MSCI : MSCI GIMI Methodology (May 2026), ir_dates.csv (2026-08-12)
"""
import json
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).parent

KRX_DOC_URL = "https://index.krx.co.kr/contents/MKD/01/0111/01110600/MKD01110600.jsp"
KRICS_TRANSITION = {
    "forecast_basis": "GICS", "comparison_basis": "KRICS", "pending": True,
    "effective_date": None, "effective_date_status": "HOLD",
    "effective_date_reason": "공유 방법론 본문에 시행일 명시가 없어 공식 적용 시점을 확정하지 않음",
    "external_notice_expected_date": "2026-11-02",
    "classification_expected_date": "2026-10-26", "sector_count": 9,
    "official_result": False, "automatic_regime_switch": False,
    "sources": [
        {"label": "DB자산운용 KOSPI 200 개정 안내", "url": "https://db-asset.com/mob/kr/customer/notice/9920"},
        {"label": "삼성증권 KOSDAQ 150 개정 안내", "url": "https://www.samsungsecurities.co.kr/kor/invest/announce_view.do?create_date=20261002&create_time=130443&seq_no=100828"},
    ],
}

# 검토 범위와 계산 가능 여부를 구분한다. 문서 검토가 완료되어도 시행일은 확정되지 않는다.
METHODOLOGY_REVIEW = {
    "schema_version": 1,
    "status": "reviewed",
    "effective_date": None,
    "effective_date_status": "HOLD",
    "effective_date_reason": "개정 방법론 및 일부개정안 본문에서 시행일을 확인하지 못함. 파일명 날짜는 근거에서 제외",
    "official_result": False,
    "automatic_regime_switch": False,
    "classification_comparison": {
        "data_basis": "same_frozen_review_rows",
        "result_count": 3,
        "baseline": {"id": "GICS", "classification": "GICS", "ruleset": "legacy_reference", "label": "기존 GICS 기준 결과"},
        "A": {"id": "KRICS-A", "classification": "KRICS", "ruleset": "amended_document_scenario", "interpretation": "primary_and_new_buffer", "label": "KRICS-A: 1차선정 통과 AND 신규 순위 버퍼"},
        "B": {"id": "KRICS-B", "classification": "KRICS", "ruleset": "amended_document_scenario", "interpretation": "liquid_and_new_buffer", "label": "KRICS-B: 유동성 충족 AND 신규 순위 버퍼"},
        "calculation_status": "입력 검증에 통과한 지수만 ready, 불충분한 지수는 HOLD",
        "source_provenance": "사용자가 정리해 공유한 종목별 매핑. KRX의 공식 정기변경 결과가 아님",
    },
    "sources": [
        {"id": "KOSPI200_AMENDED", "title": "KOSPI 200 지수 방법론_20261102시행.pdf", "body_sections": ["6.2", "6.5", "8.2", "8.3"], "effective_date": None, "effective_date_status": "HOLD", "access_basis": "KRX 개정 방법론 본문 확인; 원본 문서는 재배포하지 않음"},
        {"id": "KOSDAQ150_AMENDED", "title": "KOSDAQ 150 지수 방법론_20261102시행.pdf", "body_sections": ["6.2", "6.5", "8.2", "8.3"], "effective_date": None, "effective_date_status": "HOLD", "access_basis": "KRX 개정 방법론 본문 확인; 원본 문서는 재배포하지 않음"},
        {"id": "P00", "title": "KRX시장지수방법론일부개정안.pdf", "pages": 33, "body_sections": ["예비종목 제도 폐지 대상", "신구방법론 대비표"], "effective_date": None, "effective_date_status": "HOLD"},
        {"id": "P01", "title": "KOSPI 100·50지수 방법론_261120.pdf", "pages": 3, "body_sections": ["2.4", "4.1", "4.3", "5"], "effective_date": None, "effective_date_status": "HOLD"},
        {"id": "P12", "title": "KRX 규모별 TMI 방법론_261030.pdf", "pages": 3, "body_sections": ["2.3", "2.4.1", "2.4.2"], "effective_date": None, "effective_date_status": "HOLD"},
        {"id": "P14", "title": "KRX 섹터지수 방법론_261120.pdf", "pages": 4, "body_sections": ["2.2", "2.4", "5"], "effective_date": None, "effective_date_status": "HOLD"},
        {"id": "P16", "title": "KRX 주가지수 기본 방법론_261120.pdf", "pages": 14, "body_sections": ["6", "7", "8.2", "10.2"], "effective_date": None, "effective_date_status": "HOLD"},
        {"id": "P17", "title": "섹터지수 방법론_261120.pdf", "pages": 11, "body_sections": ["6.2", "6.4", "8", "9.4", "별표"], "effective_date": None, "effective_date_status": "HOLD"},
    ],
    "mapping_sources": {
        "mapping_file": "data/krics_mapping.json",
        "official_result": False,
        "kospi200": {"title": "사용자 제공 KRICS KOSPI 분류표", "source_id": "user_krics_kospi200_20261023", "expected_records": 826, "access_basis": "사용자 제공 분류표 전체 행 검증", "original_workbook_published": False},
        "kosdaq150": {"title": "사용자 제공 KRICS KOSDAQ 분류표", "source_id": "user_krics_kosdaq150_20261023", "expected_records": 1733, "access_basis": "사용자 제공 분류표 전체 1,733개 행을 추출·대조한 비교용 자료", "original_workbook_published": False},
    },
    "document_scope": {
        "local_pdf_count": 24,
        "documents_related_to_current_scope": ["P00", "P01", "P12", "P14", "P16", "P17"],
        "unsupported_count": 18,
        "unsupported_document_ids": ["P02", "P03", "P04", "P05", "P06", "P07", "P08", "P09", "P10", "P11", "P13", "P15", "P18", "P19", "P20", "P21", "P22", "P23"],
        "unsupported_index_implementations_added": False,
        "reserve_policy_by_document": {
            "P01": "abolished", "P03": "abolished", "P04": "abolished", "P05": "abolished", "P17": "abolished_for_parent_sector_indices",
            "P06": "retained", "P11": "retained", "P13": "retained", "P15": "retained", "P18": "retained", "P19": "retained", "P21": "retained",
        },
        "scope_note": "문서 검토는 신규 지수 구현을 뜻하지 않음. 코스닥 TOP10/15·KRX TOP10 계열·금융고배당 TOP10의 예비종목은 해당 방법론에 존치",
    },
    "issues": [
        {"id": "kosdaq_top15_size_basis", "index": "코스닥 TOP 15", "status": "HOLD", "implementation_status": "unsupported", "standalone": "P19 p2 §2.3은 일평균시가총액", "amendment": "P00 p23-24는 일평균유동시가총액", "reason": "본문과 개정안의 풀시총/유동시총 기준 충돌. 공식 해석 전 하나를 임의 적용하지 않음"},
        {"id": "amendment_effective_date", "status": "HOLD", "effective_date": None, "reason": "파일명 261120·261030·20261102 및 외부 안내 예정일을 본문 시행일로 확정하지 않음"},
    ],
}

INDEX_KB = {
    "kospi200": {
        "classification_transition": KRICS_TRANSITION,
        "name": "KOSPI 200", "provider": "KRX", "doc": "사용자 공유 KOSPI 200 개정 방법론 §6.2·6.5·8.2·8.3 (본문 시행일 미명시)", "doc_url": KRX_DOC_URL,
        "universe": "심사기준일 현재 코스피지수 구성종목(보통주) 중 관리종목·정리매매종목, 부동산투자회사·선박투자회사·사회기반시설투융자회사·기업인수목적회사, 유동주식비율 10% 미만, 신규상장 6개월 미만(이전상장 합산·특례편입·분할신설 예외) 제외. 기타 부적합 종목(기본방법론 8.2: 실질심사 장기 거래정지 등) 제외 가능",
        "sector": "기존 GICS 기준: 10개 산업군 / KRICS-A·B: 개정문서 9개 산업군(에너지화학·소재·산업재·모빌리티·정보기술·금융및부동산·소비재·헬스케어·미디어및콘텐츠). KRICS-A는1차선정 AND 신규버퍼, KRICS-B는유동성 AND 신규버퍼 해석. 같은 동결 심사자료로 비교하며 공식 확정 결과가 아님",
        "criteria": "심사대상기간(심사기준일이 속한 달 포함 최근 6개월) 일평균시가총액·일평균거래대금",
        "selection": ["1차: 산업군별 일평균시총 큰 순으로 누적시총이 산업군 전체의 85%에 처음 도달하는 종목까지. 단 일평균거래대금 순위가 산업군 심사대상종목수의 85% 이내가 아니면 제외(유동성 기준)",
                      "2차: 기존종목은 유동성 충족 & 시총순위 ≤ 산업군 기존 구성종목수×110% 이면 유지. 신규는 시총순위 ≤ 기존 구성종목수×90% 이어야 편입(기존 구성종목 3개 미만 산업군 예외)",
                      "3차: 200 미달 시 산업군 무관 미선정 종목 중 유동성 충족·시총 높은 순 추가, 초과 시 시총 낮은 순 제외",
                      "정기심사 대형주 특례(6.4.4): 심사대상 종목 중 최근 15매매일 평균시총 상위 50위 이내 종목은 기준 미충족이어도 편입 가능. 적용하면 이미 선정된 종목 중 일평균시총 최소 종목 제외",
                      "개정방법론 6.5: 예비종목을 별도로 선정하지 않음"],
        "regular": "연 2회. 정기변경일 = KOSPI200 선물 6·12월 결제월 최종거래일(둘째 목요일)의 다음 매매거래일. 심사기준일 = 정기변경월의 전전월 최종 매매거래일(4월말/10월말). 결과는 5월·11월 중 주가지수운영위원회 심의 후 공표",
        "adhoc": ["개정방법론: 상장폐지 결정은 결정일 이후 2매매일 경과 다음 매매일 제외(합병·주식교환 등은 정지개시일), 관리종목 지정도 2매매일 경과 다음 매매일 제외. 일반 수시제외 후 예비종목 자동충원 없음. 사유 다음날부터 15매매일간 정지가 지속되면 기간경과 다음 매매일 적용 가능",
                  "신규상장 특례(8.2): 신규상장일부터 15매매일 평균 총시총이 유가증권 전체 보통주 상위50위 이내 AND 거래소가 정하는 같은 기간 평균유동시총 ≥ 50위 종목 평균총시총×0.5. 산업군·유동성 등을 감안해 편입 가능. 편입일은 15매매일 경과 후 최초 KOSPI200 선물 최근월물 최종거래일 다음 매매일. IPO 편입으로 기존 최소종목을 자동제외하지 않음",
                  "합병(8.3): 비구성 상장법인에 피흡수 시 정지개시일에 합병법인으로 교체(합병법인 30매매일 이상 정지 후 재개 시 재개일 익일). 비상장 합병법인은 기존종목 상장폐지일 제외·합병법인 상장 익일 편입. 구성종목간 흡수합병은 피흡수법인 정지일 제외하고 합병신주 선반영. 기업분할은 존속·신설 적격성 개별심사"],
        "reserve_policy": {"amended": "none", "automatic_replacement": False, "source": "공유 개정방법론 6.5", "legacy": "산업군별 유동성 충족·시총순 10종목 이내 및 일반 수시 예비충원"},
        "ipo_removal_policy": {"amended": "no_automatic_smallest_removal", "legacy": "직전 정기변경 시 최소종목 제외", "regular_largecap_rule": "6.4.4 정기 대형주특례 적용시 최소종목 제외는 유지"},
        "ipo_conditions": {"average_sessions": 15, "market_common_rank_limit": 50, "official_average_float_to_cutoff_full_mcap_min": 0.5, "official_float_required": True, "admission_discretionary": True},
        "cap": "없음(2020.4 30% CAP 폐지). 30% 분산요건은 코스피200 비중상한(30/25/20%) 지수 별도 산출",
        "weighting": "유동시가총액 가중",
        "notes": "계산 범위: 신규상장 6개월은 달력상 월 단위로 판정. 인프라투자회사 유형이 확인된 종목을 제외하며 일반 사업회사를 이름의 '인프라'만으로 제외하지 않음. 합병·분할 후 심사기간 재산정은 공식 공시로 확인해 등록한 이벤트에 한해 적용. 최근 15매매일 평균시총 상위 50위 특례는 최근 15매매일 관측이 완료된 전체 심사대상 종목에서 미선정 후보를 감시하며 자동 확정 편입은 하지 않음. 관리·정리매매 및 공식 유동비율 요건은 미수집이며 수급용 FIF는 ETF 비중에서 역산한 추정치.",
    },
    "kosdaq150": {
        "classification_transition": KRICS_TRANSITION,
        "name": "KOSDAQ 150", "provider": "KRX", "doc": "사용자 공유 KOSDAQ 150 개정 방법론 §6.2·6.5·8.2 (본문 시행일 미명시)", "doc_url": KRX_DOC_URL,
        "universe": "심사기준일 현재 코스닥지수 구성종목 중 관리종목·투자주의환기종목·정리매매종목, 부동산·선박·인프라투자회사·SPAC, 유동주식비율 10% 미만, 신규상장 6개월 미만(특례편입·분할신설 예외) 제외. 산업군 시총이 전체의 1% 미만인 산업군 소속 종목은 심사대상 제외",
        "sector": "기존 GICS 기준: 11개 산업군 / KRICS-A·B: 개정문서9개 산업군(에너지화학·소재·산업재·모빌리티·정보기술·금융및부동산·소비재·헬스케어·미디어및콘텐츠). KRICS-A는1차선정 AND 신규버퍼, KRICS-B는유동성 AND 신규버퍼 해석. 산업군 시총1% 미만 제외는각분류에서판단",
        "criteria": "심사대상기간(심사기준일이 속한 달 포함 최근 6개월) 일평균시가총액·일평균거래대금",
        "selection": ["1차: 산업군별 누적시총 60% 이상 되는 종목까지. 거래대금 순위가 산업군 심사대상종목수의 80% 이내가 아니면 제외",
                      "2차: 기존종목 유지 버퍼 120%, 신규 편입 80%(산업군 기존 구성종목 3개 미만 예외)",
                      "3차: 150 미달/초과 시 산업군 무관 시총 순 추가/제외",
                      "정기심사 대형주 특례(6.4.4): 심사대상 종목 중 최근 15매매일 평균시총 상위50위 이내 편입 가능. 적용하면 이미 선정된 종목 중 일평균시총 최소종목 제외",
                      "소형주 제외: 일평균시총 순위가 코스닥 보통주 300위 초과 종목은 제외 가능(유동성 충족 차순위로 대체)",
                      "개정방법론 6.5: 예비종목을 별도로 선정하지 않음"],
        "regular": "연 2회. KOSPI200 선물 6·12월 결제월 최종거래일의 다음 매매거래일. 심사기준일 4월말/10월말, 5·11월 중 공표",
        "adhoc": ["개정방법론: 상장폐지 결정·관리종목·투자주의환기종목 지정은 2매매일 경과 다음 매매일 제외. 일반 수시제외 후 예비종목 자동충원 없음. 유가증권 이전상장으로 상장폐지되면 상장폐지일 제외. 15매매일 정지 예외 적용",
                  "신규상장 특례(8.2): 신규상장일부터15매매일 평균총시총이 코스닥 전체 보통주 상위30위 이내 AND 거래소가 정하는 같은 기간 평균유동시총 ≥ 30위 종목 평균총시총×0.5. 산업군·유동성 등을 감안해 편입 가능. 편입일은15매매일 경과 후 최초 KOSPI200 선물 최근월물 최종거래일 다음 매매일. IPO 편입으로 기존 최소종목을 자동제외하지 않음",
                  "합병·기업분할은 KOSPI200 개정방법론 준용. 구성간 흡수합병은 피흡수법인 제외 및 합병신주 선반영, 비구성법인 흡수합병은 합병법인으로 교체"],
        "reserve_policy": {"amended": "none", "automatic_replacement": False, "source": "공유 개정방법론 6.5", "legacy": "산업군별 5종목 이내 및 일반 수시 예비충원"},
        "ipo_removal_policy": {"amended": "no_automatic_smallest_removal", "legacy": "기존 최소종목 제외", "regular_largecap_rule": "6.4.4 정기 대형주특례 적용시 최소종목 제외는 유지"},
        "ipo_conditions": {"average_sessions": 15, "market_common_rank_limit": 30, "official_average_float_to_cutoff_full_mcap_min": 0.5, "official_float_required": True, "admission_discretionary": True},
        "cap": "없음", "weighting": "유동시가총액 가중",
        "notes": "계산 범위: 300위 초과 소형주 제외는 거래소의 재량 조항이며 화면은 이를 적용한 추정 시나리오. 일반 선정 규칙만 적용한 결과와의 차이를 함께 표시하며 공식 확정 결과가 아님. 신규상장 6개월은 달력상 월 단위로 판정하고 확인된 인프라투자회사 유형을 제외. 합병·분할 후 심사기간은 공식 공시로 확인해 등록한 이벤트만 재산정. 최근 15매매일 평균시총 상위 50위 특례는 최근 15매매일 관측이 완료된 전체 심사대상 종목에서 미선정 후보를 별도 표시. 관리·투자주의환기·정리매매 및 공식 유동비율 요건은 미수집이며 FIF는 추정치.",
    },
    "kospi100": {
        "name": "KOSPI 100 / KOSPI 50", "provider": "KRX", "doc": "KOSPI 100·50지수 방법론_261120.pdf (P01 §2.4·4·5; 시행일 미명시)", "doc_url": KRX_DOC_URL,
        "universe": "6·12월 정기심사에서 선정된 KOSPI 200 구성종목", "sector": "산업군 구분 없음",
        "criteria": "KOSPI 200 정기심사에서 산정한 일평균시가총액",
        "selection": ["1단계: 일평균시총 상위 100(50)종목", "2단계: 기존 구성종목은 순위 ≤ 구성종목수×120%면 유지, 신규는 ≤ 80% 이내", "3단계: 초과 시 기존종목 우선, 미달 시 미선정 기존종목 중 시총 순 추가", "개정방법론 2.4: 예비종목을 별도로 선정하지 않음"],
        "regular": "연 2회, KOSPI 200과 동일 정기변경일",
        "adhoc": ["개정방법론: KOSPI200 수시제외 종목은 제외하며 예비1순위 자동충원 없음", "KOSPI200 특례편입 신규상장종목의 상장일부터15매매일 평균시총이 같은기간 KOSPI200 내100(50)위 종목보다 크면 편입. 신규편입에 따른 최소종목 자동제외 규칙은 삭제", "비구성 상장법인에 피흡수 시 정지개시일에 합병법인으로 교체(30매매일 이상 정지시 재개익일). 비상장 합병법인은 상장익일 편입. 구성간 흡수합병은 피흡수법인 정지일 제외·합병신주 선반영. 분할은 존속법인만 잔류"],
        "reserve_policy": {"amended": "none", "automatic_replacement": False, "source": "P01 2.4; P00 p26-28", "legacy": "KOSPI100 예비20·KOSPI50 예비10 및 일반 수시 예비충원"},
        "ipo_removal_policy": {"amended": "no_automatic_smallest_removal", "legacy": "직전 정기변경 최소종목 제외 후 예비1순위 지정"},
        "cap": "30% CAP(P01 p3 §5. 6·12월 정기 CAP Factor 조정, 수시 조정 가능)", "cap_level": 0.30, "weighting": "유동시가총액 가중",
    },
    "k200_sector": {
        "name": "KOSPI 200 섹터지수 (정보기술 등 11개) / KOSDAQ 150 섹터지수", "provider": "KRX", "doc": "섹터지수 방법론_261120.pdf (P17; 시행일 미명시)", "doc_url": KRX_DOC_URL,
        "universe": "6·12월 정기심사에서 선정된 KOSPI 200(KOSDAQ 150·KRX 300) 구성종목",
        "sector": "개정문서 KRICS 별표 분류. 기존 GICS 결과와 KRICS-A·B 민감도를 구분하며 세부섹터는 공식 세부매핑이 검증된 범위에서 비교. 지주회사는 주요 자회사 섹터. 1단계9산업군만으로 별표의2~4단계 세부섹터를 추정하지 않음",
        "criteria": "모지수 심사기준", "selection": ["개정문서: 모지수 구성종목을 KRICS 별표 세부분류별로 편입(산업별 대표성 고려해 달리 선정 가능)", "코스피200·코스닥150 섹터의 예비종목 제도 폐지(P00 p1). 구성종목 자동충원과 합병법인 교체를 구분"],
        "reserve_policy": {"amended": "none_for_kospi200_and_kosdaq150_sectors", "automatic_replacement": False, "source": "P00 p1; P17 8"},
        "regular": "연 2회, KOSPI 200 정기변경일과 동일",
        "adhoc": ["모지수 수시변경(부적합·신규상장·합병·분할)에 연동. 신규상장종목이 모지수에 편입되는 날 섹터지수 편입 가능"],
        "cap": "20% CAP (구성종목 5종목 미만 섹터는 미적용). CAP Factor는 정기변경일 조정, 연계상품 운용 곤란 시 수시 조정 가능", "weighting": "유동시가총액 가중",
    },
    "krx_sector": {
        "name": "KRX 반도체 (KRX 섹터지수 17종)", "provider": "KRX", "doc": "KRX 섹터지수 방법론_261120.pdf (P14; 시행일 미명시)", "doc_url": KRX_DOC_URL,
        "universe": "심사기준일(정기변경월 전전월 말=7월말) 기준 KRX 중대형 TMI 구성종목(9월 정기변경 예정종목) 중 부동산투자회사·사회기반시설투융자회사 제외",
        "sector": "개정 KRICS 별표: KRX 반도체 = 정보기술 > 반도체및디스플레이 > 반도체. 기존 GICS 반도체및장비와의 차이는 세부분류 확보 후 검증",
        "criteria": "심사대상기간 일평균거래대금·일평균시가총액 (KRX 규모별 TMI 방법론과 동일)",
        "selection": ["섹터 심사대상종목 중 일평균시총 순으로 누적시총95%까지 & 일평균거래대금 상위90% 이내(유동성)", "중대형TMI 유니버스의 누적커버리지 계산시 개별 평균시총 비중10% cap 가능(P12). 최종 지수비중20% cap과 구분", "20종목 미달 시 유동성 충족 차순위 종목 추가하여20종목", "은행·방송통신·보험·유틸리티는 심사대상 전부 편입"],
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
    kb = deepcopy(INDEX_KB)
    for key in ("kospi200", "kosdaq150", "kospi100", "k200_sector", "krx_sector"):
        kb[key]["amendment_review"] = {
            "ruleset": "amended_document_scenario",
            "effective_date": None,
            "effective_date_status": "HOLD",
            "official_result": False,
            "automatic_regime_switch": False,
        }
        note = "개정문서는 비교 시나리오의 규칙 근거이며 시행일은 HOLD. 기존 GICS 참고와 사용자 공유 매핑 기반 KRICS 비교를 구분하고 공식 확정 결과로 표시하지 않음."
        kb[key]["notes"] = (kb[key].get("notes", "") + " " + note).strip()
        if "reserve_policy" in kb[key]:
            kb[key]["legacy_reference"] = {
                "ruleset": "legacy_reference",
                "reserve_policy": kb[key]["reserve_policy"].get("legacy", "기존 게시본의 해당 섹터별 규정"),
                "ipo_removal_policy": kb[key].get("ipo_removal_policy", {}).get("legacy"),
                "official_result": False,
            }
    for key, needle in FN_RULE_KEY.items():
        hit = next((x for x in fn if needle in (x.get("doc_file") or "") or needle in (x.get("index_name") or "")), None)
        if hit:
            kb[key] = {"name": hit.get("index_name"), "provider": "FnGuide", "doc": f'{hit.get("doc_file")} ({hit.get("doc_version_date")})',
                       "doc_url": "https://www.fnindex.co.kr/overview/detail/C/FI00.WLT." + {"fn_top10": "T10", "fn_top5plus": "SBD", "fn_ksemi": "NHS", "fn_aitop2": "NMS", "fn_aitop3": "HBM", "fn_battery": "SBI", "fn_defense": "HBI", "fn_ship": "STP", "mkf_samsung": "SAM", "mkf_hyundai": "HFW", "fn_sobujang": "SSC", "fn_semitop10": "MSE"}[key],
                       "universe": hit.get("universe"), "sector": None, "criteria": hit.get("selection_date"),
                       "selection": [hit.get("selection")], "regular": f'{hit.get("selection_date")} → {hit.get("rebalance_date")}',
                       "adhoc": [hit.get("adhoc")], "cap": hit.get("weighting"), "weighting": hit.get("weighting"), "notes": hit.get("notes")}
    return {"indexes": kb, "etf_index_key": deepcopy(ETF_INDEX_KEY),
            "methodology_review": deepcopy(METHODOLOGY_REVIEW)}


if __name__ == "__main__":
    out = build()
    (ROOT / "data" / "methodology.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("indexes:", list(out["indexes"].keys()))
