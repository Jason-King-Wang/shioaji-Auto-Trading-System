from __future__ import annotations

import csv
import shutil
import unittest
import uuid
from pathlib import Path

from sinopac_auto_trading.config import FeeConfig
from sinopac_auto_trading.quick_simulator import (
    load_quote_prices,
    parse_stock_buy_request,
    parse_stock_sell_request,
    resolve_request_prices,
    resolve_sell_request_prices,
    simulate_buy_orders,
    simulate_sell_orders,
    write_simulation_csv,
)


class QuickSimulatorTests(unittest.TestCase):
    def _case_dir(self, name: str) -> Path:
        path = Path(__file__).resolve().parent / "_tmp" / f"{name}-{uuid.uuid4().hex}"
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def test_parse_stock_buy_request_accepts_price_and_weight(self) -> None:
        request = parse_stock_buy_request("2330:982:2")

        self.assertEqual(request.stock_id, "2330")
        self.assertEqual(request.limit_price, 982.0)
        self.assertEqual(request.weight, 2.0)

    def test_simulate_buy_orders_never_submits_to_broker(self) -> None:
        request = parse_stock_buy_request("2317:151")

        result = simulate_buy_orders(
            [request],
            budget=10000,
            fees=FeeConfig(minimum_commission=20),
            buffer_multiplier=1.015,
        )

        self.assertEqual(result.mode, "simulation_only")
        self.assertEqual(result.broker, "sinopac")
        self.assertEqual(len(result.orders), 1)
        self.assertEqual(result.orders[0].status, "dry_run")
        self.assertEqual(result.orders[0].order_id, "DRY-0001")
        self.assertFalse(result.orders[0].submitted_to_broker)
        self.assertGreater(result.orders[0].quantity, 0)
        self.assertLessEqual(result.projected_total_cost, result.budget)

    def test_common_lot_rounds_down_to_thousand_shares(self) -> None:
        request = parse_stock_buy_request("2317:151")

        result = simulate_buy_orders(
            [request],
            budget=200000,
            fees=FeeConfig(minimum_commission=20),
            order_lot="common",
            buffer_multiplier=1.0,
        )

        self.assertEqual(result.orders[0].quantity, 1000)

    def test_resolve_request_prices_uses_quote_file(self) -> None:
        temp_dir = self._case_dir("quotes")
        quote_path = temp_dir / "quotes.csv"
        with quote_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["stock_id", "last_price", "timestamp"])
            writer.writeheader()
            writer.writerow({"stock_id": "2330", "last_price": "982", "timestamp": "2026-04-20T09:00:00"})

        quotes = load_quote_prices(quote_path)
        resolved = resolve_request_prices([parse_stock_buy_request("2330")], quotes)

        self.assertEqual(resolved[0].limit_price, 982.0)

    def test_write_simulation_csv_outputs_order_rows(self) -> None:
        temp_dir = self._case_dir("simulation-csv")
        result = simulate_buy_orders(
            [parse_stock_buy_request("2317:151")],
            budget=10000,
            fees=FeeConfig(minimum_commission=20),
        )

        path = write_simulation_csv(result, temp_dir / "orders.csv")

        content = path.read_text(encoding="utf-8-sig")
        self.assertIn("submitted_to_broker", content)
        self.assertIn("False", content)
        self.assertIn("DRY-0001", content)

    def test_parse_stock_sell_request_accepts_price_and_quantity(self) -> None:
        request = parse_stock_sell_request("2330:982:100")

        self.assertEqual(request.stock_id, "2330")
        self.assertEqual(request.limit_price, 982.0)
        self.assertEqual(request.quantity, 100)

    def test_simulate_sell_orders_is_sinopac_simulation_only(self) -> None:
        request = parse_stock_sell_request("2330:982", quantity=100)

        result = simulate_sell_orders(
            [request],
            fees=FeeConfig(minimum_commission=20),
        )

        self.assertEqual(result.broker, "sinopac")
        self.assertEqual(result.mode, "simulation_only")
        self.assertEqual(result.orders[0].status, "dry_run")
        self.assertEqual(result.orders[0].order_id, "DRY-0001")
        self.assertFalse(result.orders[0].submitted_to_broker)
        self.assertAlmostEqual(result.orders[0].gross_amount, 98200.0)
        self.assertGreater(result.orders[0].estimated_net_amount, 0)

    def test_resolve_sell_request_prices_uses_quote_file(self) -> None:
        temp_dir = self._case_dir("sell-quotes")
        quote_path = temp_dir / "quotes.csv"
        with quote_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["stock_id", "last_price", "timestamp"])
            writer.writeheader()
            writer.writerow({"stock_id": "2317", "last_price": "151", "timestamp": "2026-04-20T09:00:00"})

        quotes = load_quote_prices(quote_path)
        resolved = resolve_sell_request_prices([parse_stock_sell_request("2317", quantity=100)], quotes)

        self.assertEqual(resolved[0].limit_price, 151.0)


if __name__ == "__main__":
    unittest.main()
