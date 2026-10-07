# ETF/BM 편출입 트래커 일일 갱신: 시장데이터 -> ETF 구성종목 -> 분석 -> 정적 빌드 -> GitHub Pages 배포
# 사용: .\update.ps1            (전체)
#       .\update.ps1 -NoDeploy  (배포 생략)
param([switch]$NoDeploy)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$OutputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $OutputEncoding

function Invoke-PythonStep {
  param([string[]]$Arguments, [switch]$AllowFailure)
  $stepExitCode = $null
  $previousPreference = $ErrorActionPreference
  try {
    # Windows PowerShell 5.1 turns redirected native stderr into ErrorRecords.
    # Read the complete diagnostic stream, then decide success from the exit code.
    $ErrorActionPreference = "Continue"
    $global:LASTEXITCODE = $null
    & python @Arguments
    $stepExitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousPreference
  }
  if ($null -eq $stepExitCode -or $stepExitCode -ne 0) {
    $message = "Python step failed ($stepExitCode): $($Arguments -join ' ')"
    if ($AllowFailure) { Write-Warning "$message (using previous classification; build quality gate still applies)" }
    else { throw $message }
  }
}

try {

Write-Host "[1/5] market data (naver/daum/wise)..."
Invoke-PythonStep -Arguments @("fetch_market.py")

Write-Host "[1b] KRX official GICS classification..."
Invoke-PythonStep -Arguments @("fetch_gics.py") -AllowFailure

Write-Host "[2/5] ETF holdings (wisereport CU)..."
Invoke-PythonStep -Arguments @("fetch_holdings.py")

Write-Host "[3/5] analysis..."
Invoke-PythonStep -Arguments @("fetch_daily_range.py", "--since", "20250501", "--wics", "반도체와반도체장비")
Invoke-PythonStep -Arguments @("analyze.py")
Invoke-PythonStep -Arguments @("rebal_flow.py")
Invoke-PythonStep -Arguments @("flow_engine.py")
Invoke-PythonStep -Arguments @("backtest_june.py")
Invoke-PythonStep -Arguments @("archive.py")

Write-Host "[4/5] build site..."
Invoke-PythonStep -Arguments @("methodology_kb.py")
Invoke-PythonStep -Arguments @("validate_data.py")
Invoke-PythonStep -Arguments @("build_site.py")

if (-not $NoDeploy) {
  Write-Host "[5/5] deploy gh-pages..."
  & (Join-Path $PSScriptRoot "deploy_github.ps1")
  if ($LASTEXITCODE -ne 0) { throw "deploy failed ($LASTEXITCODE)" }
}
Write-Host "done: $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
exit 0
} catch {
  Write-Error $_ -ErrorAction Continue
  exit 1
}
