from __future__ import annotations

import unittest
from datetime import date, datetime

from sinopac_auto_trading.accounting import (
    build_pnl_snapshot,
    build_positions_rows_from_fills,
    compute_sell_fill_stats,
)
from sinopac_auto_trading.config import FeeConfig
from sinopac_auto_trading.order_engine import QuoteState
from sinopac_auto_trading.sell_policy import StrategyPosition


class StrategyAccountingTests(unittest.TestCase):
    def test_positions_follow_running_average_after_sell_then_rebuy(self) -> None:
        rows = build_positions_rows_from_fills(
            run_id="auto-2026-04-22",
            trade_date=date(2026, 4, 22),
            fills_rows=[
                {"stock_id": "2330", "side": "Buy", "fill_qty": 1, "fill_price": 100.0, "fill_time": "2026-04-22T09:00:00+08:00"},
                {"stock_id": "2330", "side": "Sell", "fill_qty": 1, "fill_price": 110.0, "fill_time": "2026-04-22T10:00:00+08:00"},
                {"stock_id": "2330", "side": "Buy", "fill_qty": 1, "fill_price": 200.0, "fill_time": "2026-04-22T11:00:00+08:00"},
            ],
            selection_meta_by_stock={"2330": {"stock_name": "TSMC", "source": "A+B"}},
            quote_rows_by_stock={"2330": QuoteState(last_price=205.0, bid1=204.0, ask1=205.0)},
            strategy_lot_id_for_stock=lambda stock_id: f"auto-2026-04-22:{stock_id}",
        )
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["holding_qty"], 1)
        self.assertAlmostEqual(float(row["buy_avg_price"]), 200.0)
        self.assertAlmostEqual(float(row["buy_total_cost"]), 200.0)
        self.assertAlmostEqual(float(row["current_price"]), 205.0)

    def test_positions_apply_same_day_sells_to_opening_strategy_position(self) -> None:
        rows = build_positions_rows_from_fills(
            run_id="auto-2026-04-24",
            trade_date=date(2026, 4, 24),
            opening_positions=[
                StrategyPosition(
                    strategy_lot_id="auto-2026-04-22:2330",
                    stock_id="2330",
                    stock_name="TSMC",
                    holding_qty=5,
                    buy_avg_price=100.0,
                    buy_total_cost=500.0,
                    source="A",
                )
            ],
            fills_rows=[
                {"stock_id": "2330", "side": "Sell", "fill_qty": 2, "fill_price": 110.0, "fill_time": "2026-04-24T13:01:00+08:00"}
            ],
            selection_meta_by_stock={"2330": {"stock_name": "TSMC", "source": "A"}},
            quote_rows_by_stock={"2330": QuoteState(last_price=109.0, bid1=109.0, ask1=109.5)},
            strategy_lot_id_for_stock=lambda stock_id: f"auto-2026-04-24:{stock_id}",
        )
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["strategy_lot_id"], "auto-2026-04-22:2330")
        self.assertEqual(row["holding_qty"], 3)
        self.assertAlmostEqual(float(row["buy_avg_price"]), 100.0)
        self.assertAlmostEqual(float(row["buy_total_cost"]), 300.0)
        self.assertAlmostEqual(float(row["current_price"]), 109.0)

    def test_sell_fill_stats_keep_allocated_cost_basis_and_realized_profit(self) -> None:
        stats = compute_sell_fill_stats(
            fills_rows=[
                {"strategy_lot_id": "auto-2026-04-22:2330", "stock_id": "2330", "side": "Sell", "fill_qty": 2, "fill_price": 110.0, "fill_time": "2026-04-24T13:01:00+08:00"},
                {"strategy_lot_id": "auto-2026-04-22:2330", "stock_id": "2330", "side": "Sell", "fill_qty": 1, "fill_price": 112.0, "fill_time": "2026-04-24T13:05:00+08:00"},
            ],
            opening_positions=[
                StrategyPosition(
                    strategy_lot_id="auto-2026-04-22:2330",
                    stock_id="2330",
                    stock_name="TSMC",
                    holding_qty=5,
                    buy_avg_price=100.0,
                    buy_total_cost=500.0,
                    source="A",
                )
            ],
            selection_meta_by_stock={"2330": {"stock_name": "TSMC", "source": "A"}},
            strategy_lot_id_for_stock=lambda stock_id: f"auto-2026-04-24:{stock_id}",
            fees=FeeConfig(
                commission_rate=0.0,
                commission_discount=1.0,
                minimum_commission=0.0,
                transaction_tax_rate_stock=0.0,
                transaction_tax_rate_day_trade=0.0,
            ),
        )
        stock_stats = stats["auto-2026-04-22:2330"]
        self.assertEqual(stock_stats["sold_qty"], 3)
        self.assertEqual(stock_stats["remaining_qty"], 2)
        self.assertAlmostEqual(stock_stats["allocated_buy_cost"], 300.0)
        self.assertAlmostEqual(stock_stats["fill_avg_price"], 332.0 / 3.0)
        self.assertAlmostEqual(stock_stats["realized_pnl"], 32.0)

    def test_positions_keep_same_stock_split_by_strategy_lot(self) -> None:
        rows = build_positions_rows_from_fills(
            run_id="auto-2026-04-22",
            trade_date=date(2026, 4, 22),
            fills_rows=[
                {"strategy_lot_id": "auto-2026-04-22:2330", "stock_id": "2330", "side": "Buy", "fill_qty": 1, "fill_price": 100.0, "fill_time": "2026-04-22T09:00:00+08:00"},
                {"strategy_lot_id": "auto-2026-04-22:secondary_add:2330", "stock_id": "2330", "side": "Buy", "fill_qty": 2, "fill_price": 95.0, "fill_time": "2026-04-22T10:00:00+08:00"},
            ],
            selection_meta_by_stock={"2330": {"stock_name": "TSMC", "source": "A+B", "basket_tag": "main"}},
            selection_meta_by_strategy_lot={
                "auto-2026-04-22:2330": {"stock_name": "TSMC", "source": "A", "basket_tag": "main"},
                "auto-2026-04-22:secondary_add:2330": {"stock_name": "TSMC", "source": "B", "basket_tag": "secondary_add"},
            },
            quote_rows_by_stock={"2330": QuoteState(last_price=101.0, bid1=100.5, ask1=101.0)},
            strategy_lot_id_for_stock=lambda stock_id: f"auto-2026-04-22:{stock_id}",
        )
        self.assertEqual(len(rows), 2)
        by_lot = {row["strategy_lot_id"]: row for row in rows}
        self.assertEqual(by_lot["auto-2026-04-22:2330"]["holding_qty"], 1)
        self.assertEqual(by_lot["auto-2026-04-22:2330"]["basket_tag"], "main")
        self.assertEqual(by_lot["auto-2026-04-22:secondary_add:2330"]["holding_qty"], 2)
        self.assertEqual(by_lot["auto-2026-04-22:secondary_add:2330"]["basket_tag"], "secondary_add")
        self.assertAlmostEqual(float(by_lot["auto-2026-04-22:secondary_add:2330"]["buy_avg_price"]), 95.0)

    def test_positions_skip_ambiguous_fill_rows_without_safe_lot_mapping(self) -> None:
        rows = build_positions_rows_from_fills(
            run_id="auto-2026-04-22",
            trade_date=date(2026, 4, 22),
            fills_rows=[
                {
                    "strategy_lot_id": "",
                    "stock_id": "2330",
                    "side": "Buy",
                    "fill_qty": 1,
                    "fill_price": 100.0,
                    "fill_time": "2026-04-22T09:00:00+08:00",
                    "fill_assignment_status": "ambiguous_unmapped_fill",
                }
            ],
            selection_meta_by_stock={"2330": {"stock_name": "TSMC", "source": "A+B", "basket_tag": "main"}},
            quote_rows_by_stock={"2330": QuoteState(last_price=101.0, bid1=100.5, ask1=101.0)},
            strategy_lot_id_for_stock=lambda stock_id: f"auto-2026-04-22:{stock_id}",
        )
        self.assertEqual(rows, [])

    def test_pnl_snapshot_includes_realized_profit_in_total_return(self) -> None:
        snapshot = build_pnl_snapshot(
            run_id="auto-2026-04-24",
            trade_date=date(2026, 4, 24),
            positions_rows=[
                {
                    "stock_id": "2330",
                    "holding_qty": 2,
                    "buy_avg_price": 100.0,
                    "buy_total_cost": 200.0,
                    "current_price": 110.0,
                }
            ],
            realized_pnl=32.0,
            realized_cost_basis=300.0,
            snapshot_time=datetime(2026, 4, 24, 13, 30, 0),
        )
        self.assertAlmostEqual(snapshot["unrealized_pnl"], 20.0)
        self.assertAlmostEqual(snapshot["total_pnl_after_fee_tax"], 52.0)
        self.assertAlmostEqual(snapshot["strategy_return"], 52.0 / 500.0)


if __name__ == "__main__":
    unittest.main()
