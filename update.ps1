# ETF/BM 편출입 트래커 일일 갱신: 시장데이터 -> ETF 구성종목 -> 분석 -> 정적 빌드 -> GitHub Pages 배포
# 사용: .\update.ps1            (전체)
#       .\update.ps1 -NoDeploy  (배포 생략)
param([switch]$NoDeploy)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONUTF8 = "1"

Write-Host "[1/5] market data (naver/daum/wise)..."
python fetch_market.py
if ($LASTEXITCODE -ne 0) { Write-Host "fetch_market failed"; exit 1 }

Write-Host "[1b] KRX official GICS classification..."
python fetch_gics.py
if ($LASTEXITCODE -ne 0) { Write-Host "fetch_gics failed (using previous classification)" }

Write-Host "[2/5] ETF holdings (wisereport CU)..."
python fetch_holdings.py
if ($LASTEXITCODE -ne 0) { Write-Host "fetch_holdings failed"; exit 1 }

Write-Host "[3/5] analysis..."
python analyze.py
if ($LASTEXITCODE -ne 0) { Write-Host "analyze failed"; exit 1 }
python fetch_daily_range.py --since 20250501 --wics 반도체와반도체장비
python rebal_flow.py
if ($LASTEXITCODE -ne 0) { Write-Host "rebal_flow failed"; exit 1 }
python flow_engine.py
if ($LASTEXITCODE -ne 0) { Write-Host "flow_engine failed"; exit 1 }
python backtest_june.py

Write-Host "[4/5] build site..."
python methodology_kb.py
python build_site.py
if ($LASTEXITCODE -ne 0) { Write-Host "build failed"; exit 1 }

if (-not $NoDeploy) {
  Write-Host "[5/5] deploy gh-pages..."
  & (Join-Path $PSScriptRoot "deploy_github.ps1")
  if ($LASTEXITCODE -ne 0) { Write-Host "deploy failed"; exit 1 }
}
Write-Host "done: $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
