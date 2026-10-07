# 작업 스케줄러용 래퍼: PATH 보강 후 update.ps1 실행, 로그 누적 (etf-finder/run_daily.ps1 과 동일 패턴)
$env:PATH = "C:\Python313;C:\Python313\Scripts;C:\Program Files\Git\cmd;" + $env:PATH
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$OutputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $OutputEncoding
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
"===== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') =====" | Out-File -Append -Encoding utf8 update.log
try {
  $ErrorActionPreference = "Continue"
  & (Join-Path $PSScriptRoot "update.ps1") *>&1 | Out-File -Append -Encoding utf8 -ErrorAction Stop update.log
  $updateExitCode = $LASTEXITCODE
  if ($updateExitCode -ne 0) { exit $updateExitCode }
} catch {
  $_ | Out-File -Append -Encoding utf8 update.log
  exit 1
} finally {
  $ErrorActionPreference = "Stop"
}
exit 0
