# ETF·BM 편출입 트래커 (etf-rebal)

국내 상장 ETF 중 순자산 상위 종목이 추종하는 기초지수(BM)의 **공식 방법론**을 기준으로,
정기변경·수시변경 편출입 예상 종목과 일정을 계산해 정적 웹페이지로 제공합니다.

- 사이트: https://inandout-kr.github.io/etf-rebal/
- 방법론 원문: `docs/methodology/` (KRX 지수산출방법 게시 PDF, FnGuide Methodology Book, MSCI GIMI)

## 구성

| 파일 | 역할 |
|---|---|
| `fetch_market.py` | 네이버(유니버스·시총), 다음(일별 종가·거래대금·상장주식수·상장일·WICS), WISE(섹터) 수집 → `data/market.sqlite` |
| `fetch_holdings.py` | wisereport CU_data 로 프록시 ETF(KODEX 200 등 32종) 구성종목 수집 → `data/holdings.json` |
| `analyze.py` | KOSPI 200 / KOSDAQ 150 / KOSPI 100 정기변경 시뮬레이션, FnGuide TOP-N, 캡 트리거, 신규상장 특례, MSCI 후보 → `data/analysis.json` |
| `calendar_events.py` | 선물만기·심사기준일·정기변경일 등 일정 계산 (KRX 휴장일 내장) |
| `methodology_kb.py` | 지수별 공식 방법론 요약 지식베이스 → `data/methodology.json` |
| `build_site.py` + `template.html` | 정적 페이지 빌드 → `dist/` |
| `update.ps1` / `deploy_github.ps1` | 일일 갱신·gh-pages 배포 |

## 실행

```powershell
.\update.ps1            # 수집 → 분석 → 빌드 → 배포
.\update.ps1 -NoDeploy  # 로컬 빌드만
```

## 주요 가정·한계

- 산업군은 WICS(WISE 산업분류)로 GICS 산업군을 근사하고, KOSPI 200 구성종목은 KOSPI 200 섹터 ETF 보유 정보로 보정합니다. KRX 실제 분류와 다를 수 있습니다.
- 관리종목·투자주의환기종목·유동주식비율 10% 미만 요건은 데이터가 없어 미반영입니다.
- 유동비율(FIF)은 KODEX 200·KODEX 코스닥150·TIGER MSCI Korea ETF 비중에서 역산한 추정치입니다.
- 심사대상기간이 진행 중이면(예: 12월 정기변경은 5월~10월) 현재까지의 누적 평균으로 계산한 "중간 집계"입니다.
- KRX 주가지수운영위원회의 정성 판단(부적합 종목 등)은 반영되지 않습니다.
