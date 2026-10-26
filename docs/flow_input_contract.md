# 수급 계산 입력과 보류 계약

수급 상세·전체·대시보드는 `data/flows.json`의 사전 계산 행을 공유한다. 추종자금 입력을 바꿀 때는 동일 행의 금액에 `입력명목 / event.notional_total`을 곱한다. ADV도 같은 배율을 적용하며 `row.adv20`은 완료된 최근20거래일 거래대금이다. 심사기간 거래대금으로 별도 계산하지 않는다.

`official_ffr`는 공식 유동주식비율, `cap_factor`는 공식 CAP Factor, `relative_coefficient`는 비상한 프록시의 상대가중 추정계수다. 셋은 서로 대체하지 않는다. CAP 적용 프록시의 현재비중이20%보다 낮아져도 그 비중/시총을 FFR로 역산하지 않는다.

## 공식 입력 파일

`data/flow_inputs.json`은 선택 입력이다. 없으면 공식 입력이 필요한 수급을 HOLD한다. 이 파일을 코드가 임의로 만들거나 공식 값을 추론하지 않는다. 사용자가 확인한 공식 자료와 출처를 제공할 때 아래 구조로 읽는다. `status: official`은 데이터 제공자가 검증한 자료라는 선언이며, URL 문자열만으로 출처를 자동 인증하는 기능은 아니다.

- 최상위: `schema_version: 1`, `indexes: {index_key: {...}}`, 선택적으로 `tmi: {...}`.
- `indexes.kospi200_it.membership`: `status`, `source_url`, `as_of(YYYYMMDD)`, `rule_id: P17_appendix1_kospi200_it`, `classifications`.
- `classifications[종목코드]`: `krics1`, `krics2`, `is_holding_company(boolean)`. 부모지수 예상 선정종목 전체에 대한 입력이 필요하다. 누락 행은 비편입으로 간주하지 않고 전체 섹터를 HOLD한다.
- 지주회사는 `major_subsidiary: {code, verified: true, source_url, krics1, krics2}`가 추가로 필요하다. 주요 자회사 판단이 확인되지 않으면 부모의 일반 산업군이나 현재 ETF 보유로 대체하지 않는다. 실행 규칙은 `flow_calculation.SECTOR_RULES`의 P17별표1·지주회사 주석에 연결된다.
- `indexes.krx_semi.membership`은 `rule_id: P14_appendix1_krx_semi`, `krics1/2/3`을 사용한다. KRICS3반도체만 포함하고 같은2단계의 디스플레이는 제외한다.
- `indexes[key].official_ffr`: `status: official`, `source_url`, `as_of`, `values: {종목코드:0초과1이하}`. 프록시 역산계수를 넣는 용도가 아니다.
- KRX상한지수에서5종목 이상이면 `official_cap_factors`도 필요하다. 필드: `status: official`, `source_url`, `as_of`, `valid_from/valid_to(YYYY-MM-DD)`, `reference_period: {start,end}`, `values`. 산정기간은 변경월 직전3개월의 달력 범위이며 관측일에 완료되어야 한다. Factor는0초과1이하·소수4자리, 유효구간은 해당 적용일을 포함해야 한다. 별도 공식 예외는 현재 자동 해석하지 않으며 일반 게이트를 우회하지 않는다.
- 공식 CAP Factor는 유효구간에서 고정한 채 시가총액에 곱한다. 주가 변화 후 비중을 다시20%로 잘라 맞추지 않는다. 5종목 미만 CAP예외도 공식 FFR 입력 자체를 면제하지 않는다.

## TMI와 반도체

`tmi`에는 `status: official`, `source_url`, `as_of`, `review_ref(YYYYMMDD)`, `eligibility_verified: true`, `event_coverage_verified: true`, `eligible_codes`, `previous_midlarge_codes`가 필요하다. 이전 구성목록이 없으면75/133버퍼를 생략하지 않고 HOLD한다. 등록된 합병·분할 이벤트는 심사기간 재설정에 전달한다. 자동 수집되지 않은 이벤트까지 모두 확인했다는 뜻은 아니다.

중대형TMI는94% 1차선정 후 기존75%이상 유지·신규133%미만 제외를 적용한다. `coverage_cap: 0.10`은 `coverage_cap_status: official_discretion`이 확인된 회차에만 선택한다. 이는 TMI94% 커버리지 층위다. 이후 KRX반도체 섹터95%에는10%CAP을 복사하지 않는다. 기존JSON의 `current/cap10/literal` 키는 호환상 유지하지만 `selected_tmi_scenario`와 각 시나리오의 근거를 읽어야 한다.

## 출력

외부 날짜는 실제 달력상 유효한 `YYYY-MM-DD` 또는 `YYYYMMDD` 문자열만 받는다. 숫자 날짜, 잘못된 월·일, 배열형 FFR/CF값과 역전된 CAP기간은 명시적인 HOLD 사유가 된다. TMI 코드목록의 중복·빈값·비문자 값도 보류한다. `flow_inputs.json` 최상위·indexes·지수별 입력·tmi의 객체형식이 잘못되면 기존 출력에 쓰기 전에 ValueError로 거부한다.

보류 이벤트는 `availability: hold`, 구체적인 `hold_reasons[{code,message,clause}]`를 가진다. `adds/dels/n_target/total_buy/total_sell`은 `null`; 행의 `w_target/w_target_raw/delta/flow_total/adv_mult`도 `null`이다. 관측 현재비중과 명목·출처는 유지한다. 반도체의 `n_selected/total_buy_eok/total_sell_eok`도 동일하게 `null`이다. 심사기간 미시작은 `review_availability: pending_review`로 별도 보존한다.

`by_stock`의 `known_total/total`은 계산 가능한 이벤트의 부분합이다. `held_count/held_events/complete`가 누락 범위를 명시하며, 전부 보류인 종목의 합계는0이 아니라`null`이다. 비상한 K200/KQ150 결과도 `official_result: false`, `weight_basis: uncapped_relative_coefficient_estimate`인 추정 모델로 표시한다.

레버리지 명목은 AUM×배수에서 합산 AUM에 이미 들어간 기초ETF의 실물보유분만 뺀다. 보유 이름이 합산 ETF명과 정확히 대응하지 않으면 임의로 중복 차감하지 않고 `unmatched_holdings_not_deducted`에 남긴다. `aum_eok`, `gross_notional_eok`, `deduction_eok`, `leverage_multiplier`, `notional_eok`를 별도로 제공한다.

## 검증

`tests/test_flow_input_contract.py`의 공식 입력은 가상의 테스트 전용 자료다. 실제 공식 수치가 아니다. 공식 매핑·지주회사 근거·FFR·유효CF를 모두 공급하면 HOLD가 해제되고, 하나라도 없으면 금액이 보류되는 경로를 검증한다. `test_engine.py`는 기존 극소 양수비중의 원값 보존 검증을 공식FFR 합성입력과 함께 유지한다.
