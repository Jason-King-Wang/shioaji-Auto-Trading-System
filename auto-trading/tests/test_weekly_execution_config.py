from __future__ import annotations

import shutil
import unittest
import uuid
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sinopac_auto_trading.cli import _buy_loop_sizing_budget_guard, command_approve_week
from sinopac_auto_trading.config import (
    AutoTradingConfig,
    Settings,
    set_auto_trading_weekly_execution,
    weekly_execution_week_id_for,
)


class WeeklyExecutionConfigTests(unittest.TestCase):
    def _case_dir(self, name: str) -> Path:
        path = Path(__file__).resolve().parent / "_tmp" / f"{name}-{uuid.uuid4().hex}"
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def test_set_auto_trading_weekly_execution_writes_guard_config(self) -> None:
        root = self._case_dir("weekly-execution")
        config_dir = root / "config"

        path = set_auto_trading_weekly_execution(
            weekly_budget=250000,
            weekly_execution_enabled=True,
            weekly_execution_week_id="2026-W18",
            config_dir=config_dir,
        )

        self.assertEqual(path, config_dir / "auto_trading.yaml")
        settings = Settings.load(root)
        self.assertEqual(settings.auto_trading.weekly_budget, 250000.0)
        self.assertTrue(settings.auto_trading.weekly_execution_enabled)
        self.assertEqual(settings.auto_trading.weekly_execution_week_id, "2026-W18")

    def test_command_approve_week_requires_budget_when_enabling(self) -> None:
        root = self._case_dir("weekly-command-requires-budget")
        settings = SimpleNamespace(project_root=root, auto_trading=AutoTradingConfig())

        with patch("sinopac_auto_trading.cli.Settings.load", return_value=settings):
            with self.assertRaisesRegex(RuntimeError, "requires --weekly-budget"):
                command_approve_week(
                    SimpleNamespace(
                        trade_date="2026-04-27",
                        weekly_budget=None,
                        week_id=None,
                        execute=True,
                    )
                )

    def test_command_approve_week_sets_iso_week_gate(self) -> None:
        root = self._case_dir("weekly-command")
        settings = SimpleNamespace(project_root=root, auto_trading=AutoTradingConfig())

        with patch("sinopac_auto_trading.cli.Settings.load", return_value=settings):
            exit_code = command_approve_week(
                SimpleNamespace(
                    trade_date="2026-04-27",
                    weekly_budget=300000,
                    week_id=None,
                    execute=True,
                )
            )

        self.assertEqual(exit_code, 0)
        loaded = Settings.load(root)
        self.assertTrue(loaded.auto_trading.weekly_execution_enabled)
        self.assertEqual(loaded.auto_trading.weekly_budget, 300000.0)
        self.assertEqual(
            loaded.auto_trading.weekly_execution_week_id,
            weekly_execution_week_id_for(date(2026, 4, 27)),
        )

    def test_buy_loop_blocks_live_when_sizing_budget_is_stale(self) -> None:
        settings = SimpleNamespace(
            auto_trading=AutoTradingConfig(weekly_budget=300000, overrun_tolerance=0),
        )

        allowed, reason = _buy_loop_sizing_budget_guard(
            settings,
            {
                "sizing_weekly_budget": 100000,
                "sizing_hard_budget": 100000,
                "sizing_weekly_execution_week_id": weekly_execution_week_id_for(date(2026, 4, 27)),
            },
            trade_date=date(2026, 4, 27),
        )

        self.assertFalse(allowed)
        self.assertEqual(reason, "sizing_budget_mismatch")


if __name__ == "__main__":
    unittest.main()
