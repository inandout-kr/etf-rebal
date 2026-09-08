# 작업 스케줄러용 래퍼: PATH 보강 후 update.ps1 실행, 로그 누적 (etf-finder/run_daily.ps1 과 동일 패턴)
$env:PATH = "C:\Python313;C:\Python313\Scripts;C:\Program Files\Git\cmd;" + $env:PATH
$env:PYTHONUTF8 = "1"
Set-Location $PSScriptRoot
"===== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') =====" | Out-File -Append -Encoding utf8 update.log
& (Join-Path $PSScriptRoot "update.ps1") 2>&1 | Out-File -Append -Encoding utf8 update.log
