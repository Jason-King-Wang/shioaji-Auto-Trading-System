from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from sinopac_auto_trading.allowed_live_order import (
    CHECK_INTERVAL_SECONDS,
    REPRICE_THRESHOLD_TICKS,
    TARGET_END_TIME,
    TARGET_ORDER_LOT,
    TARGET_PRICE_CAP,
    TARGET_QUANTITY,
    TARGET_START_TIME,
    TARGET_STOCK_ID,
)
from sinopac_auto_trading.live_order_chase import (
    _place_order,
    capped_target_price,
    classify_trade_state,
    parse_hhmm,
    run_single_stock_chase,
)
from sinopac_auto_trading.order_engine import (
    BuyMode,
    ManagedOrder,
    QuoteState,
    current_buy_mode,
    current_mode_target_price,
    plan_order_action,
)


def _trade(*, quantity: int, price: float, status: str = "", filled: int = 0, cancelled: int = 0):
    return SimpleNamespace(
        order=SimpleNamespace(id="OID1", quantity=quantity, price=price),
        status=SimpleNamespace(status=status, order_quantity=quantity, deal_quantity=filled, cancel_quantity=cancelled),
    )


class LiveOrderChaseTests(unittest.TestCase):
    def test_allowed_2330_live_order_constants_match_guarded_buy_plan(self) -> None:
        self.assertEqual(TARGET_STOCK_ID, "2330")
        self.assertEqual(TARGET_ORDER_LOT, "IntradayOdd")
        self.assertEqual(TARGET_QUANTITY, 1)
        self.assertEqual(TARGET_PRICE_CAP, 2100.0)
        self.assertEqual(TARGET_START_TIME, "09:10")
        self.assertEqual(TARGET_END_TIME, "13:20")
        self.assertEqual(CHECK_INTERVAL_SECONDS, 300)
        self.assertEqual(REPRICE_THRESHOLD_TICKS, 5)

    def test_parse_hhmm(self) -> None:
        self.assertEqual(parse_hhmm("09:10").hour, 9)
        self.assertEqual(parse_hhmm("09:10").minute, 10)

    def test_buy_target_price_respects_cap(self) -> None:
        self.assertEqual(capped_target_price("Buy", 2030.0, 2025.0), 2025.0)
        self.assertEqual(capped_target_price("Buy", 2020.0, 2025.0), 2020.0)
        self.assertEqual(capped_target_price("Buy", 2110.0, 2100.0), 2100.0)

    def test_time_based_buy_modes_and_target_prices_match_chase_plan(self) -> None:
        quote = QuoteState(last_price=2085.0, bid1=2080.0, ask1=2090.0, limit_up_price=2200.0)

        self.assertEqual(current_buy_mode(datetime(2026, 4, 24, 9, 10)), BuyMode.NORMAL)
        self.assertEqual(current_mode_target_price(quote, BuyMode.NORMAL), 2085.0)

        self.assertEqual(current_buy_mode(datetime(2026, 4, 24, 12, 30)), BuyMode.ADD)
        self.assertEqual(current_mode_target_price(quote, BuyMode.ADD), 2090.0)

        self.assertEqual(current_buy_mode(datetime(2026, 4, 24, 13, 0)), BuyMode.SUPER_ADD)
        self.assertEqual(current_mode_target_price(quote, BuyMode.SUPER_ADD), 2095.0)

    def test_cancel_replace_only_when_target_moves_at_least_five_ticks(self) -> None:
        existing = ManagedOrder(
            strategy_lot_id="T2001",
            stock_id="2330",
            order_id="OID1",
            order_price=2065.0,
            order_qty=1,
            filled_qty=0,
            remaining_qty=1,
        )

        order_action = plan_order_action(
            existing,
            target_price=2090.0,
            remaining_qty=1,
            reprice_threshold_ticks=5,
        )

        self.assertEqual(order_action.action, "cancel_replace")
        self.assertEqual(order_action.target_price, 2090.0)

    def test_place_order_uses_limit_rod_intraday_odd_lot(self) -> None:
        fake_sj = SimpleNamespace(
            constant=SimpleNamespace(
                Action=SimpleNamespace(Buy="BUY"),
                StockPriceType=SimpleNamespace(LMT="LMT"),
                OrderType=SimpleNamespace(ROD="ROD"),
                StockOrderLot=SimpleNamespace(IntradayOdd="INTRADAY_ODD"),
            )
        )

        class FakeApi:
            stock_account = "stock-account"

            def __init__(self) -> None:
                self.order_kwargs = {}

            def Order(self, **kwargs):
                self.order_kwargs = kwargs
                return SimpleNamespace(**kwargs)

            def place_order(self, contract, order):
                return SimpleNamespace(contract=contract, order=order)

        api = FakeApi()
        contract = SimpleNamespace(code="2330")
        with patch("sinopac_auto_trading.live_order_chase._load_shioaji", return_value=fake_sj):
            trade = _place_order(
                api,
                contract,
                action="Buy",
                order_lot="IntradayOdd",
                price=2090.0,
                quantity=1,
                custom_field="T2001",
            )

        self.assertIs(trade.contract, contract)
        self.assertEqual(api.order_kwargs["price"], 2090.0)
        self.assertEqual(api.order_kwargs["quantity"], 1)
        self.assertEqual(api.order_kwargs["action"], "BUY")
        self.assertEqual(api.order_kwargs["price_type"], "LMT")
        self.assertEqual(api.order_kwargs["order_type"], "ROD")
        self.assertEqual(api.order_kwargs["order_lot"], "INTRADAY_ODD")

    def test_classify_trade_state_filled(self) -> None:
        self.assertEqual(classify_trade_state(_trade(quantity=1, price=100.0, status="Filled", filled=1)), "filled")

    def test_classify_trade_state_cancelled(self) -> None:
        self.assertEqual(classify_trade_state(_trade(quantity=1, price=100.0, status="Cancelled", cancelled=1)), "cancelled")

    def test_classify_trade_state_active(self) -> None:
        self.assertEqual(classify_trade_state(_trade(quantity=1, price=100.0, status="Submitted")), "active")

    def test_run_single_stock_chase_requires_confirm_live_guard(self) -> None:
        settings = SimpleNamespace(
            evaluate_live_submit_guard=lambda *, confirm_live: (False, "confirm_live_missing"),
        )
        with self.assertRaisesRegex(RuntimeError, "--confirm-live"):
            run_single_stock_chase(
                settings=settings,
                stock_id="2330",
                exchange="TSE",
                action="Buy",
                order_lot="IntradayOdd",
                quantity=1,
                price_cap=2100.0,
                live=True,
                submit=True,
                confirm_live=False,
                start_time=parse_hhmm("09:10"),
                end_time=parse_hhmm("13:20"),
                check_interval_seconds=300,
                reprice_threshold_ticks=5,
                custom_prefix="CH",
            )


if __name__ == "__main__":
    unittest.main()
