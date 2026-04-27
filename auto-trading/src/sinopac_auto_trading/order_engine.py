from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum

from .tick import normalize_price_to_valid_tick, tick_distance, tick_up


class BuyMode(str, Enum):
    NORMAL = "normal"
    ADD = "add"
    SUPER_ADD = "super_add"


@dataclass(slots=True)
class ManagedOrder:
    strategy_lot_id: str
    stock_id: str
    order_id: str
    order_price: float
    order_qty: int
    filled_qty: int
    remaining_qty: int
    active: bool = True


@dataclass(slots=True)
class QuoteState:
    last_price: float
    bid1: float | None = None
    ask1: float | None = None
    limit_up_price: float | None = None
    limit_down_price: float | None = None


@dataclass(slots=True)
class OrderAction:
    action: str
    target_price: float | None
    remaining_qty: int
    reason: str


def current_buy_mode(check_time: datetime) -> BuyMode:
    current_clock = check_time.time()
    if current_clock < time(12, 30):
        return BuyMode.NORMAL
    if current_clock < time(13, 0):
        return BuyMode.ADD
    return BuyMode.SUPER_ADD


def current_mode_target_price(quote: QuoteState, mode: BuyMode) -> float:
    last_price = normalize_price_to_valid_tick(quote.last_price)
    if mode == BuyMode.NORMAL:
        target = last_price
    elif mode == BuyMode.ADD:
        target = normalize_price_to_valid_tick(quote.ask1 or tick_up(last_price, 1))
    else:
        ask_leg = tick_up(quote.ask1 or last_price, 1)
        target = max(tick_up(last_price, 2), ask_leg)
    if quote.limit_up_price is not None:
        target = min(target, normalize_price_to_valid_tick(quote.limit_up_price))
    return normalize_price_to_valid_tick(target)


def ensure_single_active_order(orders: list[ManagedOrder]) -> None:
    active_orders = [order for order in orders if order.active]
    if len(active_orders) > 1:
        raise RuntimeError("Duplicate active order risk detected.")


def plan_order_action(
    existing_order: ManagedOrder | None,
    *,
    target_price: float,
    remaining_qty: int,
    reprice_threshold_ticks: int = 5,
) -> OrderAction:
    if remaining_qty <= 0:
        return OrderAction("done", None, 0, "no remaining quantity")
    if existing_order is None:
        return OrderAction("place", target_price, remaining_qty, "no existing order")

    distance = tick_distance(existing_order.order_price, target_price)
    if distance >= reprice_threshold_ticks:
        return OrderAction("cancel_replace", target_price, remaining_qty, f"distance={distance} ticks")
    return OrderAction("keep", existing_order.order_price, remaining_qty, f"distance={distance} ticks")
