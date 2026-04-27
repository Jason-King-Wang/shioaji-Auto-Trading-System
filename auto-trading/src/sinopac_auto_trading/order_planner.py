from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from .prediction_loader import PredictionSignal

SUPPORTED_ORDER_LOTS = {"Common", "IntradayOdd"}


def _round_price(value: float) -> float:
    return round(value, 2)


def _derive_limit_price(side: str, reference_price: float, buffer_pct: float) -> float:
    buffer_ratio = buffer_pct / 100.0
    if side == "Sell":
        return _round_price(reference_price * (1.0 - buffer_ratio))
    return _round_price(reference_price * (1.0 + buffer_ratio))


def _quantity_for_budget(limit_price: float, budget_per_order: float, order_lot: str) -> int:
    if order_lot == "Common":
        unit_cost = limit_price * 1000
    else:
        unit_cost = limit_price
    if unit_cost <= 0:
        return 0
    return int(budget_per_order // unit_cost)


@dataclass(slots=True)
class PlannedOrder:
    plan_rank: int
    stock_id: str
    stock_name: str
    exchange_hint: str
    side: str
    order_lot: str
    quantity: int
    reference_price: float
    limit_price: float
    budget_twd: float
    confidence: float | None
    model_rank: int | None
    stage_1_price: float | None
    stage_2_price: float | None
    target_price: float | None
    source_csv: str
    note: str


def build_order_plan(
    signals: list[PredictionSignal],
    *,
    budget_per_order: float,
    price_buffer_pct: float,
    max_orders: int,
    order_lot: str,
    source_csv: Path,
) -> list[PlannedOrder]:
    if order_lot not in SUPPORTED_ORDER_LOTS:
        raise RuntimeError(f"Unsupported order lot: {order_lot}")

    ranked_signals = sorted(
        signals,
        key=lambda item: (
            item.model_rank is None,
            item.model_rank if item.model_rank is not None else 999999,
            item.stock_id,
        ),
    )

    plan: list[PlannedOrder] = []
    for signal in ranked_signals:
        if len(plan) >= max_orders:
            break

        limit_price = _derive_limit_price(signal.side, signal.reference_price, price_buffer_pct)
        quantity = _quantity_for_budget(limit_price, budget_per_order, order_lot)
        if quantity <= 0:
            continue

        plan.append(
            PlannedOrder(
                plan_rank=len(plan) + 1,
                stock_id=signal.stock_id,
                stock_name=signal.stock_name,
                exchange_hint=signal.exchange_hint,
                side=signal.side,
                order_lot=order_lot,
                quantity=quantity,
                reference_price=signal.reference_price,
                limit_price=limit_price,
                budget_twd=budget_per_order,
                confidence=signal.confidence,
                model_rank=signal.model_rank,
                stage_1_price=signal.stage_1_price,
                stage_2_price=signal.stage_2_price,
                target_price=signal.target_price,
                source_csv=str(source_csv),
                note=signal.note,
            )
        )

    return plan


def write_order_plan_csv(plan: list[PlannedOrder], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "plan_rank",
        "stock_id",
        "stock_name",
        "exchange_hint",
        "side",
        "order_lot",
        "quantity",
        "reference_price",
        "limit_price",
        "budget_twd",
        "confidence",
        "model_rank",
        "stage_1_price",
        "stage_2_price",
        "target_price",
        "source_csv",
        "note",
    ]
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in plan:
            writer.writerow(
                {
                    "plan_rank": item.plan_rank,
                    "stock_id": item.stock_id,
                    "stock_name": item.stock_name,
                    "exchange_hint": item.exchange_hint,
                    "side": item.side,
                    "order_lot": item.order_lot,
                    "quantity": item.quantity,
                    "reference_price": item.reference_price,
                    "limit_price": item.limit_price,
                    "budget_twd": item.budget_twd,
                    "confidence": item.confidence,
                    "model_rank": item.model_rank,
                    "stage_1_price": item.stage_1_price,
                    "stage_2_price": item.stage_2_price,
                    "target_price": item.target_price,
                    "source_csv": item.source_csv,
                    "note": item.note,
                }
            )


def read_order_plan_csv(plan_path: Path) -> list[PlannedOrder]:
    plan: list[PlannedOrder] = []
    with plan_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            plan.append(
                PlannedOrder(
                    plan_rank=int(row["plan_rank"]),
                    stock_id=row["stock_id"],
                    stock_name=row["stock_name"],
                    exchange_hint=row.get("exchange_hint", ""),
                    side=row["side"],
                    order_lot=row["order_lot"],
                    quantity=int(row["quantity"]),
                    reference_price=float(row["reference_price"]),
                    limit_price=float(row["limit_price"]),
                    budget_twd=float(row["budget_twd"]),
                    confidence=float(row["confidence"]) if row.get("confidence") else None,
                    model_rank=int(row["model_rank"]) if row.get("model_rank") else None,
                    stage_1_price=float(row["stage_1_price"]) if row.get("stage_1_price") else None,
                    stage_2_price=float(row["stage_2_price"]) if row.get("stage_2_price") else None,
                    target_price=float(row["target_price"]) if row.get("target_price") else None,
                    source_csv=row.get("source_csv", ""),
                    note=row.get("note", ""),
                )
            )
    return plan
