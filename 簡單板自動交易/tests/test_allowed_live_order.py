from __future__ import annotations

import shutil
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import uuid

from sinopac_auto_trading.allowed_live_order import (
    TASK_NAME,
    TARGET_STOCK_ID,
    AllowedLiveOrderTaskStatus,
    calendar_allows_live_order,
    find_existing_allowed_live_trade,
    matching_allowed_live_trade,
    run_allowed_live_order_task,
)
from sinopac_auto_trading.cli import command_run_allowed_live_order
from sinopac_auto_trading.config import AutoTradingConfig, Settings


def _trade(
    *,
    stock_id: str = TARGET_STOCK_ID,
    action: str = "Buy",
    order_lot: str = "IntradayOdd",
    quantity: int = 1,
    order_datetime: str = "2026-04-22T09:10:00+08:00",
):
    return SimpleNamespace(
        contract=SimpleNamespace(code=stock_id),
        order=SimpleNamespace(id="OID1", action=action, order_lot=order_lot, quantity=quantity),
        status=SimpleNamespace(status="Submitted", order_datetime=order_datetime, modified_time=order_datetime),
    )


class AllowedLiveOrderTests(unittest.TestCase):
    def _case_dir(self, name: str) -> Path:
        path = Path(__file__).resolve().parent / "_tmp" / f"{name}-{uuid.uuid4().hex}"
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def test_calendar_allows_trade_day_when_present_in_csv(self) -> None:
        temp_dir = self._case_dir("allowed-calendar")
        calendar_path = temp_dir / "calendar.csv"
        calendar_path.write_text("trade_date\n2026-04-22\n2026-04-23\n", encoding="utf-8")
        allowed, reason = calendar_allows_live_order(__import__("datetime").date(2026, 4, 22), calendar_path=calendar_path)
        self.assertTrue(allowed)
        self.assertEqual(reason, "trade_day")

    def test_calendar_rejects_missing_calendar(self) -> None:
        temp_dir = self._case_dir("missing-calendar")
        calendar_path = temp_dir / "missing.csv"
        allowed, reason = calendar_allows_live_order(__import__("datetime").date(2026, 4, 22), calendar_path=calendar_path)
        self.assertFalse(allowed)
        self.assertEqual(reason, "calendar_missing")

    def test_matching_allowed_live_trade_accepts_target_order(self) -> None:
        self.assertTrue(
            matching_allowed_live_trade(_trade(), trade_date=__import__("datetime").date(2026, 4, 22))
        )

    def test_matching_allowed_live_trade_rejects_wrong_date(self) -> None:
        self.assertFalse(
            matching_allowed_live_trade(
                _trade(order_datetime="2026-04-21T09:10:00+08:00"),
                trade_date=__import__("datetime").date(2026, 4, 22),
            )
        )

    def test_find_existing_allowed_live_trade_returns_first_match(self) -> None:
        trades = [
            _trade(stock_id="2454"),
            _trade(stock_id="2330", action="Buy", order_lot="IntradayOdd", quantity=1),
        ]
        found = find_existing_allowed_live_trade(trades, trade_date=__import__("datetime").date(2026, 4, 22))
        self.assertIsNotNone(found)
        self.assertEqual(found.contract.code, "2330")

    def test_matching_allowed_live_trade_rejects_wrong_side_lot_or_quantity(self) -> None:
        trade_date = date(2026, 4, 22)
        self.assertFalse(matching_allowed_live_trade(_trade(action="Sell"), trade_date=trade_date))
        self.assertFalse(matching_allowed_live_trade(_trade(order_lot="Common"), trade_date=trade_date))
        self.assertFalse(matching_allowed_live_trade(_trade(quantity=2), trade_date=trade_date))

    def _settings(
        self,
        project_root: Path,
        *,
        allow_live_submit: bool,
        live_enabled: bool,
        weekly_execution_enabled: bool = True,
        weekly_budget: float = 100000,
    ) -> Settings:
        return Settings(
            api_key=None,
            secret_key=None,
            person_id=None,
            ca_path=None,
            ca_password=None,
            default_simulation=True,
            allow_live_submit=allow_live_submit,
            default_order_lot="IntradayOdd",
            budget_per_order=100000,
            price_buffer_pct=0.3,
            max_orders=5,
            auto_trading=AutoTradingConfig(
                live_enabled=live_enabled,
                weekly_execution_enabled=weekly_execution_enabled,
                weekly_budget=weekly_budget,
            ),
            project_root=project_root,
        )

    def test_allowed_task_auto_enables_live_config_when_schedule_is_authorized(self) -> None:
        temp_dir = self._case_dir("allowed-live-autofix")
        config_dir = temp_dir / "config"
        config_dir.mkdir()
        config_path = config_dir / "auto_trading.yaml"
        config_path.write_text("live_enabled: false\nweekly_budget: 123\n", encoding="utf-8")
        run_dir = temp_dir / "run"
        settings = self._settings(temp_dir, allow_live_submit=True, live_enabled=False)
        fake_api = SimpleNamespace(
            stock_account=object(),
            update_status=lambda _account: None,
            list_trades=lambda: [],
        )
        chase_result = SimpleNamespace(
            final_state="Submitted",
            final_order_id="OID-AUTO",
            summary_path=run_dir / "chase_2330.json",
        )

        with (
            patch.dict("os.environ", {"AUTO_TRADE_LIVE": "1"}, clear=False),
            patch("sinopac_auto_trading.allowed_live_order.calendar_allows_live_order", return_value=(True, "trade_day")),
            patch("sinopac_auto_trading.allowed_live_order.auto_trading_dir_for", return_value=run_dir),
            patch("sinopac_auto_trading.allowed_live_order.login", return_value=(fake_api, [])),
            patch("sinopac_auto_trading.allowed_live_order.run_single_stock_chase", return_value=chase_result),
        ):
            status = run_allowed_live_order_task(settings, trade_date=date(2026, 4, 27))

        self.assertEqual(status.status, "submitted")
        self.assertIn("live_enabled: true", config_path.read_text(encoding="utf-8"))
        self.assertTrue(settings.auto_trading.live_enabled)

    def test_allowed_task_skips_existing_order_on_retry_without_resubmitting(self) -> None:
        temp_dir = self._case_dir("allowed-live-retry-existing")
        (temp_dir / "config").mkdir()
        run_dir = temp_dir / "run"
        settings = self._settings(temp_dir, allow_live_submit=True, live_enabled=True)
        fake_api = SimpleNamespace(
            stock_account=object(),
            update_status=lambda _account: None,
            list_trades=lambda: [_trade(order_datetime="2026-04-27T09:15:00+08:00")],
        )

        with (
            patch.dict("os.environ", {"AUTO_TRADE_LIVE": "1"}, clear=False),
            patch("sinopac_auto_trading.allowed_live_order.calendar_allows_live_order", return_value=(True, "trade_day")),
            patch("sinopac_auto_trading.allowed_live_order.auto_trading_dir_for", return_value=run_dir),
            patch("sinopac_auto_trading.allowed_live_order.login", return_value=(fake_api, [])),
            patch("sinopac_auto_trading.allowed_live_order.run_single_stock_chase") as chase,
        ):
            status = run_allowed_live_order_task(settings, trade_date=date(2026, 4, 27))

        self.assertEqual(status.status, "skipped_existing_order")
        self.assertEqual(status.matched_order_id, "OID1")
        chase.assert_not_called()

    def test_allowed_task_does_not_auto_enable_live_config_without_allow_submit(self) -> None:
        temp_dir = self._case_dir("allowed-live-no-allow")
        config_dir = temp_dir / "config"
        config_dir.mkdir()
        config_path = config_dir / "auto_trading.yaml"
        config_path.write_text("live_enabled: false\n", encoding="utf-8")
        run_dir = temp_dir / "run"
        settings = self._settings(temp_dir, allow_live_submit=False, live_enabled=False)

        with (
            patch.dict("os.environ", {"AUTO_TRADE_LIVE": "1"}, clear=False),
            patch("sinopac_auto_trading.allowed_live_order.calendar_allows_live_order", return_value=(True, "trade_day")),
            patch("sinopac_auto_trading.allowed_live_order.auto_trading_dir_for", return_value=run_dir),
        ):
            status = run_allowed_live_order_task(settings, trade_date=date(2026, 4, 27))

        self.assertEqual(status.status, "skipped_live_submit_disabled")
        self.assertIn("live_enabled: false", config_path.read_text(encoding="utf-8"))

    def test_allowed_task_does_not_auto_enable_live_config_without_runner_live_env(self) -> None:
        temp_dir = self._case_dir("allowed-live-no-runner-env")
        config_dir = temp_dir / "config"
        config_dir.mkdir()
        config_path = config_dir / "auto_trading.yaml"
        config_path.write_text("live_enabled: false\n", encoding="utf-8")
        run_dir = temp_dir / "run"
        settings = self._settings(temp_dir, allow_live_submit=True, live_enabled=False)

        with (
            patch.dict("os.environ", {"AUTO_TRADE_LIVE": ""}, clear=False),
            patch("sinopac_auto_trading.allowed_live_order.calendar_allows_live_order", return_value=(True, "trade_day")),
            patch("sinopac_auto_trading.allowed_live_order.auto_trading_dir_for", return_value=run_dir),
        ):
            status = run_allowed_live_order_task(settings, trade_date=date(2026, 4, 27))

        self.assertEqual(status.status, "skipped_config_live_disabled")
        self.assertIn("live_enabled: false", config_path.read_text(encoding="utf-8"))

    def test_allowed_task_requires_user_weekly_execution_command(self) -> None:
        temp_dir = self._case_dir("allowed-live-weekly-disabled")
        (temp_dir / "config").mkdir()
        run_dir = temp_dir / "run"
        settings = self._settings(
            temp_dir,
            allow_live_submit=True,
            live_enabled=True,
            weekly_execution_enabled=False,
            weekly_budget=100000,
        )

        with (
            patch.dict("os.environ", {"AUTO_TRADE_LIVE": "1"}, clear=False),
            patch("sinopac_auto_trading.allowed_live_order.calendar_allows_live_order", return_value=(True, "trade_day")),
            patch("sinopac_auto_trading.allowed_live_order.auto_trading_dir_for", return_value=run_dir),
        ):
            status = run_allowed_live_order_task(settings, trade_date=date(2026, 4, 27))

        self.assertEqual(status.status, "skipped_weekly_execution_disabled")

    def test_allowed_task_requires_positive_weekly_budget(self) -> None:
        temp_dir = self._case_dir("allowed-live-weekly-budget-missing")
        (temp_dir / "config").mkdir()
        run_dir = temp_dir / "run"
        settings = self._settings(
            temp_dir,
            allow_live_submit=True,
            live_enabled=True,
            weekly_execution_enabled=True,
            weekly_budget=0,
        )

        with (
            patch.dict("os.environ", {"AUTO_TRADE_LIVE": "1"}, clear=False),
            patch("sinopac_auto_trading.allowed_live_order.calendar_allows_live_order", return_value=(True, "trade_day")),
            patch("sinopac_auto_trading.allowed_live_order.auto_trading_dir_for", return_value=run_dir),
        ):
            status = run_allowed_live_order_task(settings, trade_date=date(2026, 4, 27))

        self.assertEqual(status.status, "skipped_weekly_budget_missing")

    def _task_status(self, status: str) -> AllowedLiveOrderTaskStatus:
        return AllowedLiveOrderTaskStatus(
            trade_date="2026-04-27",
            task_name=TASK_NAME,
            status=status,
            message=f"status={status}",
        )

    def test_run_allowed_live_order_command_returns_zero_only_for_real_success(self) -> None:
        success_statuses = ["submitted", "skipped_existing_order"]
        for status in success_statuses:
            with (
                self.subTest(status=status),
                patch("sinopac_auto_trading.cli.Settings.from_env", return_value=object()),
                patch("sinopac_auto_trading.cli.run_allowed_live_order_task", return_value=self._task_status(status)),
            ):
                exit_code = command_run_allowed_live_order(SimpleNamespace(trade_date="2026-04-27"))

            self.assertEqual(exit_code, 0)

    def test_run_allowed_live_order_command_returns_nonzero_for_skipped_guard(self) -> None:
        with (
            patch("sinopac_auto_trading.cli.Settings.from_env", return_value=object()),
            patch(
                "sinopac_auto_trading.cli.run_allowed_live_order_task",
                return_value=self._task_status("skipped_config_live_disabled"),
            ),
        ):
            exit_code = command_run_allowed_live_order(SimpleNamespace(trade_date="2026-04-27"))

        self.assertEqual(exit_code, 2)

    def test_run_allowed_live_order_command_returns_nonzero_for_failed_task(self) -> None:
        with (
            patch("sinopac_auto_trading.cli.Settings.from_env", return_value=object()),
            patch("sinopac_auto_trading.cli.run_allowed_live_order_task", return_value=self._task_status("failed")),
        ):
            exit_code = command_run_allowed_live_order(SimpleNamespace(trade_date="2026-04-27"))

        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
