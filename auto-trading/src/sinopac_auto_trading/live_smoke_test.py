from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Any

from .broker_adapter import BrokerAdapter, BrokerOrderResult
from .config import Settings
from .quote_provider import QuoteProvider
from .risk_controls import estimate_buy_order_cost
from .selection_provider import SelectionItem


@dataclass(slots=True)
class SmokeTestOrder:
    stock_id: str
    stock_name: str
    source: str
    qty: int
    price: float
    status: str
    detail: str = ""


@dataclass(slots=True)
class SmokeTestResult:
    mode: str
    total_estimated_amount: float
    orders: list[SmokeTestOrder]


def _smoke_qty(item: SelectionItem, settings: Settings) -> int:
    return (
        settings.auto_trading.test_qty_dual_source
        if item.normalized_source_weight() >= 2
        else settings.auto_trading.test_qty_single_source
    )


def run_live_smoke_test(
    items: list[SelectionItem],
    *,
    broker: BrokerAdapter,
    quote_provider: QuoteProvider | None,
    settings: Settings,
    live: bool,
    confirm_live: bool,
) -> SmokeTestResult:
    if live:
        evaluate_guard = getattr(settings, "evaluate_live_submit_guard", None)
        if callable(evaluate_guard):
            can_go_live, _reason = evaluate_guard(confirm_live=confirm_live, trade_date=date.today())
        else:
            env_live = os.getenv("AUTO_TRADE_LIVE") == "1"
            can_go_live = settings.auto_trading.live_enabled and live and confirm_live and env_live
    else:
        can_go_live = False
    mode = "live" if can_go_live else "dry_run"
    orders: list[SmokeTestOrder] = []
    total_estimated_amount = 0.0

    for item in items:
        snapshot = quote_provider.get_snapshot(item.stock_id) if quote_provider else None
        price = snapshot.last_price if snapshot else 0.0
        qty = _smoke_qty(item, settings)
        estimated_amount = estimate_buy_order_cost(
            price,
            qty,
            fees=settings.fees,
            buffer_multiplier=settings.auto_trading.cost_buffer_multiplier,
        )
        total_estimated_amount += estimated_amount

        if estimated_amount > settings.auto_trading.test_max_single_stock_amount:
            orders.append(SmokeTestOrder(item.stock_id, item.stock_name, item.source, qty, price, "blocked", "single stock amount too high"))
            continue

        if total_estimated_amount > settings.auto_trading.test_max_total_buy_amount:
            orders.append(SmokeTestOrder(item.stock_id, item.stock_name, item.source, qty, price, "blocked", "total smoke test amount too high"))
            continue

        if mode == "live":
            summary = broker.get_account_summary()
            if not summary.signed:
                raise RuntimeError("Live smoke test blocked because broker account is not signed.")
            result: BrokerOrderResult = broker.place_buy_order(
                item.stock_id,
                price,
                qty,
                "intraday_odd_lot",
                {"note": "live_smoke_test", "stock_name": item.stock_name},
            )
            orders.append(SmokeTestOrder(item.stock_id, item.stock_name, item.source, qty, price, result.status, result.detail))
        else:
            orders.append(SmokeTestOrder(item.stock_id, item.stock_name, item.source, qty, price, "dry_run"))

    return SmokeTestResult(mode=mode, total_estimated_amount=total_estimated_amount, orders=orders)
