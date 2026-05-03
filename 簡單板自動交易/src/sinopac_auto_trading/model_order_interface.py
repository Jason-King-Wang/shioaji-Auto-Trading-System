from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import FeeConfig, Settings
from .paths import DATA_DIR
from .quick_simulator import (
    SIMPLE_BROKER_NAME,
    SellSimulationResult,
    SimulatedBuyOrder,
    SimulatedSellOrder,
    StockBuyRequest,
    StockSellRequest,
    load_quote_prices,
    normalize_order_lot,
    resolve_request_prices,
    resolve_sell_request_prices,
    simulate_buy_orders,
    simulate_sell_orders,
)
from .risk_controls import estimate_buy_order_cost
from .time_utils import TAIPEI


@dataclass(slots=True)
class ModelOrderIntent:
    action: str
    stock_id: str
    price: float | None = None
    budget: float | None = None
    quantity: int | None = None
    weight: float = 1.0
    lot: str = "odd"
    stock_name: str = ""
    signal_id: str = ""
    note: str = ""


@dataclass(slots=True)
class ModelOrderBatch:
    source_model: str
    intents: list[ModelOrderIntent]
    default_lot: str = "odd"
    buy_budget: float | None = None
    quote_file: str = ""


@dataclass(slots=True)
class ModelOrderBatchResult:
    created_at: str
    broker: str
    mode: str
    source_model: str
    order_count: int
    projected_buy_cost: float
    projected_sell_gross_amount: float
    projected_sell_net_amount: float
    orders: list[dict[str, object]]
    output_csv: str = ""


def load_model_order_batch(path: Path) -> ModelOrderBatch:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _load_csv_batch(path)
    return _load_json_batch(path)


def process_model_order_batch(
    batch: ModelOrderBatch,
    *,
    fees: FeeConfig,
    quote_file: Path,
    buffer_multiplier: float,
    output_path: Path | None = None,
    write_output: bool = True,
) -> ModelOrderBatchResult:
    if not batch.intents:
        raise ValueError("model order batch must contain at least one order intent.")

    quote_prices = load_quote_prices(quote_file)
    orders: list[dict[str, object]] = []
    fixed_buy_intents: list[ModelOrderIntent] = []
    budget_buy_intents: list[ModelOrderIntent] = []
    shared_budget_buy_intents: list[ModelOrderIntent] = []
    sell_intents: list[ModelOrderIntent] = []

    for intent in batch.intents:
        action = _normalize_action(intent.action)
        if action == "buy":
            if intent.quantity is not None:
                fixed_buy_intents.append(intent)
            elif intent.budget is not None:
                budget_buy_intents.append(intent)
            else:
                shared_budget_buy_intents.append(intent)
            continue
        if action == "sell":
            sell_intents.append(intent)
            continue
        raise ValueError(f"unsupported action for {intent.stock_id}: {intent.action!r}")

    for intent in budget_buy_intents:
        result = _simulate_budget_buy_intents(
            [intent],
            buy_budget=float(intent.budget or 0),
            default_lot=batch.default_lot,
            quote_prices=quote_prices,
            fees=fees,
            buffer_multiplier=buffer_multiplier,
        )
        orders.extend(_buy_result_rows(result, [intent], len(orders)))

    if shared_budget_buy_intents:
        if batch.buy_budget is None or batch.buy_budget <= 0:
            stock_ids = ", ".join(intent.stock_id for intent in shared_budget_buy_intents)
            raise ValueError(f"buy_budget is required for buy intents without budget or quantity: {stock_ids}.")
        result = _simulate_budget_buy_intents(
            shared_budget_buy_intents,
            buy_budget=float(batch.buy_budget),
            default_lot=batch.default_lot,
            quote_prices=quote_prices,
            fees=fees,
            buffer_multiplier=buffer_multiplier,
        )
        orders.extend(_buy_result_rows(result, shared_budget_buy_intents, len(orders)))

    for intent in fixed_buy_intents:
        order = _simulate_fixed_buy_intent(
            intent,
            default_lot=batch.default_lot,
            quote_prices=quote_prices,
            fees=fees,
            buffer_multiplier=buffer_multiplier,
        )
        orders.append(_buy_order_row(order, intent, len(orders) + 1))

    for intent in sell_intents:
        result = _simulate_sell_intents(
            [intent],
            default_lot=batch.default_lot,
            quote_prices=quote_prices,
            fees=fees,
        )
        orders.extend(_sell_result_rows(result, [intent], len(orders)))

    for row in orders:
        row["source_model"] = batch.source_model

    result = ModelOrderBatchResult(
        created_at=datetime.now(TAIPEI).isoformat(timespec="seconds"),
        broker=SIMPLE_BROKER_NAME,
        mode="simulation_only",
        source_model=batch.source_model,
        order_count=len(orders),
        projected_buy_cost=sum(_float(row.get("estimated_total_cost")) for row in orders if row.get("side") == "Buy"),
        projected_sell_gross_amount=sum(_float(row.get("gross_amount")) for row in orders if row.get("side") == "Sell"),
        projected_sell_net_amount=sum(_float(row.get("estimated_net_amount")) for row in orders if row.get("side") == "Sell"),
        orders=orders,
    )
    if write_output:
        write_model_order_result_csv(result, output_path)
    return result


def write_model_order_result_csv(result: ModelOrderBatchResult, path: Path | None = None) -> Path:
    resolved_path = path or _default_output_path(result.created_at)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "source_model",
        "signal_id",
        "broker",
        "mode",
        "submitted_to_broker",
        "side",
        "stock_id",
        "stock_name",
        "limit_price",
        "quantity",
        "order_lot",
        "allocated_budget",
        "gross_amount",
        "estimated_fee",
        "estimated_tax",
        "cost_buffer",
        "estimated_total_cost",
        "estimated_net_amount",
        "unused_allocated_budget",
        "status",
        "order_id",
        "note",
        "quote_timestamp",
    ]
    with resolved_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in result.orders:
            writer.writerow({field: row.get(field, "") for field in fieldnames})
    result.output_csv = str(resolved_path)
    return resolved_path


def command_model_orders(args) -> int:
    settings = Settings.load()
    batch = load_model_order_batch(Path(args.file))
    if args.buy_budget is not None:
        batch.buy_budget = float(args.buy_budget)
    if args.source_model:
        batch.source_model = args.source_model
    if args.quote_file:
        batch.quote_file = args.quote_file

    quote_file = (
        Path(batch.quote_file)
        if batch.quote_file
        else settings.project_root / "examples" / "fake_quotes_example.csv"
    )
    buffer_multiplier = (
        float(args.buffer_multiplier)
        if args.buffer_multiplier is not None
        else settings.auto_trading.cost_buffer_multiplier
    )
    result = process_model_order_batch(
        batch,
        fees=settings.fees,
        quote_file=quote_file,
        buffer_multiplier=buffer_multiplier,
        output_path=Path(args.output) if args.output else None,
        write_output=not args.no_write,
    )
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return 0

    _print_model_order_result(result)
    return 0


def _load_json_batch(path: Path) -> ModelOrderBatch:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return ModelOrderBatch(
            source_model=path.stem,
            intents=[_intent_from_mapping(item) for item in payload],
        )
    if not isinstance(payload, dict):
        raise ValueError("model order JSON must be an object or a list.")
    raw_orders = payload.get("orders", payload.get("order_intents"))
    if not isinstance(raw_orders, list):
        raise ValueError("model order JSON must contain an orders list.")
    return ModelOrderBatch(
        source_model=str(payload.get("source_model", payload.get("model", path.stem)) or path.stem),
        intents=[_intent_from_mapping(item) for item in raw_orders],
        default_lot=str(payload.get("default_lot", payload.get("lot", "odd")) or "odd"),
        buy_budget=_optional_float(payload.get("buy_budget", payload.get("budget"))),
        quote_file=str(payload.get("quote_file", "") or ""),
    )


def _load_csv_batch(path: Path) -> ModelOrderBatch:
    intents: list[ModelOrderIntent] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            intents.append(_intent_from_mapping(row))
    return ModelOrderBatch(source_model=path.stem, intents=intents)


def _intent_from_mapping(raw: Any) -> ModelOrderIntent:
    if not isinstance(raw, dict):
        raise ValueError("each order intent must be an object.")
    stock_id = str(raw.get("stock_id", raw.get("stock", raw.get("code", ""))) or "").strip()
    if not stock_id:
        raise ValueError("order intent missing stock_id.")
    return ModelOrderIntent(
        action=_normalize_action(str(raw.get("action", raw.get("side", "")) or "")),
        stock_id=stock_id,
        price=_optional_float(raw.get("price", raw.get("limit_price", raw.get("reference_price")))),
        budget=_optional_float(raw.get("budget", raw.get("target_budget"))),
        quantity=_optional_int(raw.get("quantity", raw.get("qty", raw.get("target_qty")))),
        weight=_optional_float(raw.get("weight", raw.get("target_weight"))) or 1.0,
        lot=str(raw.get("lot", raw.get("order_lot", "")) or "").strip(),
        stock_name=str(raw.get("stock_name", raw.get("name", "")) or "").strip(),
        signal_id=str(raw.get("signal_id", raw.get("id", "")) or "").strip(),
        note=str(raw.get("note", "") or "").strip(),
    )


def _simulate_budget_buy_intents(
    intents: list[ModelOrderIntent],
    *,
    buy_budget: float,
    default_lot: str,
    quote_prices: dict[str, StockBuyRequest],
    fees: FeeConfig,
    buffer_multiplier: float,
):
    requests = [
        StockBuyRequest(
            stock_id=intent.stock_id,
            stock_name=intent.stock_name,
            limit_price=intent.price,
            weight=intent.weight,
        )
        for intent in intents
    ]
    resolved = resolve_request_prices(requests, quote_prices)
    lot = _resolve_lot(intents[0].lot, default_lot)
    return simulate_buy_orders(
        resolved,
        budget=buy_budget,
        fees=fees,
        order_lot=lot,
        buffer_multiplier=buffer_multiplier,
    )


def _simulate_fixed_buy_intent(
    intent: ModelOrderIntent,
    *,
    default_lot: str,
    quote_prices: dict[str, StockBuyRequest],
    fees: FeeConfig,
    buffer_multiplier: float,
) -> SimulatedBuyOrder:
    resolved = resolve_request_prices(
        [
            StockBuyRequest(
                stock_id=intent.stock_id,
                stock_name=intent.stock_name,
                limit_price=intent.price,
                weight=intent.weight,
            )
        ],
        quote_prices,
    )[0]
    quantity = int(intent.quantity or 0)
    if quantity <= 0:
        raise ValueError(f"buy quantity must be greater than 0 for {intent.stock_id}.")
    lot = normalize_order_lot(_resolve_lot(intent.lot, default_lot))
    if lot == "common" and quantity % 1000 != 0:
        raise ValueError("common lot buy quantity must be a multiple of 1000.")
    gross = float(resolved.limit_price or 0) * quantity
    fee = fees.estimate_buy_fee(gross) if gross > 0 else 0.0
    estimated_total_cost = estimate_buy_order_cost(
        float(resolved.limit_price or 0),
        quantity,
        fees=fees,
        buffer_multiplier=buffer_multiplier,
    )
    return SimulatedBuyOrder(
        broker=SIMPLE_BROKER_NAME,
        stock_id=resolved.stock_id,
        stock_name=resolved.stock_name,
        side="Buy",
        limit_price=float(resolved.limit_price or 0),
        weight=intent.weight,
        allocated_budget=estimated_total_cost,
        quantity=quantity,
        order_lot=lot,
        gross_amount=gross,
        estimated_fee=fee,
        cost_buffer=max(estimated_total_cost - gross - fee, 0.0),
        estimated_total_cost=estimated_total_cost,
        unused_allocated_budget=0.0,
        status="dry_run",
        order_id="",
        submitted_to_broker=False,
        note="model_fixed_quantity_simulated",
        quote_timestamp=resolved.quote_timestamp,
    )


def _simulate_sell_intents(
    intents: list[ModelOrderIntent],
    *,
    default_lot: str,
    quote_prices: dict[str, StockBuyRequest],
    fees: FeeConfig,
) -> SellSimulationResult:
    requests = [
        StockSellRequest(
            stock_id=intent.stock_id,
            stock_name=intent.stock_name,
            limit_price=intent.price,
            quantity=int(intent.quantity or 0),
        )
        for intent in intents
    ]
    resolved = resolve_sell_request_prices(requests, quote_prices)
    lot = _resolve_lot(intents[0].lot, default_lot)
    return simulate_sell_orders(resolved, fees=fees, order_lot=lot)


def _buy_result_rows(result, intents: list[ModelOrderIntent], offset: int) -> list[dict[str, object]]:
    return [
        _buy_order_row(order, intents[index], offset + index + 1)
        for index, order in enumerate(result.orders)
    ]


def _sell_result_rows(result: SellSimulationResult, intents: list[ModelOrderIntent], offset: int) -> list[dict[str, object]]:
    return [
        _sell_order_row(order, intents[index], offset + index + 1)
        for index, order in enumerate(result.orders)
    ]


def _buy_order_row(order: SimulatedBuyOrder, intent: ModelOrderIntent, sequence: int) -> dict[str, object]:
    return {
        "source_model": "",
        "signal_id": intent.signal_id,
        "broker": SIMPLE_BROKER_NAME,
        "mode": "simulation_only",
        "submitted_to_broker": False,
        "side": "Buy",
        "stock_id": order.stock_id,
        "stock_name": order.stock_name,
        "limit_price": order.limit_price,
        "quantity": order.quantity,
        "order_lot": order.order_lot,
        "allocated_budget": order.allocated_budget,
        "gross_amount": order.gross_amount,
        "estimated_fee": order.estimated_fee,
        "estimated_tax": "",
        "cost_buffer": order.cost_buffer,
        "estimated_total_cost": order.estimated_total_cost,
        "estimated_net_amount": "",
        "unused_allocated_budget": order.unused_allocated_budget,
        "status": order.status,
        "order_id": f"DRY-{sequence:04d}" if order.quantity > 0 else "",
        "note": intent.note or order.note,
        "quote_timestamp": order.quote_timestamp,
    }


def _sell_order_row(order: SimulatedSellOrder, intent: ModelOrderIntent, sequence: int) -> dict[str, object]:
    return {
        "source_model": "",
        "signal_id": intent.signal_id,
        "broker": SIMPLE_BROKER_NAME,
        "mode": "simulation_only",
        "submitted_to_broker": False,
        "side": "Sell",
        "stock_id": order.stock_id,
        "stock_name": order.stock_name,
        "limit_price": order.limit_price,
        "quantity": order.quantity,
        "order_lot": order.order_lot,
        "allocated_budget": "",
        "gross_amount": order.gross_amount,
        "estimated_fee": order.estimated_fee,
        "estimated_tax": order.estimated_tax,
        "cost_buffer": "",
        "estimated_total_cost": "",
        "estimated_net_amount": order.estimated_net_amount,
        "unused_allocated_budget": "",
        "status": order.status,
        "order_id": f"DRY-{sequence:04d}",
        "note": intent.note or order.note,
        "quote_timestamp": order.quote_timestamp,
    }


def _print_model_order_result(result: ModelOrderBatchResult) -> None:
    print(f"broker: {result.broker}")
    print(f"mode: {result.mode}")
    print("submitted_to_broker: false")
    print(f"source_model: {result.source_model}")
    print(f"order_count: {result.order_count}")
    print(f"projected_buy_cost: {result.projected_buy_cost:.2f}")
    print(f"projected_sell_gross_amount: {result.projected_sell_gross_amount:.2f}")
    print(f"projected_sell_net_amount: {result.projected_sell_net_amount:.2f}")
    if result.output_csv:
        print(f"output_csv: {result.output_csv}")
    print("")
    print(_format_model_orders_table(result.orders))


def _format_model_orders_table(orders: list[dict[str, object]]) -> str:
    headers = ["side", "stock", "price", "qty", "cost_or_net", "status", "order_id", "signal_id"]
    rows = []
    for order in orders:
        side = str(order.get("side", ""))
        value = order.get("estimated_total_cost") if side == "Buy" else order.get("estimated_net_amount")
        rows.append(
            [
                side,
                str(order.get("stock_id", "")),
                f"{_float(order.get('limit_price')):.2f}",
                str(order.get("quantity", "")),
                f"{_float(value):.2f}",
                str(order.get("status", "")),
                str(order.get("order_id", "")),
                str(order.get("signal_id", "")),
            ]
        )
    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))

    def render(row: list[str]) -> str:
        return "  ".join(value.rjust(widths[index]) for index, value in enumerate(row))

    lines = [render(headers), render(["-" * width for width in widths])]
    lines.extend(render(row) for row in rows)
    return "\n".join(lines)


def _normalize_action(raw: str) -> str:
    value = str(raw or "").strip().lower()
    if value in {"buy", "b", "long"}:
        return "buy"
    if value in {"sell", "s", "exit"}:
        return "sell"
    raise ValueError(f"unsupported action: {raw!r}")


def _resolve_lot(raw: str, default_lot: str) -> str:
    return raw.strip() if raw and raw.strip() else default_lot


def _optional_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    return int(float(value))


def _float(value: object) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _default_output_path(created_at: str) -> Path:
    safe_timestamp = created_at.replace(":", "").replace("+", "_").replace("-", "")
    return DATA_DIR / "simulations" / f"{safe_timestamp}_model_orders.csv"
