from __future__ import annotations

import shutil
import unittest
import uuid
from csv import DictWriter
from datetime import date
from pathlib import Path
from unittest.mock import patch

from sinopac_auto_trading.calendar import WeekTradePlan
from sinopac_auto_trading.ledger import (
    load_week_custom_field_lot_lookup,
    load_week_lot_ledger,
    load_week_order_id_lot_lookup,
)


class LedgerTests(unittest.TestCase):
    def _case_dir(self, name: str) -> Path:
        path = Path(__file__).resolve().parent / "_tmp" / f"{name}-{uuid.uuid4().hex}"
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def test_load_week_lot_ledger_aggregates_weekly_rows_by_strategy_lot(self) -> None:
        root = self._case_dir("weekly-ledger")
        self._write_csv(
            root / "2026-04-21" / "orders.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-21:secondary_add:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "source": "A+B",
                    "basket_tag": "secondary_add",
                    "order_id": "BUY-2330-1",
                    "broker_custom_field": "B12345",
                    "status": "submitted",
                }
            ],
        )
        self._write_csv(
            root / "2026-04-21" / "fills.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-21:secondary_add:2330",
                    "stock_id": "2330",
                    "basket_tag": "secondary_add",
                    "side": "Buy",
                    "fill_price": 100.0,
                    "fill_qty": 2,
                    "broker_fill_id": "BUY-2330-1",
                    "broker_custom_field": "B12345",
                }
            ],
        )
        self._write_csv(
            root / "2026-04-24" / "sell_decisions.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-21:secondary_add:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "basket_tag": "secondary_add",
                    "sell_order_id": "SELL-2330-1",
                    "broker_custom_field": "S54321",
                    "sell_order_status": "filled_or_partially_filled",
                    "allocated_buy_cost": 100.0,
                    "realized_pnl": 12.0,
                    "conservative_profit": 10.0,
                }
            ],
        )
        self._write_csv(
            root / "2026-04-24" / "fills.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-21:secondary_add:2330",
                    "stock_id": "2330",
                    "basket_tag": "secondary_add",
                    "side": "Sell",
                    "fill_price": 112.0,
                    "fill_qty": 1,
                    "broker_fill_id": "SELL-2330-1",
                    "broker_custom_field": "S54321",
                }
            ],
        )
        self._write_csv(
            root / "2026-04-24" / "positions.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-21:secondary_add:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "source": "A+B",
                    "basket_tag": "secondary_add",
                    "holding_qty": 1,
                    "buy_avg_price": 100.0,
                    "buy_total_cost": 100.0,
                    "status": "strategy_fill_scoped",
                }
            ],
        )
        plan = WeekTradePlan(
            anchor_date=date(2026, 4, 24),
            week_trade_days=[date(2026, 4, 21), date(2026, 4, 22), date(2026, 4, 24)],
            buy_cutoff_day=date(2026, 4, 22),
            last_trade_day=date(2026, 4, 24),
            calendar_missing_warning=False,
            source_path=None,
        )
        with patch("sinopac_auto_trading.ledger.resolve_week_trade_plan", return_value=plan), patch(
            "sinopac_auto_trading.ledger.auto_trading_dir_for",
            side_effect=lambda trade_date: root / trade_date.isoformat(),
        ):
            rows = load_week_lot_ledger(date(2026, 4, 24))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["strategy_lot_id"], "auto-2026-04-21:secondary_add:2330")
        self.assertEqual(row["basket_tag"], "secondary_add")
        self.assertEqual(row["buy_fill_qty"], 2)
        self.assertEqual(row["sell_fill_qty"], 1)
        self.assertEqual(row["closing_qty"], 1)
        self.assertEqual(row["lot_status"], "open")
        self.assertAlmostEqual(row["realized_pnl"], 12.0)
        self.assertEqual(row["buy_order_ids"], "BUY-2330-1")
        self.assertEqual(row["sell_order_ids"], "SELL-2330-1")
        self.assertEqual(row["fill_order_ids"], "BUY-2330-1|SELL-2330-1")
        self.assertEqual(row["buy_custom_fields"], "B12345")
        self.assertEqual(row["sell_custom_fields"], "S54321")
        self.assertEqual(row["fill_custom_fields"], "B12345|S54321")

    def test_load_week_order_id_lot_lookup_includes_fill_order_ids(self) -> None:
        root = self._case_dir("lookup-ledger")
        self._write_csv(
            root / "2026-04-22" / "fills.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-22:secondary_add:2330",
                    "stock_id": "2330",
                    "basket_tag": "secondary_add",
                    "side": "Buy",
                    "fill_price": 100.0,
                    "fill_qty": 1,
                    "broker_fill_id": "FILL-2330-1",
                }
            ],
        )
        plan = WeekTradePlan(
            anchor_date=date(2026, 4, 24),
            week_trade_days=[date(2026, 4, 22), date(2026, 4, 24)],
            buy_cutoff_day=date(2026, 4, 22),
            last_trade_day=date(2026, 4, 24),
            calendar_missing_warning=False,
            source_path=None,
        )
        with patch("sinopac_auto_trading.ledger.resolve_week_trade_plan", return_value=plan), patch(
            "sinopac_auto_trading.ledger.auto_trading_dir_for",
            side_effect=lambda trade_date: root / trade_date.isoformat(),
        ):
            lookup = load_week_order_id_lot_lookup(date(2026, 4, 24))
        self.assertEqual(lookup["FILL-2330-1"], "auto-2026-04-22:secondary_add:2330")

    def test_load_week_custom_field_lot_lookup_includes_order_and_fill_custom_fields(self) -> None:
        root = self._case_dir("custom-field-lookup")
        self._write_csv(
            root / "2026-04-22" / "orders.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-22:secondary_add:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "basket_tag": "secondary_add",
                    "order_id": "BUY-2330-1",
                    "broker_custom_field": "B12345",
                    "status": "submitted",
                }
            ],
        )
        self._write_csv(
            root / "2026-04-24" / "fills.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-22:secondary_add:2330",
                    "stock_id": "2330",
                    "basket_tag": "secondary_add",
                    "side": "Sell",
                    "fill_price": 112.0,
                    "fill_qty": 1,
                    "broker_fill_id": "SELL-2330-1",
                    "broker_custom_field": "S54321",
                }
            ],
        )
        plan = WeekTradePlan(
            anchor_date=date(2026, 4, 24),
            week_trade_days=[date(2026, 4, 22), date(2026, 4, 24)],
            buy_cutoff_day=date(2026, 4, 22),
            last_trade_day=date(2026, 4, 24),
            calendar_missing_warning=False,
            source_path=None,
        )
        with patch("sinopac_auto_trading.ledger.resolve_week_trade_plan", return_value=plan), patch(
            "sinopac_auto_trading.ledger.auto_trading_dir_for",
            side_effect=lambda trade_date: root / trade_date.isoformat(),
        ):
            lookup = load_week_custom_field_lot_lookup(date(2026, 4, 24))
        self.assertEqual(lookup["B12345"], "auto-2026-04-22:secondary_add:2330")
        self.assertEqual(lookup["S54321"], "auto-2026-04-22:secondary_add:2330")

    @staticmethod
    def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
