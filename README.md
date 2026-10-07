# ETF·BM 편출입 트래커 (etf-rebal)

국내 상장 ETF 중 순자산 상위 종목이 추종하는 기초지수(BM)의 **공식 방법론**을 기준으로,
정기변경·수시변경 편출입 예상 종목과 일정을 계산해 정적 웹페이지로 제공합니다.

- 사이트: https://inandout-kr.github.io/etf-rebal/
- 방법론 원문: `docs/methodology/` (KRX 지수산출방법 게시 PDF, FnGuide Methodology Book, MSCI GIMI)

## 구성

| 파일 | 역할 |
|---|---|
| `fetch_market.py` | 네이버(유니버스·시총), 다음(일별 종가·거래대금·상장주식수·상장일·WICS), WISE(섹터) 수집 → `data/market.sqlite` |
| `fetch_gics.py` | KRX 인덱스 사이트 공식 GICS 산업분류(산업별 종목현황, 24개 산업그룹) 수집 → `stock.gics_ig/gics_sec` |
| `fetch_holdings.py` | wisereport CU_data 로 프록시 ETF(KODEX 200 등 32종) 구성종목 수집 → `data/holdings.json` |
| `analyze.py` | KOSPI 200 / KOSDAQ 150 / KOSPI 100 정기변경 시뮬레이션, FnGuide TOP-N, 캡 트리거, 신규상장 특례, MSCI 후보 → `data/analysis.json` |
| `rebal_flow.py` / `flow_engine.py` | KRX 반도체 9월 정기변경 및 남은 정기변경 전체의 종목별 패시브 매수/매도 추정 (공식 가중·캡 규칙) |
| `backtest_june.py` | 2026년 6월 정기변경(KRX 5/22 공표)을 같은 로직으로 재현해 정답과 비교 → `data/backtest_june.json` |
| `archive.py` | 매매일(적용일 전 영업일)이 되면 그날 아침 예측을 `data/history/`에 동결, 매매 후 프록시 ETF PDF로 실제 비중변화·편출입을 대조 → 히스토리 탭 |
| `calendar_events.py` | 선물만기·심사기준일·정기변경일 등 일정 계산 (KRX 휴장일 내장) |
| `methodology_kb.py` | 지수별 공식 방법론 요약 지식베이스 → `data/methodology.json` |
| `build_site.py` + `template.html` | 정적 페이지 빌드 → `dist/` |
| `update.ps1` / `deploy_github.ps1` | 일일 갱신·gh-pages 배포 |

## 실행

```powershell
.\update.ps1            # 수집 → 분석 → 빌드 → 배포
.\update.ps1 -NoDeploy  # 로컬 빌드만
```

Windows 예약 작업 `ETF-Rebal-Daily`는 매일 08:10에 `powershell.exe`로 `run_daily.ps1`을 실행합니다. 실행 내역과 오류는 `update.log`에 기록됩니다. Windows PowerShell 5.1에서 한글 인수를 올바르게 읽도록 `.ps1` 파일은 UTF-8 BOM 인코딩을 유지해야 합니다. 예약 작업 호환성은 `python -B -m unittest discover -s tests -p "test_scheduler.py"`로 실제 Windows PowerShell에서 검사합니다.

## 주요 가정·한계

- 산업군은 KRX 인덱스 사이트가 공개하는 공식 GICS 산업분류를 사용합니다(미분류 종목만 섹터 ETF·WICS로 보완). 과거 정기변경 재현 성적은 `data/backtest_june.json`에서 계산해 화면에 표시합니다. 현재 분류·구성종목에서 과거 상태를 역산하므로 당시 정보를 그대로 동결한 사전 예측 검증과는 차이가 있으며, 오차 원인을 정성 판단만으로 단정하지 않습니다.
- 관리종목·투자주의환기종목·유동주식비율 10% 미만 요건은 데이터가 없어 미반영입니다.
- 유동비율(FIF)은 KODEX 200·KODEX 코스닥150·TIGER MSCI Korea ETF 비중에서 역산한 추정치입니다.
- 심사대상기간이 진행 중이면(예: 12월 정기변경은 5월~10월) 현재까지의 누적 평균으로 계산한 "중간 집계"입니다.
- KRX 주가지수운영위원회의 정성 판단(부적합 종목 등)은 반영되지 않습니다.
- 매매가 끝난 정기변경은 활성 탭에서 빠지고 히스토리 탭으로 이동합니다. 스냅샷 파일(`data/history/*.json`)은 이후 갱신에서 덮어쓰지 않으며, 실제 결과는 ETF 실물 PDF 기준이라 지수 실제 비중과는 현금·선물·추적오차만큼 차이가 납니다.

## 화면 사용

- 표의 열 제목을 클릭하면 오름차순·내림차순으로 정렬됩니다. 선택한 방향은 열 제목에 표시되며, 키보드로 열 제목에 이동한 뒤 Enter 또는 Space로도 정렬할 수 있습니다.
- 금액·비율·날짜는 해당 값 기준으로 비교하고, 결측값은 정렬 방향과 관계없이 마지막에 표시합니다.
- 대시보드의 종목명에서 종목별 분석으로 이동할 수 있습니다. 종목코드의 외부 시세 링크는 별도로 유지합니다.
- ‘오늘 달라진 것’은 직전 데이터 기준일의 일별 스냅샷과 비교합니다. 처음 실행할 때는 기준 스냅샷만 저장하며 변화량을 임의로 만들지 않습니다.
- KRX 반도체 전용 상단 탭은 제공하지 않습니다. ETF·방법론, 공통 수급과 히스토리에서 기존 자료를 조회하며, 이전 `#semi` 주소는 전체 리밸 플로우로 연결됩니다.

## 데이터 기준과 검증

- 기간 평균과 최근 20거래일 평균 거래대금은 한국시간 기준 거래가 완료된 날의 데이터만 사용합니다. 거래정지 등으로 거래대금이 0인 정상 완료일을 일괄 제외하지 않습니다.
- 수집 실패 시 마지막 정상 데이터를 보존하고, 데이터의 기준일과 대체값 사용 여부를 화면에 표시합니다.
- 현재 화면·향후 심사는 마지막 검증된 현재 유니버스와 종목별 완료 가격일을 사용합니다. 가격 기준일 당시의 상장 유니버스를 재구성한 자료가 아닙니다. 이탈 종목의 과거 기록은 보존하며, 활성 종목의 기준일 가격 누락은 명시하고 보유종목의 수급 계산을 보류합니다.
- 출처·관측시각·값이 검증되지 않은 환율로 MSCI를 계산하지 않습니다. MSCI만 보류하며 국내 원화 계산은 계속할 수 있습니다. 검증된 이전 환율은 원래 관측시각을 유지합니다.
- 정기변경 날짜는 공통 일정 규칙에서 계산합니다. 공식 공표가 확인되지 않은 날짜와 휴장일 정보의 지원 범위 밖인 일정은 확정 일정으로 취급하지 않습니다.
- 빌드 전에 필수 데이터, 기준일, 구성 수, 비중 합계와 수급 계산의 유효성을 검사합니다.
- 일별 비교 스냅샷은 `data/daily_snapshots/`에, 기존 정기변경 예측 스냅샷은 `data/history/`에 각각 보관합니다.

회귀 검증:

```powershell
python -B -m unittest discover -s tests -p "test_*.py"
node tests/test_table_sort.js
node tests/test_data_quality_ui.js
```
