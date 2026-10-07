# Validate first, then append a commit to gh-pages in an isolated checkout.
# The checkout is retained on success/failure for inspection or retry.
$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$OutputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $OutputEncoding
$deployCheckout = Join-Path ([System.IO.Path]::GetTempPath()) ("etf-rebal-deploy-" + [guid]::NewGuid().ToString("N"))
$distPath = Join-Path $PSScriptRoot "dist"

function Invoke-CheckedGit {
  param([string[]]$Arguments)
  $gitExitCode = $null
  $previousPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = "Continue"
    $global:LASTEXITCODE = $null
    & git @Arguments
    $gitExitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousPreference
  }
  if ($null -eq $gitExitCode -or $gitExitCode -ne 0) { throw "Git step failed ($gitExitCode): $($Arguments -join ' ')" }
}

Push-Location $PSScriptRoot
try {
  try {
    $ErrorActionPreference = "Continue"
    $global:LASTEXITCODE = $null
    & python validate_data.py --dist-dir $distPath
    $validationExitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = "Stop"
  }
  if ($null -eq $validationExitCode -or $validationExitCode -ne 0) { throw "Deployment blocked by data quality validation" }
  Invoke-CheckedGit -Arguments @("clone", "--quiet", "--depth", "1", "--single-branch", "--branch", "gh-pages", "https://github.com/inandout-kr/etf-rebal.git", $deployCheckout)
  foreach ($file in @("index.html", "data.json", ".nojekyll")) {
    Copy-Item -LiteralPath (Join-Path $distPath $file) -Destination (Join-Path $deployCheckout $file) -Force
  }
  Invoke-CheckedGit -Arguments @("-C", $deployCheckout, "add", "--", "index.html", "data.json", ".nojekyll")
  try {
    $ErrorActionPreference = "Continue"
    $global:LASTEXITCODE = $null
    & git -C $deployCheckout diff --cached --quiet
    $diffExit = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = "Stop"
  }
  if ($diffExit -eq 1) {
    Invoke-CheckedGit -Arguments @("-C", $deployCheckout, "commit", "--quiet", "-m", "deploy $(Get-Date -Format 'yyyy-MM-dd HH:mm')")
    Invoke-CheckedGit -Arguments @("-C", $deployCheckout, "push", "--quiet", "origin", "HEAD:gh-pages")
  } elseif ($diffExit -ne 0) {
    throw "Could not inspect deployment changes ($diffExit)"
  }
  Write-Host "gh-pages deploy done: https://inandout-kr.github.io/etf-rebal/"
  Write-Host "Deployment checkout retained: $deployCheckout"
} catch {
  Write-Error "Deployment failed. Checkout retained for retry: $deployCheckout`n$_" -ErrorAction Continue
  exit 1
} finally {
  Pop-Location
}
exit 0
