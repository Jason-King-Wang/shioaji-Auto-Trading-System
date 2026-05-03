from __future__ import annotations

import csv
import json
import shutil
import unittest
import uuid
from pathlib import Path

from sinopac_auto_trading.config import FeeConfig
from sinopac_auto_trading.model_order_interface import (
    load_model_order_batch,
    process_model_order_batch,
)


class ModelOrderInterfaceTests(unittest.TestCase):
    def _case_dir(self, name: str) -> Path:
        path = Path(__file__).resolve().parent / "_tmp" / f"{name}-{uuid.uuid4().hex}"
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def _quote_file(self, temp_dir: Path) -> Path:
        path = temp_dir / "quotes.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["stock_id", "last_price", "timestamp"])
            writer.writeheader()
            writer.writerow({"stock_id": "2330", "last_price": "982", "timestamp": "2026-04-20T10:30:00"})
            writer.writerow({"stock_id": "2317", "last_price": "151", "timestamp": "2026-04-20T10:30:00"})
        return path

    def test_json_model_orders_accepts_shared_budget_and_sell_intent(self) -> None:
        temp_dir = self._case_dir("json-model-orders")
        path = temp_dir / "orders.json"
        path.write_text(
            json.dumps(
                {
                    "source_model": "momentum_v1",
                    "buy_budget": 100000,
                    "orders": [
                        {"action": "buy", "stock_id": "2330", "weight": 2, "signal_id": "b1"},
                        {"action": "buy", "stock_id": "2317", "weight": 1, "signal_id": "b2"},
                        {"action": "sell", "stock_id": "2317", "quantity": 100, "signal_id": "s1"},
                    ],
                }
            ),
            encoding="utf-8",
        )

        batch = load_model_order_batch(path)
        result = process_model_order_batch(
            batch,
            fees=FeeConfig(minimum_commission=20),
            quote_file=self._quote_file(temp_dir),
            buffer_multiplier=1.015,
            write_output=False,
        )

        self.assertEqual(result.broker, "sinopac")
        self.assertEqual(result.mode, "simulation_only")
        self.assertEqual(result.source_model, "momentum_v1")
        self.assertEqual(result.order_count, 3)
        self.assertEqual([row["order_id"] for row in result.orders], ["DRY-0001", "DRY-0002", "DRY-0003"])
        self.assertTrue(all(row["submitted_to_broker"] is False for row in result.orders))
        self.assertLessEqual(result.projected_buy_cost, 100000)
        self.assertGreater(result.projected_sell_net_amount, 0)

    def test_json_model_orders_accepts_fixed_buy_quantity(self) -> None:
        temp_dir = self._case_dir("fixed-buy-model-orders")
        path = temp_dir / "orders.json"
        path.write_text(
            json.dumps(
                {
                    "source_model": "fixed_qty_model",
                    "orders": [
                        {"action": "buy", "stock_id": "2330", "price": 982, "quantity": 10, "signal_id": "fixed-buy"}
                    ],
                }
            ),
            encoding="utf-8",
        )

        result = process_model_order_batch(
            load_model_order_batch(path),
            fees=FeeConfig(minimum_commission=20),
            quote_file=self._quote_file(temp_dir),
            buffer_multiplier=1.015,
            write_output=False,
        )

        self.assertEqual(result.orders[0]["side"], "Buy")
        self.assertEqual(result.orders[0]["quantity"], 10)
        self.assertEqual(result.orders[0]["signal_id"], "fixed-buy")
        self.assertGreater(result.orders[0]["estimated_total_cost"], 0)

    def test_csv_model_orders_are_supported(self) -> None:
        temp_dir = self._case_dir("csv-model-orders")
        path = temp_dir / "orders.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["action", "stock_id", "price", "budget", "signal_id"])
            writer.writeheader()
            writer.writerow({"action": "buy", "stock_id": "2330", "price": "982", "budget": "50000", "signal_id": "csv-buy"})

        result = process_model_order_batch(
            load_model_order_batch(path),
            fees=FeeConfig(minimum_commission=20),
            quote_file=self._quote_file(temp_dir),
            buffer_multiplier=1.015,
            write_output=False,
        )

        self.assertEqual(result.source_model, "orders")
        self.assertEqual(result.order_count, 1)
        self.assertEqual(result.orders[0]["signal_id"], "csv-buy")


if __name__ == "__main__":
    unittest.main()
