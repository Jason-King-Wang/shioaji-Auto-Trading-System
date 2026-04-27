from __future__ import annotations

import unittest

from sinopac_auto_trading.tick import normalize_price_to_valid_tick, tick_distance, tick_down, tick_up


class TickTests(unittest.TestCase):
    def test_tick_up_and_down(self) -> None:
        self.assertEqual(tick_up(49.95, 1), 50.0)
        self.assertEqual(tick_down(50.0, 1), 49.95)

    def test_tick_distance(self) -> None:
        self.assertEqual(tick_distance(100.0, 101.0), 2)

    def test_normalize_price(self) -> None:
        self.assertEqual(normalize_price_to_valid_tick(50.07), 50.0)

    def test_invalid_price_raises(self) -> None:
        with self.assertRaises(ValueError):
            normalize_price_to_valid_tick(0)


if __name__ == "__main__":
    unittest.main()
