from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TaskRunnerScriptTests(unittest.TestCase):
    def test_allowed_live_order_runner_quotes_run_py_path_with_spaces(self) -> None:
        script = (PROJECT_ROOT / "scripts" / "run_allowed_2330_live_order_task.ps1").read_text(encoding="utf-8")

        self.assertIn("$quotedRunPy", script)
        self.assertIn('$processInfo.Arguments = "$quotedRunPy run_allowed_live_order"', script)
        self.assertNotIn("-ArgumentList @($runPy", script)

    def test_allowed_live_order_runner_has_separate_smoke_test_mode(self) -> None:
        script = (PROJECT_ROOT / "scripts" / "run_allowed_2330_live_order_task.ps1").read_text(encoding="utf-8")

        self.assertIn("[switch]$SmokeTest", script)
        self.assertIn("allowed_2330_live_order_smoke", script)
        self.assertIn("smoke_ok", script)
        self.assertIn("if (-not $SmokeTest)", script)

    def test_common_live_order_schedule_preflight_checks_runner_and_live_guard(self) -> None:
        script = (PROJECT_ROOT / "scripts" / "assert_live_order_schedule_preflight.ps1").read_text(encoding="utf-8")

        self.assertIn('[string]$UntilTime = "13:20"', script)
        self.assertIn("catch-up window remains open", script)
        self.assertIn("RunDate/AtTime is in the past and the catch-up window is closed", script)
        self.assertIn("-SmokeTest", script)
        self.assertIn("Runner smoke test failed. Scheduled task was not registered.", script)
        self.assertIn("project_root / 'src'", script)
        self.assertIn("project_root / 'config'", script)
        self.assertNotIn('project_root / "src"', script)
        self.assertIn("ensure_auto_trading_live_enabled", script)
        self.assertIn("evaluate_live_submit_guard(confirm_live=True)", script)
        self.assertIn('$env:AUTO_TRADE_LIVE = "1"', script)
        self.assertIn("Live guard preflight failed. Scheduled task was not registered.", script)

    def test_allowed_live_order_installer_invokes_common_preflight_before_registering(self) -> None:
        script = (PROJECT_ROOT / "scripts" / "install_allowed_2330_live_order_task.ps1").read_text(encoding="utf-8")

        self.assertIn("[Parameter(Mandatory = $true)]", script)
        self.assertNotIn('[string]$RunDate = "2026-04-22"', script)
        self.assertIn('[string]$UntilTime = "13:20"', script)
        self.assertIn("[int]$RetryIntervalMinutes = 5", script)
        self.assertIn('[string]$ProjectRoot = ""', script)
        self.assertIn("$resolvedProjectRoot", script)
        self.assertNotIn("-ProjectRoot $projectRoot", script)
        self.assertIn("-ProjectRoot $resolvedProjectRoot", script)
        self.assertIn("$effectiveTriggerAt", script)
        self.assertIn("-RepetitionInterval (New-TimeSpan -Minutes $RetryIntervalMinutes)", script)
        self.assertIn("-RepetitionDuration $repetitionDuration", script)
        self.assertIn("assert_live_order_schedule_preflight.ps1", script)
        self.assertIn("Live order schedule preflight failed. Scheduled task was not registered.", script)
        self.assertLess(script.index("assert_live_order_schedule_preflight.ps1"), script.index("Register-ScheduledTask"))

    def test_order_schedule_installers_cannot_skip_preflight(self) -> None:
        scripts = list((PROJECT_ROOT / "scripts").glob("install_*order*_task.ps1"))
        self.assertTrue(scripts)
        for path in scripts:
            script = path.read_text(encoding="utf-8")
            self.assertNotIn("SkipLiveGuardPreflight", script)
            self.assertNotIn("SkipRunnerSmokeTest", script)

    def test_every_scheduled_task_registration_goes_through_common_preflight(self) -> None:
        scripts = list((PROJECT_ROOT / "scripts").glob("*.ps1"))
        registrations = [path for path in scripts if "Register-ScheduledTask" in path.read_text(encoding="utf-8")]
        self.assertTrue(registrations)
        for path in registrations:
            script = path.read_text(encoding="utf-8")
            self.assertIn("assert_live_order_schedule_preflight.ps1", script, path.name)
            self.assertLess(
                script.index("assert_live_order_schedule_preflight.ps1"),
                script.index("Register-ScheduledTask"),
                path.name,
            )



if __name__ == "__main__":
    unittest.main()
