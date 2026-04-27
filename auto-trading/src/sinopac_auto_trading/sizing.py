from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from .basket import DEFAULT_BASKET_TAG, normalize_basket_tag
from .config import AutoTradingConfig, FeeConfig
from .selection_provider import SelectionItem


@dataclass(slots=True)
class SizedSelection:
    item: SelectionItem
    estimated_buy_price: float
    source_weight: float
    target_qty: int
    projected_cost: float


@dataclass(slots=True)
class SizingResult:
    rows: list[SizedSelection]
    projected_total_cost: float
    weekly_budget: float
    hard_budget: float
    usable_budget: float


def _projected_cost(price: float, qty: int, fees: FeeConfig, *, buffer_multiplier: float) -> float:
    gross = price * qty
    fee = fees.estimate_buy_fee(gross)
    return (gross + fee) * buffer_multiplier


def _base_qty(
    items: list[SelectionItem],
    estimated_prices: dict[str, float],
    usable_budget: float,
    auto: AutoTradingConfig,
) -> int:
    weighted_total = 0.0
    for item in items:
        if item.target_qty is not None:
            continue
        price = estimated_prices[item.stock_id]
        weighted_total += price * item.normalized_source_weight() * auto.cost_buffer_multiplier
    if weighted_total <= 0:
        return 0
    return max(1, math.floor(usable_budget / weighted_total))


def _resolve_qty(item: SelectionItem, base_qty: int, auto: AutoTradingConfig, *, smoke_test: bool) -> int:
    if item.target_qty is not None:
        return int(item.target_qty)
    if smoke_test:
        return auto.test_qty_dual_source if item.normalized_source_weight() >= 2 else auto.test_qty_single_source
    return max(1, int(base_qty * item.normalized_source_weight()))


def _locked_qty_for_item(locked_qty_by_stock: dict[str, int], item: SelectionItem) -> int:
    basket_key = f"{item.stock_id}::{item.normalized_basket_tag()}"
    return max(int(locked_qty_by_stock.get(basket_key, locked_qty_by_stock.get(item.stock_id, 0))), 0)


def _second_trade_day(week_trade_days: list[date] | None) -> date | None:
    if not week_trade_days or len(week_trade_days) < 2:
        return None
    return week_trade_days[1]


def secondary_add_active_for_trade_date(
    *,
    trade_date: date | None,
    week_trade_days: list[date] | None,
    auto: AutoTradingConfig,
    smoke_test: bool,
) -> bool:
    if smoke_test or not auto.enable_secondary_add:
        return False
    second_trade_day = _second_trade_day(week_trade_days)
    if trade_date is None or second_trade_day is None:
        return auto.enable_secondary_add
    return trade_date == second_trade_day


def _secondary_add_budget_ratio(items: list[SelectionItem], auto: AutoTradingConfig) -> float:
    total_weight = sum(item.normalized_source_weight() for item in items)
    if total_weight <= 0:
        return auto.secondary_add_budget_pct_min
    secondary_weight = sum(
        item.normalized_source_weight()
        for item in items
        if item.normalized_basket_tag() == "secondary_add"
    )
    raw_ratio = secondary_weight / total_weight
    return min(max(raw_ratio, auto.secondary_add_budget_pct_min), auto.secondary_add_budget_pct_max)


def _size_bucket(
    indexed_items: list[tuple[int, SelectionItem]],
    estimated_prices: dict[str, float],
    usable_budget: float,
    auto: AutoTradingConfig,
    fees: FeeConfig,
    *,
    smoke_test: bool,
    locked_qty_by_stock: dict[str, int],
) -> list[tuple[int, SizedSelection]]:
    items = [item for _, item in indexed_items]
    base_qty = _base_qty(items, estimated_prices, usable_budget, auto)

    def build_rows(candidate_base_qty: int) -> list[tuple[int, SizedSelection]]:
        rows: list[tuple[int, SizedSelection]] = []
        for index, item in indexed_items:
            price = estimated_prices[item.stock_id]
            qty = _resolve_qty(item, candidate_base_qty, auto, smoke_test=smoke_test)
            rows.append(
                (
                    index,
                    SizedSelection(
                        item=item,
                        estimated_buy_price=price,
                        source_weight=item.normalized_source_weight(),
                        target_qty=qty,
                        projected_cost=_projected_cost(price, qty, fees, buffer_multiplier=auto.cost_buffer_multiplier),
                    ),
                )
            )
        return rows

    rows = build_rows(base_qty)

    def total_cost() -> float:
        return sum(row.projected_cost for _, row in rows)

    while base_qty > 0 and rows and total_cost() > usable_budget:
        if not any(row.item.target_qty is None for _, row in rows):
            break
        base_qty -= 1
        rows = build_rows(base_qty)

    while rows and total_cost() > usable_budget:
        reducible = [
            row
            for row in rows
            if row[1].item.target_qty is None and row[1].target_qty > _locked_qty_for_item(locked_qty_by_stock, row[1].item)
        ]
        if not reducible:
            break
        target_index, target = max(reducible, key=lambda row: row[1].projected_cost)
        target.target_qty -= 1
        target.projected_cost = _projected_cost(
            target.estimated_buy_price,
            target.target_qty,
            fees,
            buffer_multiplier=auto.cost_buffer_multiplier,
        )

    return rows


def _zero_bucket(indexed_items: list[tuple[int, SelectionItem]], estimated_prices: dict[str, float]) -> list[tuple[int, SizedSelection]]:
    rows: list[tuple[int, SizedSelection]] = []
    for index, item in indexed_items:
        rows.append(
            (
                index,
                SizedSelection(
                    item=item,
                    estimated_buy_price=estimated_prices[item.stock_id],
                    source_weight=item.normalized_source_weight(),
                    target_qty=0,
                    projected_cost=0.0,
                ),
            )
        )
    return rows


def size_selection(
    items: list[SelectionItem],
    estimated_prices: dict[str, float],
    *,
    auto: AutoTradingConfig,
    fees: FeeConfig,
    cash_available: float | None = None,
    smoke_test: bool = False,
    locked_qty_by_stock: dict[str, int] | None = None,
    trade_date: date | None = None,
    week_trade_days: list[date] | None = None,
) -> SizingResult:
    locked_qty_by_stock = locked_qty_by_stock or {}
    hard_budget = auto.hard_budget
    usable_budget = min(hard_budget, cash_available) if cash_available is not None else hard_budget
    secondary_active = secondary_add_active_for_trade_date(
        trade_date=trade_date,
        week_trade_days=week_trade_days,
        auto=auto,
        smoke_test=smoke_test,
    )
    indexed_items = list(enumerate(items))
    main_bucket = [
        (index, item)
        for index, item in indexed_items
        if item.normalized_basket_tag() != "secondary_add"
    ]
    secondary_bucket = [
        (index, item)
        for index, item in indexed_items
        if item.normalized_basket_tag() == "secondary_add"
    ]
    rows_by_index: dict[int, SizedSelection] = {}

    if secondary_bucket and secondary_active:
        if main_bucket:
            secondary_ratio = _secondary_add_budget_ratio(items, auto)
            secondary_budget = usable_budget * secondary_ratio
            main_budget = max(usable_budget - secondary_budget, 0.0)
        else:
            secondary_budget = usable_budget
            main_budget = 0.0
        sized_groups = _size_bucket(
            main_bucket,
            estimated_prices,
            main_budget,
            auto,
            fees,
            smoke_test=smoke_test,
            locked_qty_by_stock=locked_qty_by_stock,
        )
        sized_groups.extend(
            _size_bucket(
                secondary_bucket,
                estimated_prices,
                secondary_budget,
                auto,
                fees,
                smoke_test=smoke_test,
                locked_qty_by_stock=locked_qty_by_stock,
            )
        )
    else:
        sized_groups = _size_bucket(
            main_bucket,
            estimated_prices,
            usable_budget,
            auto,
            fees,
            smoke_test=smoke_test,
            locked_qty_by_stock=locked_qty_by_stock,
        )
        if secondary_bucket:
            sized_groups.extend(_zero_bucket(secondary_bucket, estimated_prices))

    for index, row in sized_groups:
        rows_by_index[index] = row
    rows = [rows_by_index[index] for index in sorted(rows_by_index)]

    def total_cost() -> float:
        return sum(row.projected_cost for row in rows)

    return SizingResult(
        rows=rows,
        projected_total_cost=total_cost(),
        weekly_budget=auto.weekly_budget,
        hard_budget=hard_budget,
        usable_budget=usable_budget,
    )
