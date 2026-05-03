from __future__ import annotations

import os
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from sinopac_auto_trading.broker_adapter import FakeBrokerAdapter
from sinopac_auto_trading.cli import _buy_loop_can_go_live
from sinopac_auto_trading.config import AutoTradingConfig, FeeConfig, ProviderConfig, Settings
from sinopac_auto_trading.live_smoke_test import run_live_smoke_test
from sinopac_auto_trading.quote_provider import MemoryQuoteProvider, QuoteSnapshot
from sinopac_auto_trading.selection_provider import SelectionItem


def _settings(
    *,
    live_enabled: bool,
    allow_live_submit: bool = False,
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
            weekly_budget=weekly_budget,
            weekly_execution_enabled=weekly_execution_enabled,
            overrun_tolerance=50000,
            test_qty_single_source=1,
            test_qty_dual_source=2,
            test_max_total_buy_amount=50000,
            test_max_single_stock_amount=10000,
        ),
        fees=FeeConfig(),
        providers=ProviderConfig(),
        project_root=Path.cwd(),
    )


class DryRunSafetyTests(unittest.TestCase):
    def test_default_dry_run_does_not_place_real_order(self) -> None:
        broker = FakeBrokerAdapter(cash_available=50000)
        quote_provider = MemoryQuoteProvider(
            [
                QuoteSnapshot(
                    stock_id="2330",
                    timestamp=datetime(2026, 4, 20, 10, 30),
                    open_price=980,
                    last_price=990,
                    bid1=989,
                    ask1=990,
                )
            ]
        )
        result = run_live_smoke_test(
            [SelectionItem(stock_id="2330", stock_name="TSMC", source="manual")],
            broker=broker,
            quote_provider=quote_provider,
            settings=_settings(live_enabled=False),
            live=False,
            confirm_live=False,
        )
        self.assertEqual(result.mode, "dry_run")
        self.assertEqual(len(broker.orders), 0)

    def test_live_requires_three_confirmations(self) -> None:
        broker = FakeBrokerAdapter(cash_available=50000)
        quote_provider = MemoryQuoteProvider(
            [
                QuoteSnapshot(
                    stock_id="2330",
                    timestamp=datetime(2026, 4, 20, 10, 30),
                    open_price=980,
                    last_price=990,
                    bid1=989,
                    ask1=990,
                )
            ]
        )
        with patch.dict(os.environ, {}, clear=False):
            result = run_live_smoke_test(
                [SelectionItem(stock_id="2330", stock_name="TSMC", source="manual")],
                broker=broker,
                quote_provider=quote_provider,
                settings=_settings(live_enabled=True, allow_live_submit=True),
                live=True,
                confirm_live=True,
            )
        self.assertEqual(result.mode, "dry_run")
        self.assertEqual(len(broker.orders), 0)

    def test_live_places_order_only_after_all_confirmations(self) -> None:
        broker = FakeBrokerAdapter(cash_available=50000, signed=True)
        quote_provider = MemoryQuoteProvider(
            [
                QuoteSnapshot(
                    stock_id="2330",
                    timestamp=datetime(2026, 4, 20, 10, 30),
                    open_price=980,
                    last_price=990,
                    bid1=989,
                    ask1=990,
                )
            ]
        )
        with patch.dict(os.environ, {"AUTO_TRADE_LIVE": "1"}, clear=False):
            result = run_live_smoke_test(
                [SelectionItem(stock_id="2330", stock_name="TSMC", source="manual")],
                broker=broker,
                quote_provider=quote_provider,
                settings=_settings(live_enabled=True, allow_live_submit=True),
                live=True,
                confirm_live=True,
            )
        self.assertEqual(result.mode, "live")
        self.assertEqual(len(broker.orders), 1)

    def test_smoke_test_total_estimated_amount_uses_fee_buffered_cost(self) -> None:
        broker = FakeBrokerAdapter(cash_available=50000)
        quote_provider = MemoryQuoteProvider(
            [
                QuoteSnapshot(
                    stock_id="2330",
                    timestamp=datetime(2026, 4, 20, 10, 30),
                    open_price=980,
                    last_price=100.0,
                    bid1=99.0,
                    ask1=100.0,
                )
            ]
        )
        settings = _settings(live_enabled=False)
        settings.fees.minimum_commission = 20.0
        settings.auto_trading.cost_buffer_multiplier = 1.015
        result = run_live_smoke_test(
            [SelectionItem(stock_id="2330", stock_name="TSMC", source="manual")],
            broker=broker,
            quote_provider=quote_provider,
            settings=settings,
            live=False,
            confirm_live=False,
        )
        self.assertAlmostEqual(result.total_estimated_amount, 121.8)

    def test_buy_loop_live_guard_requires_explicit_submit_flag(self) -> None:
        settings = _settings(live_enabled=True)
        with patch.dict(os.environ, {"AUTO_TRADE_LIVE": "1"}, clear=False):
            allowed, reason = _buy_loop_can_go_live(settings, live=True, confirm_live=True)
        self.assertFalse(allowed)
        self.assertEqual(reason, "allow_live_submit_disabled")

    def test_buy_loop_live_guard_passes_after_all_confirmations(self) -> None:
        settings = _settings(live_enabled=True, allow_live_submit=True)
        with patch.dict(os.environ, {"AUTO_TRADE_LIVE": "1"}, clear=False):
            allowed, reason = _buy_loop_can_go_live(settings, live=True, confirm_live=True)
        self.assertTrue(allowed)
        self.assertEqual(reason, "live_confirmed")

    def test_buy_loop_live_guard_requires_weekly_execution_enabled(self) -> None:
        settings = _settings(
            live_enabled=True,
            allow_live_submit=True,
            weekly_execution_enabled=False,
            weekly_budget=100000,
        )
        with patch.dict(os.environ, {"AUTO_TRADE_LIVE": "1"}, clear=False):
            allowed, reason = _buy_loop_can_go_live(settings, live=True, confirm_live=True)
        self.assertFalse(allowed)
        self.assertEqual(reason, "weekly_execution_disabled")

    def test_buy_loop_live_guard_requires_positive_weekly_budget(self) -> None:
        settings = _settings(
            live_enabled=True,
            allow_live_submit=True,
            weekly_execution_enabled=True,
            weekly_budget=0,
        )
        with patch.dict(os.environ, {"AUTO_TRADE_LIVE": "1"}, clear=False):
            allowed, reason = _buy_loop_can_go_live(settings, live=True, confirm_live=True)
        self.assertFalse(allowed)
        self.assertEqual(reason, "weekly_budget_missing")


if __name__ == "__main__":
    unittest.main()
