from __future__ import annotations

import json
import shutil
import unittest
import uuid
from datetime import date
from pathlib import Path

from sinopac_auto_trading.config import AutoTradingConfig, FeeConfig, ProviderConfig, Settings
from sinopac_auto_trading.obsidian_sync import sync_obsidian_snapshot


class ObsidianSyncTests(unittest.TestCase):
    def test_sync_obsidian_writes_status_and_notes(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"obsidian-sync-{uuid.uuid4().hex}"
        project_root = base / "project"
        vault_root = base / "vault"
        (project_root / "notes" / "daily").mkdir(parents=True, exist_ok=True)
        (project_root / "notes" / "weeks").mkdir(parents=True, exist_ok=True)
        (project_root / "reports" / "auto_trading").mkdir(parents=True, exist_ok=True)
        (project_root / "data" / "auto_trading" / "2026-04-20").mkdir(parents=True, exist_ok=True)
        (project_root / "data" / "calendars").mkdir(parents=True, exist_ok=True)
        (project_root / "data" / "calendars" / "twse_trading_calendar.csv").write_text(
            "trade_date\n2026-04-20\n2026-04-21\n2026-04-22\n2026-04-23\n2026-04-24\n",
            encoding="utf-8",
        )
        (project_root / "notes" / "daily" / "2026-04-20_auto_trading_daily.md").write_text("# Daily\n", encoding="utf-8")
        (project_root / "notes" / "weeks" / "2026-04-20_2026-04-24_auto_trading_weekly.md").write_text(
            "# Weekly\n",
            encoding="utf-8",
        )
        (project_root / "reports" / "auto_trading" / "daily").mkdir(parents=True, exist_ok=True)
        (project_root / "reports" / "auto_trading" / "daily" / "2026-04-20.html").write_text(
            "<html></html>",
            encoding="utf-8",
        )
        (project_root / "data" / "auto_trading" / "2026-04-20" / "state.json").write_text(
            json.dumps({"provider_name": "manual_csv", "buy_cutoff_day": "2026-04-22", "last_trade_day": "2026-04-24"}),
            encoding="utf-8",
        )

        settings = Settings(
            api_key=None,
            secret_key=None,
            person_id=None,
            ca_path=None,
            ca_password=None,
            default_simulation=True,
            allow_live_submit=False,
            default_order_lot="IntradayOdd",
            budget_per_order=100000,
            price_buffer_pct=0.3,
            max_orders=5,
            auto_trading=AutoTradingConfig(),
            fees=FeeConfig(),
            providers=ProviderConfig(active="manual_csv", definitions={}),
            project_root=project_root,
            obsidian_vault_root=vault_root,
        )

        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        written = sync_obsidian_snapshot(settings, trade_date=date(2026, 4, 20), event_summary="test sync")

        self.assertEqual(len(written), 4)
        status_path = vault_root / "90_current" / "current_system_status.md"
        commands_path = vault_root / "90_current" / "current_commands.md"
        daily_path = vault_root / "30_daily" / "2026-04-20_auto_trading_sync.md"
        weekly_path = vault_root / "20_weeks" / "2026-04-20_2026-04-24_auto_trading_weekly.md"

        self.assertTrue(status_path.exists())
        self.assertTrue(daily_path.exists())
        self.assertTrue(weekly_path.exists())

        status_text = status_path.read_text(encoding="utf-8")
        commands_text = commands_path.read_text(encoding="utf-8")
        daily_text = daily_path.read_text(encoding="utf-8")
        weekly_text = weekly_path.read_text(encoding="utf-8")

        self.assertIn("2330 / Buy / IntradayOdd / 1股 / 09:10 / 價格上限 2100", status_text)
        self.assertIn("先讀筆記", status_text)
        self.assertIn("判讀原則", status_text)
        self.assertIn("run_allowed_live_order", commands_text)
        self.assertIn("workflow_status --trade-date 2026-04-20", commands_text)
        self.assertIn("-AtTime 09:10 -UntilTime 13:20 -RetryIntervalMinutes 5", commands_text)
        self.assertIn("共同 preflight", commands_text)
        self.assertIn("避免重複下單", commands_text)
        self.assertIn("exit code 0", commands_text)
        self.assertIn("sell_loop --trade-date 2026-04-24 --live --confirm-live", commands_text)
        self.assertIn("以 [[我的箴言語錄]] 與 [[使用者操作偏好]] 為主", daily_text)
        self.assertIn("目前唯一受 guard 的 live automation", weekly_text)


if __name__ == "__main__":
    unittest.main()
