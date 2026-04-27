from __future__ import annotations

import unittest
from datetime import date

from sinopac_auto_trading.config import AutoTradingConfig, FeeConfig
from sinopac_auto_trading.selection_provider import SelectionItem
from sinopac_auto_trading.sizing import size_selection


class SizingTests(unittest.TestCase):
    def test_ab_weights_and_target_qty_priority(self) -> None:
        auto = AutoTradingConfig(weekly_budget=10_000_000, overrun_tolerance=50000)
        fees = FeeConfig(minimum_commission=1)
        items = [
            SelectionItem(stock_id="2330", stock_name="TSMC", source="A"),
            SelectionItem(stock_id="2317", stock_name="Hon Hai", source="B"),
            SelectionItem(stock_id="2454", stock_name="MediaTek", source="A+B"),
            SelectionItem(stock_id="1101", stock_name="TCC", source="manual", target_qty=7),
        ]
        prices = {"2330": 100.0, "2317": 100.0, "2454": 100.0, "1101": 10.0}

        result = size_selection(items, prices, auto=auto, fees=fees)
        qty_by_stock = {row.item.stock_id: row.target_qty for row in result.rows}

        self.assertEqual(qty_by_stock["2330"], qty_by_stock["2317"])
        self.assertEqual(qty_by_stock["2454"], qty_by_stock["2330"] * 2)
        self.assertEqual(qty_by_stock["1101"], 7)
        self.assertEqual(result.hard_budget, 10_050_000)

    def test_reduce_only_when_projected_total_exceeds_hard_budget(self) -> None:
        auto = AutoTradingConfig(weekly_budget=1000, overrun_tolerance=0, cost_buffer_multiplier=1.0)
        fees = FeeConfig(minimum_commission=0, commission_rate=0.0)
        items = [
            SelectionItem(stock_id="2330", stock_name="TSMC", source="A"),
            SelectionItem(stock_id="2454", stock_name="MediaTek", source="A+B"),
        ]
        prices = {"2330": 400.0, "2454": 400.0}

        result = size_selection(items, prices, auto=auto, fees=fees)

        self.assertLessEqual(result.projected_total_cost, result.hard_budget)

    def test_locked_qty_is_not_reduced(self) -> None:
        auto = AutoTradingConfig(weekly_budget=1000, overrun_tolerance=0, cost_buffer_multiplier=1.0)
        fees = FeeConfig(minimum_commission=0, commission_rate=0.0)
        items = [
            SelectionItem(stock_id="2330", stock_name="TSMC", source="A"),
            SelectionItem(stock_id="2454", stock_name="MediaTek", source="A"),
        ]
        prices = {"2330": 500.0, "2454": 500.0}

        result = size_selection(
            items,
            prices,
            auto=auto,
            fees=fees,
            locked_qty_by_stock={"2330": 1},
        )
        qty_by_stock = {row.item.stock_id: row.target_qty for row in result.rows}
        self.assertGreaterEqual(qty_by_stock["2330"], 1)

    def test_live_smoke_test_qty_uses_1_and_2_shares(self) -> None:
        auto = AutoTradingConfig(weekly_budget=100000, overrun_tolerance=50000, test_qty_single_source=1, test_qty_dual_source=2)
        fees = FeeConfig(minimum_commission=1)
        items = [
            SelectionItem(stock_id="2330", stock_name="TSMC", source="manual"),
            SelectionItem(stock_id="2454", stock_name="MediaTek", source="A+B"),
        ]
        prices = {"2330": 1000.0, "2454": 1000.0}

        result = size_selection(items, prices, auto=auto, fees=fees, smoke_test=True)
        qty_by_stock = {row.item.stock_id: row.target_qty for row in result.rows}
        self.assertEqual(qty_by_stock["2330"], 1)
        self.assertEqual(qty_by_stock["2454"], 2)

    def test_secondary_add_is_zeroed_before_second_trade_day(self) -> None:
        auto = AutoTradingConfig(
            weekly_budget=1000,
            overrun_tolerance=0,
            cost_buffer_multiplier=1.0,
            enable_secondary_add=True,
            secondary_add_budget_pct_min=0.30,
            secondary_add_budget_pct_max=0.40,
        )
        fees = FeeConfig(minimum_commission=0, commission_rate=0.0)
        items = [
            SelectionItem(stock_id="2330", stock_name="TSMC", source="A", basket_tag="main"),
            SelectionItem(stock_id="2454", stock_name="MediaTek", source="B", basket_tag="secondary_add"),
        ]
        prices = {"2330": 100.0, "2454": 100.0}

        result = size_selection(
            items,
            prices,
            auto=auto,
            fees=fees,
            trade_date=date(2026, 4, 21),
            week_trade_days=[date(2026, 4, 21), date(2026, 4, 22), date(2026, 4, 23)],
        )
        qty_by_stock = {row.item.stock_id: row.target_qty for row in result.rows}
        self.assertEqual(qty_by_stock["2330"], 10)
        self.assertEqual(qty_by_stock["2454"], 0)

    def test_secondary_add_uses_capped_budget_ratio_on_second_trade_day(self) -> None:
        auto = AutoTradingConfig(
            weekly_budget=1000,
            overrun_tolerance=0,
            cost_buffer_multiplier=1.0,
            enable_secondary_add=True,
            secondary_add_budget_pct_min=0.30,
            secondary_add_budget_pct_max=0.40,
        )
        fees = FeeConfig(minimum_commission=0, commission_rate=0.0)
        items = [
            SelectionItem(stock_id="2330", stock_name="TSMC", source="A", basket_tag="main"),
            SelectionItem(stock_id="2454", stock_name="MediaTek", source="B", basket_tag="secondary_add"),
        ]
        prices = {"2330": 100.0, "2454": 100.0}

        result = size_selection(
            items,
            prices,
            auto=auto,
            fees=fees,
            trade_date=date(2026, 4, 22),
            week_trade_days=[date(2026, 4, 21), date(2026, 4, 22), date(2026, 4, 23)],
        )
        by_stock = {row.item.stock_id: row for row in result.rows}
        self.assertEqual(by_stock["2330"].target_qty, 6)
        self.assertEqual(by_stock["2454"].target_qty, 4)
        self.assertLessEqual(result.projected_total_cost, result.hard_budget)


if __name__ == "__main__":
    unittest.main()
