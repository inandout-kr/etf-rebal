"""Exercise the Windows PowerShell 5.1 scheduler entry point without network access."""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
SCRIPTS = ("run_daily.ps1", "update.ps1", "deploy_github.ps1")


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def run_powershell(script, cwd, env=None):
    script = "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false);\n" + script
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return subprocess.run([str(POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                           "-EncodedCommand", encoded], cwd=cwd, env=env, capture_output=True,
                          encoding="utf-8", errors="replace", timeout=60)


@unittest.skipUnless(os.name == "nt" and POWERSHELL.is_file(), "Windows PowerShell 5.1 is required")
class SchedulerTests(unittest.TestCase):
    def test_scheduler_shell_parses_utf8_and_preserves_korean_wics_argument(self):
        for name in SCRIPTS:
            self.assertTrue((ROOT / name).read_bytes().startswith(b"\xef\xbb\xbf"), name)
        script = """
        $result = @(); $wics = @()
        foreach ($name in @('run_daily.ps1', 'update.ps1', 'deploy_github.ps1')) {
          $tokens = $null; $parseErrors = $null
          $ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path (Get-Location) $name), [ref]$tokens, [ref]$parseErrors)
          $result += @($parseErrors | ForEach-Object { $_.Message })
          if ($name -eq 'update.ps1') {
            $wics = @($ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.StringConstantExpressionAst] -and $node.Value -eq '반도체와반도체장비' }, $true) | ForEach-Object { $_.Value })
          }
        }
        @{version=$PSVersionTable.PSVersion.ToString(); errors=@($result); wics=@($wics)} | ConvertTo-Json -Compress
        """
        completed = run_powershell(script, ROOT)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertTrue(result["version"].startswith("5.1."), result)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["wics"], ["반도체와반도체장비"])

    def run_isolated_scheduler(self, failure="", emit_stderr=False):
        with tempfile.TemporaryDirectory(prefix="etf-scheduler-test-") as directory:
            root = Path(directory)
            for name in SCRIPTS:
                shutil.copyfile(ROOT / name, root / name)
            # The real deployment script is parsed above; this stub prevents every network path.
            (root / "deploy_github.ps1").write_text("Write-Output 'deployment stub'; exit 0\n", encoding="utf-8-sig")
            fake = root / "fake_python.py"
            fake.write_text('''import json, os, sys
from pathlib import Path
with Path(os.environ["SCHEDULER_TEST_CALLS"]).open("a", encoding="utf-8") as output:
    output.write(json.dumps(sys.argv[1:], ensure_ascii=False) + "\\n")
print("자동 갱신 검증: " + sys.argv[1])
if os.environ.get("SCHEDULER_TEST_STDERR") == "1":
    print("진단 메시지", file=sys.stderr)
    print("traceback final line", file=sys.stderr)
sys.exit(7 if sys.argv[1] == os.environ.get("SCHEDULER_TEST_FAILURE") else 0)
''', encoding="utf-8")
            calls_path = root / "calls.jsonl"
            environment = dict(os.environ, SCHEDULER_TEST_CALLS=str(calls_path),
                               SCHEDULER_TEST_FAILURE=failure, SCHEDULER_TEST_STDERR=str(int(emit_stderr)))
            script = f"""
            function global:python {{
              & {quote(sys.executable)} {quote(fake)} @args
              $global:LASTEXITCODE = $LASTEXITCODE
            }}
            function global:git {{ throw 'Unexpected Git invocation in scheduler regression test' }}
            & {quote(root / 'run_daily.ps1')}
            exit $LASTEXITCODE
            """
            completed = run_powershell(script, root, environment)
            calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()] if calls_path.exists() else []
            log = (root / "update.log").read_text(encoding="utf-8-sig")
            return completed, calls, log

    def test_actual_entry_point_keeps_korean_argument_and_utf8_log(self):
        completed, calls, log = self.run_isolated_scheduler()
        self.assertEqual(completed.returncode, 0, completed.stderr + log)
        range_call = next(call for call in calls if call[0] == "fetch_daily_range.py")
        self.assertEqual(range_call[range_call.index("--wics") + 1], "반도체와반도체장비")
        self.assertIn("자동 갱신 검증", log)
        self.assertEqual(calls[-1], ["build_site.py"])
        self.assertIn("deployment stub", log)
        self.assertIn("[1/5] market data", log)

    def test_native_stderr_is_fully_logged_and_exit_code_decides_success(self):
        completed, calls, log = self.run_isolated_scheduler(emit_stderr=True)
        self.assertEqual(completed.returncode, 0, completed.stderr + log)
        self.assertEqual(calls[-1], ["build_site.py"])
        self.assertIn("진단 메시지", log)
        self.assertIn("traceback final line", log)
        failed, calls, log = self.run_isolated_scheduler("fetch_market.py", emit_stderr=True)
        self.assertNotEqual(failed.returncode, 0, log)
        self.assertEqual(calls, [["fetch_market.py"]])
        self.assertIn("traceback final line", log)
        self.assertIn("Python step failed (7)", log)

    def test_native_failure_stops_pipeline_and_reaches_scheduler(self):
        for step in ("fetch_market.py", "backtest_june.py", "validate_data.py"):
            with self.subTest(step=step):
                completed, calls, log = self.run_isolated_scheduler(step)
                self.assertNotEqual(completed.returncode, 0, log)
                self.assertEqual(calls[-1], [step])
                self.assertIn("Python step failed (7)", log)
                self.assertNotIn("deployment stub", log)


if __name__ == "__main__":
    unittest.main()
