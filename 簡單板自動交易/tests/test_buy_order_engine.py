from __future__ import annotations

import unittest

from sinopac_auto_trading.order_engine import ManagedOrder, ensure_single_active_order, plan_order_action


class BuyOrderEngineTests(unittest.TestCase):
    def test_keep_order_when_tick_distance_below_threshold(self) -> None:
        order = ManagedOrder("lot-1", "2330", "A1", 100.0, 2, 0, 2)
        action = plan_order_action(order, target_price=102.0, remaining_qty=2)
        self.assertEqual(action.action, "keep")

    def test_cancel_replace_when_tick_distance_meets_threshold(self) -> None:
        order = ManagedOrder("lot-1", "2330", "A1", 100.0, 2, 0, 2)
        action = plan_order_action(order, target_price=105.0, remaining_qty=2)
        self.assertEqual(action.action, "cancel_replace")

    def test_partial_fill_only_keeps_remaining_qty(self) -> None:
        order = ManagedOrder("lot-1", "2330", "A1", 100.0, 5, 2, 3)
        action = plan_order_action(order, target_price=105.0, remaining_qty=3)
        self.assertEqual(action.remaining_qty, 3)

    def test_duplicate_active_orders_raise(self) -> None:
        orders = [
            ManagedOrder("lot-1", "2330", "A1", 100.0, 1, 0, 1, active=True),
            ManagedOrder("lot-1", "2330", "A2", 101.0, 1, 0, 1, active=True),
        ]
        with self.assertRaises(RuntimeError):
            ensure_single_active_order(orders)


if __name__ == "__main__":
    unittest.main()
