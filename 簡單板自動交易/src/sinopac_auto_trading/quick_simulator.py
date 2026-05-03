from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .broker_adapter import FakeBrokerAdapter
from .config import FeeConfig, Settings
from .paths import DATA_DIR
from .risk_controls import affordable_buy_qty, estimate_buy_order_cost
from .time_utils import TAIPEI

SIMPLE_BROKER_NAME = "sinopac"


@dataclass(slots=True)
class StockBuyRequest:
    stock_id: str
    limit_price: float | None = None
    weight: float = 1.0
    stock_name: str = ""
    quote_timestamp: str = ""


@dataclass(slots=True)
class SimulatedBuyOrder:
    broker: str
    stock_id: str
    stock_name: str
    side: str
    limit_price: float
    weight: float
    allocated_budget: float
    quantity: int
    order_lot: str
    gross_amount: float
    estimated_fee: float
    cost_buffer: float
    estimated_total_cost: float
    unused_allocated_budget: float
    status: str
    order_id: str
    submitted_to_broker: bool
    note: str
    quote_timestamp: str = ""


@dataclass(slots=True)
class SimulationResult:
    created_at: str
    broker: str
    mode: str
    budget: float
    order_lot: str
    buffer_multiplier: float
    projected_total_cost: float
    estimated_remaining_cash: float
    orders: list[SimulatedBuyOrder]
    output_csv: str = ""


@dataclass(slots=True)
class StockSellRequest:
    stock_id: str
    quantity: int
    limit_price: float | None = None
    stock_name: str = ""
    quote_timestamp: str = ""


@dataclass(slots=True)
class SimulatedSellOrder:
    broker: str
    stock_id: str
    stock_name: str
    side: str
    limit_price: float
    quantity: int
    order_lot: str
    gross_amount: float
    estimated_fee: float
    estimated_tax: float
    estimated_net_amount: float
    status: str
    order_id: str
    submitted_to_broker: bool
    note: str
    quote_timestamp: str = ""


@dataclass(slots=True)
class SellSimulationResult:
    created_at: str
    broker: str
    mode: str
    order_lot: str
    projected_gross_amount: float
    projected_fee: float
    projected_tax: float
    projected_net_amount: float
    orders: list[SimulatedSellOrder]
    output_csv: str = ""


def normalize_order_lot(raw: str) -> str:
    value = str(raw or "").strip().lower().replace("-", "_")
    if value in {"odd", "odd_lot", "intradayodd", "intraday_odd", "intraday_odd_lot"}:
        return "intraday_odd_lot"
    if value in {"common", "regular", "board_lot", "lot"}:
        return "common"
    raise ValueError("order lot must be odd or common.")


def parse_stock_buy_request(raw: str) -> StockBuyRequest:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("stock spec cannot be empty.")

    for separator in (":", "@", "="):
        if separator in text:
            parts = [part.strip() for part in text.split(separator)]
            break
    else:
        parts = [text]

    if len(parts) > 3 or not parts[0]:
        raise ValueError(f"invalid stock spec: {raw!r}")

    stock_id = parts[0]
    limit_price: float | None = None
    weight = 1.0

    if len(parts) >= 2 and parts[1]:
        limit_price = float(parts[1])
        if limit_price <= 0:
            raise ValueError(f"price must be greater than 0 for {stock_id}.")

    if len(parts) == 3 and parts[2]:
        weight = float(parts[2])
        if weight <= 0:
            raise ValueError(f"weight must be greater than 0 for {stock_id}.")

    return StockBuyRequest(stock_id=stock_id, limit_price=limit_price, weight=weight)


def parse_stock_sell_request(raw: str, *, quantity: int | None = None) -> StockSellRequest:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("stock spec cannot be empty.")

    for separator in (":", "@", "="):
        if separator in text:
            parts = [part.strip() for part in text.split(separator)]
            break
    else:
        parts = [text]

    if len(parts) > 3 or not parts[0]:
        raise ValueError(f"invalid stock spec: {raw!r}")

    stock_id = parts[0]
    limit_price: float | None = None
    resolved_quantity = quantity
    if len(parts) >= 2 and parts[1]:
        limit_price = float(parts[1])
        if limit_price <= 0:
            raise ValueError(f"price must be greater than 0 for {stock_id}.")
    if len(parts) == 3 and parts[2]:
        resolved_quantity = int(float(parts[2]))

    if resolved_quantity is None or resolved_quantity <= 0:
        raise ValueError(f"quantity must be greater than 0 for {stock_id}.")

    return StockSellRequest(stock_id=stock_id, limit_price=limit_price, quantity=resolved_quantity)


def load_quote_prices(path: Path) -> dict[str, StockBuyRequest]:
    if not path.exists():
        return {}

    quotes: dict[str, StockBuyRequest] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            stock_id = str(row.get("stock_id", "") or row.get("code", "")).strip()
            if not stock_id:
                continue
            price = _first_positive_float(
                row,
                ("last_price", "price", "reference_price", "close", "ask1", "bid1"),
            )
            if price is None:
                continue
            timestamp = str(row.get("timestamp", "") or "").strip()
            existing = quotes.get(stock_id)
            if existing is not None and timestamp and existing.quote_timestamp:
                if timestamp < existing.quote_timestamp:
                    continue
            quotes[stock_id] = StockBuyRequest(
                stock_id=stock_id,
                stock_name=str(row.get("stock_name", "") or row.get("name", "")).strip(),
                limit_price=price,
                quote_timestamp=timestamp,
            )
    return quotes


def resolve_request_prices(
    requests: list[StockBuyRequest],
    quote_prices: dict[str, StockBuyRequest],
) -> list[StockBuyRequest]:
    resolved: list[StockBuyRequest] = []
    missing: list[str] = []
    for request in requests:
        if request.limit_price is not None:
            quote = quote_prices.get(request.stock_id)
            resolved.append(
                StockBuyRequest(
                    stock_id=request.stock_id,
                    stock_name=request.stock_name or (quote.stock_name if quote else ""),
                    limit_price=request.limit_price,
                    weight=request.weight,
                    quote_timestamp=request.quote_timestamp or (quote.quote_timestamp if quote else ""),
                )
            )
            continue

        quote = quote_prices.get(request.stock_id)
        if quote is None or quote.limit_price is None:
            missing.append(request.stock_id)
            continue
        resolved.append(
            StockBuyRequest(
                stock_id=request.stock_id,
                stock_name=request.stock_name or quote.stock_name,
                limit_price=quote.limit_price,
                weight=request.weight,
                quote_timestamp=quote.quote_timestamp,
            )
        )

    if missing:
        shown = ", ".join(missing)
        raise ValueError(
            "missing price for stock(s): "
            f"{shown}. Use --stock CODE:PRICE or provide --quote-file with stock_id,last_price."
        )
    return resolved


def resolve_sell_request_prices(
    requests: list[StockSellRequest],
    quote_prices: dict[str, StockBuyRequest],
) -> list[StockSellRequest]:
    resolved: list[StockSellRequest] = []
    missing: list[str] = []
    for request in requests:
        if request.limit_price is not None:
            quote = quote_prices.get(request.stock_id)
            resolved.append(
                StockSellRequest(
                    stock_id=request.stock_id,
                    stock_name=request.stock_name or (quote.stock_name if quote else ""),
                    limit_price=request.limit_price,
                    quantity=request.quantity,
                    quote_timestamp=request.quote_timestamp or (quote.quote_timestamp if quote else ""),
                )
            )
            continue

        quote = quote_prices.get(request.stock_id)
        if quote is None or quote.limit_price is None:
            missing.append(request.stock_id)
            continue
        resolved.append(
            StockSellRequest(
                stock_id=request.stock_id,
                stock_name=request.stock_name or quote.stock_name,
                limit_price=quote.limit_price,
                quantity=request.quantity,
                quote_timestamp=quote.quote_timestamp,
            )
        )

    if missing:
        shown = ", ".join(missing)
        raise ValueError(
            "missing price for stock(s): "
            f"{shown}. Use --stock CODE:PRICE or provide --quote-file with stock_id,last_price."
        )
    return resolved


def simulate_buy_orders(
    requests: list[StockBuyRequest],
    *,
    budget: float,
    fees: FeeConfig | None = None,
    order_lot: str = "intraday_odd_lot",
    buffer_multiplier: float = 1.015,
) -> SimulationResult:
    if budget <= 0:
        raise ValueError("budget must be greater than 0.")
    if not requests:
        raise ValueError("at least one stock is required.")

    resolved_order_lot = normalize_order_lot(order_lot)
    total_weight = sum(request.weight for request in requests)
    if total_weight <= 0:
        raise ValueError("total stock weight must be greater than 0.")

    broker = FakeBrokerAdapter(cash_available=budget)
    orders: list[SimulatedBuyOrder] = []
    for request in requests:
        if request.limit_price is None or request.limit_price <= 0:
            raise ValueError(f"missing valid price for {request.stock_id}.")

        allocation = budget * request.weight / total_weight
        max_requested_qty = max(math.floor(allocation / request.limit_price), 0)
        quantity = affordable_buy_qty(
            requested_qty=max_requested_qty,
            target_price=request.limit_price,
            remaining_budget=allocation,
            order_lot=resolved_order_lot,
            fees=fees,
            buffer_multiplier=buffer_multiplier,
        )
        gross = request.limit_price * quantity
        fee = fees.estimate_buy_fee(gross) if fees is not None and gross > 0 else 0.0
        estimated_total_cost = estimate_buy_order_cost(
            request.limit_price,
            quantity,
            fees=fees,
            buffer_multiplier=buffer_multiplier,
        )
        buffer_amount = max(estimated_total_cost - gross - fee, 0.0)
        note = "simulated_order_created" if quantity > 0 else "budget_too_small_for_one_order_lot"
        order_id = ""
        status = "skipped"
        if quantity > 0:
            result = broker.place_buy_order(
                request.stock_id,
                request.limit_price,
                quantity,
                resolved_order_lot,
                {
                    "mode": "simulation_only",
                    "submitted_to_broker": False,
                    "source": "simulate-buy",
                },
            )
            order_id = result.order_id
            status = result.status

        orders.append(
            SimulatedBuyOrder(
                broker=SIMPLE_BROKER_NAME,
                stock_id=request.stock_id,
                stock_name=request.stock_name,
                side="Buy",
                limit_price=request.limit_price,
                weight=request.weight,
                allocated_budget=allocation,
                quantity=quantity,
                order_lot=resolved_order_lot,
                gross_amount=gross,
                estimated_fee=fee,
                cost_buffer=buffer_amount,
                estimated_total_cost=estimated_total_cost,
                unused_allocated_budget=max(allocation - estimated_total_cost, 0.0),
                status=status,
                order_id=order_id,
                submitted_to_broker=False,
                note=note,
                quote_timestamp=request.quote_timestamp,
            )
        )

    projected_total_cost = sum(order.estimated_total_cost for order in orders)
    return SimulationResult(
        created_at=datetime.now(TAIPEI).isoformat(timespec="seconds"),
        broker=SIMPLE_BROKER_NAME,
        mode="simulation_only",
        budget=budget,
        order_lot=resolved_order_lot,
        buffer_multiplier=buffer_multiplier,
        projected_total_cost=projected_total_cost,
        estimated_remaining_cash=max(budget - projected_total_cost, 0.0),
        orders=orders,
    )


def simulate_sell_orders(
    requests: list[StockSellRequest],
    *,
    fees: FeeConfig | None = None,
    order_lot: str = "intraday_odd_lot",
) -> SellSimulationResult:
    if not requests:
        raise ValueError("at least one stock is required.")

    resolved_order_lot = normalize_order_lot(order_lot)
    broker = FakeBrokerAdapter()
    orders: list[SimulatedSellOrder] = []
    for request in requests:
        if request.limit_price is None or request.limit_price <= 0:
            raise ValueError(f"missing valid price for {request.stock_id}.")
        if request.quantity <= 0:
            raise ValueError(f"quantity must be greater than 0 for {request.stock_id}.")
        if resolved_order_lot == "common" and request.quantity % 1000 != 0:
            raise ValueError("common lot sell quantity must be a multiple of 1000.")

        gross = request.limit_price * request.quantity
        fee = fees.estimate_sell_fee(gross) if fees is not None and gross > 0 else 0.0
        tax = fees.estimate_sell_tax(gross) if fees is not None and gross > 0 else 0.0
        net_amount = max(gross - fee - tax, 0.0)
        result = broker.place_sell_order(
            request.stock_id,
            request.limit_price,
            request.quantity,
            resolved_order_lot,
            {
                "mode": "simulation_only",
                "submitted_to_broker": False,
                "source": "sell",
            },
        )
        orders.append(
            SimulatedSellOrder(
                broker=SIMPLE_BROKER_NAME,
                stock_id=request.stock_id,
                stock_name=request.stock_name,
                side="Sell",
                limit_price=request.limit_price,
                quantity=request.quantity,
                order_lot=resolved_order_lot,
                gross_amount=gross,
                estimated_fee=fee,
                estimated_tax=tax,
                estimated_net_amount=net_amount,
                status=result.status,
                order_id=result.order_id,
                submitted_to_broker=False,
                note="simulated_order_created",
                quote_timestamp=request.quote_timestamp,
            )
        )

    return SellSimulationResult(
        created_at=datetime.now(TAIPEI).isoformat(timespec="seconds"),
        broker=SIMPLE_BROKER_NAME,
        mode="simulation_only",
        order_lot=resolved_order_lot,
        projected_gross_amount=sum(order.gross_amount for order in orders),
        projected_fee=sum(order.estimated_fee for order in orders),
        projected_tax=sum(order.estimated_tax for order in orders),
        projected_net_amount=sum(order.estimated_net_amount for order in orders),
        orders=orders,
    )


def write_simulation_csv(result: SimulationResult, path: Path | None = None) -> Path:
    resolved_path = path or _default_output_path(result.created_at)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(asdict(result.orders[0]).keys()) if result.orders else []
    with resolved_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for order in result.orders:
            writer.writerow(asdict(order))
    result.output_csv = str(resolved_path)
    return resolved_path


def write_sell_simulation_csv(result: SellSimulationResult, path: Path | None = None) -> Path:
    resolved_path = path or _default_output_path(result.created_at, action="sell")
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(asdict(result.orders[0]).keys()) if result.orders else []
    with resolved_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for order in result.orders:
            writer.writerow(asdict(order))
    result.output_csv = str(resolved_path)
    return resolved_path


def command_simulate_buy(args) -> int:
    settings = Settings.load()
    quote_file = Path(args.quote_file) if args.quote_file else settings.project_root / "examples" / "fake_quotes_example.csv"
    requests = [parse_stock_buy_request(raw) for raw in args.stock]
    quote_prices = load_quote_prices(quote_file)
    resolved_requests = resolve_request_prices(requests, quote_prices)
    buffer_multiplier = (
        float(args.buffer_multiplier)
        if args.buffer_multiplier is not None
        else settings.auto_trading.cost_buffer_multiplier
    )
    result = simulate_buy_orders(
        resolved_requests,
        budget=float(args.budget),
        fees=settings.fees,
        order_lot=args.lot,
        buffer_multiplier=buffer_multiplier,
    )
    if not args.no_write:
        write_simulation_csv(result, Path(args.output) if args.output else None)

    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return 0

    _print_buy_result(result)
    return 0


def command_simple_buy(args) -> int:
    settings = Settings.load()
    quote_file = Path(args.quote_file) if args.quote_file else settings.project_root / "examples" / "fake_quotes_example.csv"
    request_text = f"{args.stock}:{args.price}" if args.price is not None else args.stock
    requests = [parse_stock_buy_request(request_text)]
    resolved_requests = resolve_request_prices(requests, load_quote_prices(quote_file))
    buffer_multiplier = (
        float(args.buffer_multiplier)
        if args.buffer_multiplier is not None
        else settings.auto_trading.cost_buffer_multiplier
    )
    result = simulate_buy_orders(
        resolved_requests,
        budget=float(args.budget),
        fees=settings.fees,
        order_lot=args.lot,
        buffer_multiplier=buffer_multiplier,
    )
    if not args.no_write:
        write_simulation_csv(result, Path(args.output) if args.output else None)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return 0
    _print_buy_result(result)
    return 0


def command_simple_sell(args) -> int:
    settings = Settings.load()
    quote_file = Path(args.quote_file) if args.quote_file else settings.project_root / "examples" / "fake_quotes_example.csv"
    request_text = f"{args.stock}:{args.price}" if args.price is not None else args.stock
    requests = [parse_stock_sell_request(request_text, quantity=args.quantity)]
    resolved_requests = resolve_sell_request_prices(requests, load_quote_prices(quote_file))
    result = simulate_sell_orders(
        resolved_requests,
        fees=settings.fees,
        order_lot=args.lot,
    )
    if not args.no_write:
        write_sell_simulation_csv(result, Path(args.output) if args.output else None)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return 0
    _print_sell_result(result)
    return 0


def command_simple_order(args) -> int:
    payload_path = Path(args.file)
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("order JSON must be an object.")

    action = str(payload.get("action", "")).strip().lower()
    output = payload.get("output")
    no_write = bool(args.no_write or payload.get("no_write", False))
    if action == "buy":
        command_args = _ObjectArgs(
            stock=str(payload.get("stock_id") or payload.get("stock") or ""),
            price=_optional_float(payload.get("price", payload.get("limit_price"))),
            budget=float(payload.get("budget", 0)),
            lot=str(payload.get("lot", "odd")),
            quote_file=str(payload.get("quote_file", "")) or None,
            buffer_multiplier=_optional_float(payload.get("buffer_multiplier")),
            output=str(output) if output else None,
            no_write=no_write,
            json=args.json,
        )
        return command_simple_buy(command_args)
    if action == "sell":
        command_args = _ObjectArgs(
            stock=str(payload.get("stock_id") or payload.get("stock") or ""),
            price=_optional_float(payload.get("price", payload.get("limit_price"))),
            quantity=int(float(payload.get("quantity", payload.get("qty", 0)))),
            lot=str(payload.get("lot", "odd")),
            quote_file=str(payload.get("quote_file", "")) or None,
            output=str(output) if output else None,
            no_write=no_write,
            json=args.json,
        )
        return command_simple_sell(command_args)
    raise ValueError("order action must be buy or sell.")


def _print_buy_result(result: SimulationResult) -> None:
    print(f"broker: {result.broker}")
    print(f"mode: {result.mode}")
    print("submitted_to_broker: false")
    print(f"budget: {result.budget:.2f}")
    print(f"order_lot: {result.order_lot}")
    print(f"buffer_multiplier: {result.buffer_multiplier:.6f}")
    print(f"projected_total_cost: {result.projected_total_cost:.2f}")
    print(f"estimated_remaining_cash: {result.estimated_remaining_cash:.2f}")
    if result.output_csv:
        print(f"output_csv: {result.output_csv}")
    print("")
    print(_format_buy_orders_table(result.orders))


def _print_sell_result(result: SellSimulationResult) -> None:
    print(f"broker: {result.broker}")
    print(f"mode: {result.mode}")
    print("submitted_to_broker: false")
    print(f"order_lot: {result.order_lot}")
    print(f"projected_gross_amount: {result.projected_gross_amount:.2f}")
    print(f"projected_fee: {result.projected_fee:.2f}")
    print(f"projected_tax: {result.projected_tax:.2f}")
    print(f"projected_net_amount: {result.projected_net_amount:.2f}")
    if result.output_csv:
        print(f"output_csv: {result.output_csv}")
    print("")
    print(_format_sell_orders_table(result.orders))


def _first_positive_float(row: dict[str, str], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        raw = str(row.get(key, "") or "").strip()
        if not raw:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        if value > 0:
            return value
    return None


def _default_output_path(created_at: str, *, action: str = "buy") -> Path:
    safe_timestamp = created_at.replace(":", "").replace("+", "_").replace("-", "")
    return DATA_DIR / "simulations" / f"{safe_timestamp}_simulate_{action}.csv"


def _format_buy_orders_table(orders: list[SimulatedBuyOrder]) -> str:
    headers = [
        "stock",
        "price",
        "alloc",
        "qty",
        "gross",
        "fee",
        "buffer",
        "est_cost",
        "unused",
        "status",
        "order_id",
    ]
    rows = [
        [
            order.stock_id,
            f"{order.limit_price:.2f}",
            f"{order.allocated_budget:.2f}",
            str(order.quantity),
            f"{order.gross_amount:.2f}",
            f"{order.estimated_fee:.2f}",
            f"{order.cost_buffer:.2f}",
            f"{order.estimated_total_cost:.2f}",
            f"{order.unused_allocated_budget:.2f}",
            order.status,
            order.order_id,
        ]
        for order in orders
    ]
    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))

    def render(row: list[str]) -> str:
        return "  ".join(value.rjust(widths[index]) for index, value in enumerate(row))

    lines = [render(headers), render(["-" * width for width in widths])]
    lines.extend(render(row) for row in rows)
    return "\n".join(lines)


def _format_sell_orders_table(orders: list[SimulatedSellOrder]) -> str:
    headers = [
        "stock",
        "price",
        "qty",
        "gross",
        "fee",
        "tax",
        "net",
        "status",
        "order_id",
    ]
    rows = [
        [
            order.stock_id,
            f"{order.limit_price:.2f}",
            str(order.quantity),
            f"{order.gross_amount:.2f}",
            f"{order.estimated_fee:.2f}",
            f"{order.estimated_tax:.2f}",
            f"{order.estimated_net_amount:.2f}",
            order.status,
            order.order_id,
        ]
        for order in orders
    ]
    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))

    def render(row: list[str]) -> str:
        return "  ".join(value.rjust(widths[index]) for index, value in enumerate(row))

    lines = [render(headers), render(["-" * width for width in widths])]
    lines.extend(render(row) for row in rows)
    return "\n".join(lines)


def _optional_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


class _ObjectArgs:
    def __init__(self, **values: object) -> None:
        self.__dict__.update(values)
