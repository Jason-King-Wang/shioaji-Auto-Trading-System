from __future__ import annotations

import unittest

from sinopac_auto_trading.config import FeeConfig
from datetime import datetime

from sinopac_auto_trading.risk_controls import (
    affordable_buy_qty,
    estimate_buy_order_cost,
    live_buy_quote_gate,
    parse_quote_timestamp,
    quote_is_stale,
)


class RiskControlTests(unittest.TestCase):
    def test_estimate_buy_order_cost_includes_fee_and_buffer(self) -> None:
        cost = estimate_buy_order_cost(
            100.0,
            1,
            fees=FeeConfig(minimum_commission=20.0),
            buffer_multiplier=1.015,
        )
        self.assertAlmostEqual(cost, 121.8)

    def test_affordable_buy_qty_respects_fee_and_buffer(self) -> None:
        qty = affordable_buy_qty(
            requested_qty=2,
            target_price=100.0,
            remaining_budget=122.0,
            fees=FeeConfig(minimum_commission=20.0),
            buffer_multiplier=1.015,
        )
        self.assertEqual(qty, 1)

    def test_affordable_buy_qty_returns_zero_when_buffered_cost_exceeds_budget(self) -> None:
        qty = affordable_buy_qty(
            requested_qty=1,
            target_price=100.0,
            remaining_budget=120.0,
            fees=FeeConfig(minimum_commission=20.0),
            buffer_multiplier=1.015,
        )
        self.assertEqual(qty, 0)

    def test_parse_quote_timestamp_supports_iso_string(self) -> None:
        parsed = parse_quote_timestamp("2026-04-22T09:15:00+08:00")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.isoformat(), "2026-04-22T09:15:00+08:00")

    def test_quote_is_stale_after_threshold(self) -> None:
        is_stale = quote_is_stale(
            parse_quote_timestamp("2026-04-22T09:15:00+08:00"),
            now=datetime.fromisoformat("2026-04-22T09:15:20+08:00"),
            stale_seconds=15,
        )
        self.assertTrue(is_stale)

    def test_live_buy_quote_gate_keeps_existing_order_when_quote_is_stale(self) -> None:
        gate = live_buy_quote_gate(
            requested_action="cancel_replace",
            quote_is_fresh=False,
            existing_order_active=True,
        )
        self.assertFalse(gate.allowed)
        self.assertEqual(gate.status, "keep_existing_due_stale_quote")

    def test_live_buy_quote_gate_blocks_new_order_when_quote_is_stale(self) -> None:
        gate = live_buy_quote_gate(
            requested_action="place",
            quote_is_fresh=False,
            existing_order_active=False,
        )
        self.assertFalse(gate.allowed)
        self.assertEqual(gate.status, "blocked_stale_quote")


if __name__ == "__main__":
    unittest.main()
