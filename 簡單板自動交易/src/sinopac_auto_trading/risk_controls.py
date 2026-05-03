from __future__ import annotations

from datetime import datetime

from .config import FeeConfig
from dataclasses import dataclass


@dataclass(slots=True)
class BuySubmissionGate:
    allowed: bool
    status: str
    reason: str


def estimate_buy_order_cost(
    price: float,
    qty: int,
    *,
    fees: FeeConfig | None = None,
    buffer_multiplier: float = 1.0,
) -> float:
    if price <= 0 or qty <= 0:
        return 0.0
    gross = price * qty
    fee = fees.estimate_buy_fee(gross) if fees is not None else 0.0
    return (gross + fee) * buffer_multiplier


def affordable_buy_qty(
    *,
    requested_qty: int,
    target_price: float,
    remaining_budget: float,
    order_lot: str = "intraday_odd_lot",
    fees: FeeConfig | None = None,
    buffer_multiplier: float = 1.0,
) -> int:
    if requested_qty <= 0 or target_price <= 0 or remaining_budget <= 0:
        return 0

    lot_size = 1000 if order_lot.lower() == "common" else 1
    max_lots = requested_qty // lot_size
    if max_lots <= 0:
        return 0

    low = 0
    high = max_lots
    best_lots = 0
    while low <= high:
        mid = (low + high) // 2
        qty = mid * lot_size
        estimated_cost = estimate_buy_order_cost(
            target_price,
            qty,
            fees=fees,
            buffer_multiplier=buffer_multiplier,
        )
        if estimated_cost <= remaining_budget:
            best_lots = mid
            low = mid + 1
        else:
            high = mid - 1
    return best_lots * lot_size


def parse_quote_timestamp(raw: object) -> datetime | None:
    if isinstance(raw, datetime):
        return raw
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def quote_is_stale(
    quote_timestamp: datetime | None,
    *,
    now: datetime,
    stale_seconds: int,
) -> bool:
    if stale_seconds <= 0 or quote_timestamp is None:
        return False
    comparable_quote_timestamp = quote_timestamp
    if comparable_quote_timestamp.tzinfo is None and now.tzinfo is not None:
        comparable_quote_timestamp = comparable_quote_timestamp.replace(tzinfo=now.tzinfo)
    return (now - comparable_quote_timestamp).total_seconds() > stale_seconds


def live_buy_quote_gate(
    *,
    requested_action: str,
    quote_is_fresh: bool,
    existing_order_active: bool,
) -> BuySubmissionGate:
    if requested_action == "done":
        return BuySubmissionGate(
            allowed=False,
            status="done",
            reason="no_remaining_quantity",
        )
    if quote_is_fresh:
        return BuySubmissionGate(
            allowed=True,
            status="ready",
            reason="quote_fresh",
        )
    if existing_order_active:
        return BuySubmissionGate(
            allowed=False,
            status="keep_existing_due_stale_quote",
            reason="quote_stale_keep_existing_order",
        )
    return BuySubmissionGate(
        allowed=False,
        status="blocked_stale_quote",
        reason="quote_stale",
    )
