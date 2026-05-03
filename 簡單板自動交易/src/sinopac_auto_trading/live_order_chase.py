from __future__ import annotations

import csv
import json
import time as time_module
from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from .config import describe_live_submit_guard
from .order_engine import ManagedOrder, QuoteState, current_buy_mode, current_mode_target_price, plan_order_action
from .paths import auto_trading_dir_for
from .shioaji_client import login, resolve_stock_contract
from .tick import normalize_price_to_valid_tick
from .time_utils import TAIPEI


def _load_shioaji() -> Any:
    try:
        import shioaji as sj  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise RuntimeError("shioaji is not installed. Install dependencies first with `python -m pip install -e .`.") from exc
    return sj


def _now() -> datetime:
    return datetime.now(TAIPEI)


def parse_hhmm(raw: str) -> time:
    return datetime.strptime(raw, "%H:%M").time()


@dataclass(slots=True)
class ChaseStep:
    timestamp: str
    mode: str
    action: str
    target_price: float
    order_price: float | None
    remaining_qty: int
    order_id: str
    trade_state: str
    note: str


@dataclass(slots=True)
class ChaseResult:
    stock_id: str
    stock_name: str
    action: str
    order_lot: str
    quantity: int
    price_cap: float | None
    submitted: bool
    final_state: str
    final_order_id: str
    final_order_price: float | None
    steps: list[ChaseStep]
    summary_path: Path


def _snapshot_timestamp(snapshot: Any) -> str:
    raw = getattr(snapshot, "ts", None)
    if raw in (None, ""):
        return ""
    try:
        return datetime.fromtimestamp(int(raw) / 1_000_000_000, tz=TAIPEI).isoformat()
    except Exception:
        return str(raw)


def fetch_quote_state(api: Any, contract: Any) -> tuple[QuoteState, str]:
    snapshots = api.snapshots([contract])
    snapshot = snapshots[0]
    last_price = float(getattr(snapshot, "close", 0) or getattr(snapshot, "sell_price", 0) or getattr(snapshot, "buy_price", 0) or 0)
    if last_price <= 0:
        raise RuntimeError(f"Snapshot for {contract.code} does not have a usable last price.")
    quote = QuoteState(
        last_price=last_price,
        bid1=float(getattr(snapshot, "buy_price", 0) or 0) or None,
        ask1=float(getattr(snapshot, "sell_price", 0) or 0) or None,
        limit_up_price=float(getattr(contract, "limit_up", 0) or 0) or None,
        limit_down_price=float(getattr(contract, "limit_down", 0) or 0) or None,
    )
    return quote, _snapshot_timestamp(snapshot)


def capped_target_price(action: str, computed_target_price: float, price_cap: float | None) -> float:
    target = normalize_price_to_valid_tick(computed_target_price)
    if price_cap is None:
        return target
    cap = normalize_price_to_valid_tick(price_cap)
    if action == "Buy":
        return min(target, cap)
    return max(target, cap)


def _status_name(trade: Any) -> str:
    return str(getattr(getattr(trade, "status", None), "status", ""))


def _order_id(trade: Any) -> str:
    return str(getattr(getattr(trade, "order", None), "id", ""))


def _order_qty(trade: Any) -> int:
    status = getattr(trade, "status", None)
    order = getattr(trade, "order", None)
    raw = getattr(status, "order_quantity", None)
    if raw in (None, "", 0):
        raw = getattr(order, "quantity", 0)
    return int(raw or 0)


def _filled_qty(trade: Any) -> int:
    status = getattr(trade, "status", None)
    for field_name in ("deal_quantity", "filled_quantity", "deal_qty"):
        raw = getattr(status, field_name, None)
        if raw not in (None, ""):
            return int(raw or 0)
    return 0


def _cancel_qty(trade: Any) -> int:
    status = getattr(trade, "status", None)
    return int(getattr(status, "cancel_quantity", 0) or 0)


def classify_trade_state(trade: Any) -> str:
    status_name = _status_name(trade).lower()
    order_qty = _order_qty(trade)
    filled_qty = _filled_qty(trade)
    cancel_qty = _cancel_qty(trade)

    if order_qty > 0 and filled_qty >= order_qty:
        return "filled"
    if any(keyword in status_name for keyword in ("failed", "fail", "rejected", "reject", "error")):
        return "failed"
    if any(keyword in status_name for keyword in ("cancel", "cancelled", "canceled")) or (order_qty > 0 and cancel_qty >= order_qty):
        return "cancelled"
    if "filled" in status_name:
        return "filled"
    return "active"


def _trade_by_order_id(api: Any, order_id: str) -> Any | None:
    for trade in api.list_trades():
        if str(getattr(getattr(trade, "order", None), "id", "")) == order_id:
            return trade
    return None


def _trade_by_custom_field(api: Any, custom_field: str) -> Any | None:
    for trade in api.list_trades():
        if str(getattr(getattr(trade, "order", None), "custom_field", "")) == custom_field:
            return trade
    return None


def _managed_order_from_trade(trade: Any) -> ManagedOrder:
    order = getattr(trade, "order", None)
    quantity = int(getattr(order, "quantity", 0) or 0)
    filled_qty = _filled_qty(trade)
    remaining_qty = max(quantity - filled_qty, 0)
    return ManagedOrder(
        strategy_lot_id=str(getattr(order, "custom_field", "")),
        stock_id=str(getattr(getattr(trade, "contract", None), "code", "")),
        order_id=_order_id(trade),
        order_price=float(getattr(order, "price", 0) or 0),
        order_qty=quantity,
        filled_qty=filled_qty,
        remaining_qty=remaining_qty,
    )


def _place_order(
    api: Any,
    contract: Any,
    *,
    action: str,
    order_lot: str,
    price: float,
    quantity: int,
    custom_field: str,
) -> Any:
    sj = _load_shioaji()
    order = api.Order(
        price=price,
        quantity=quantity,
        action=getattr(sj.constant.Action, action),
        price_type=sj.constant.StockPriceType.LMT,
        order_type=sj.constant.OrderType.ROD,
        order_lot=getattr(sj.constant.StockOrderLot, order_lot),
        custom_field=custom_field,
        account=api.stock_account,
    )
    try:
        return api.place_order(contract, order)
    except Exception as exc:  # pragma: no cover - depends on broker timing
        api.update_status(api.stock_account)
        recovered = _trade_by_custom_field(api, custom_field)
        if recovered is not None:
            return recovered
        raise RuntimeError(f"place_order failed for {custom_field}: {exc}") from exc


def _cancel_order_and_wait(api: Any, trade: Any, *, wait_seconds: int = 20) -> Any:
    api.cancel_order(trade)
    order_id = _order_id(trade)
    deadline = time_module.time() + wait_seconds
    latest = trade
    while time_module.time() < deadline:
        time_module.sleep(2)
        api.update_status(api.stock_account)
        refreshed = _trade_by_order_id(api, order_id)
        if refreshed is not None:
            latest = refreshed
        state = classify_trade_state(latest)
        if state in {"cancelled", "filled", "failed"}:
            return latest
    return latest


def _write_chase_result(trade_date: date, result: ChaseResult) -> Path:
    run_dir = auto_trading_dir_for(trade_date)
    run_dir.mkdir(parents=True, exist_ok=True)
    summary_path = run_dir / f"chase_{result.stock_id}.json"
    steps_path = run_dir / f"chase_{result.stock_id}_steps.csv"
    summary = asdict(result)
    summary["summary_path"] = str(summary_path)
    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    with steps_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(result.steps[0]).keys()) if result.steps else ["timestamp"])
        writer.writeheader()
        for step in result.steps:
            writer.writerow(asdict(step))
    return summary_path


def run_single_stock_chase(
    *,
    settings: Any,
    stock_id: str,
    exchange: str,
    action: str,
    order_lot: str,
    quantity: int,
    price_cap: float | None,
    live: bool,
    submit: bool,
    confirm_live: bool,
    start_time: time,
    end_time: time,
    check_interval_seconds: int,
    reprice_threshold_ticks: int,
    custom_prefix: str,
) -> ChaseResult:
    if action != "Buy":
        raise RuntimeError("chase-stock-order currently supports Buy only.")
    if live and submit:
        allowed, reason = settings.evaluate_live_submit_guard(confirm_live=confirm_live)
        if not allowed:
            raise RuntimeError(describe_live_submit_guard(reason))

    api, _accounts = login(settings, simulation=not live, fetch_contract=True)
    contract = resolve_stock_contract(api, stock_id, exchange_hint=exchange)
    stock_name = str(getattr(contract, "name", stock_id))

    quote, snapshot_ts = fetch_quote_state(api, contract)
    initial_mode = current_buy_mode(_now())
    initial_target = capped_target_price(action, current_mode_target_price(quote, initial_mode), price_cap)
    steps = [
        ChaseStep(
            timestamp=_now().isoformat(),
            mode=initial_mode.value,
            action="preview",
            target_price=initial_target,
            order_price=None,
            remaining_qty=quantity,
            order_id="",
            trade_state="preview_only" if not submit else "pending",
            note=f"snapshot_ts={snapshot_ts}",
        )
    ]

    if not submit:
        result = ChaseResult(
            stock_id=contract.code,
            stock_name=stock_name,
            action=action,
            order_lot=order_lot,
            quantity=quantity,
            price_cap=price_cap,
            submitted=False,
            final_state="preview_only",
            final_order_id="",
            final_order_price=None,
            steps=steps,
            summary_path=Path(""),
        )
        result.summary_path = _write_chase_result(_now().date(), result)
        return result

    while _now().time() < start_time:
        seconds = (_now().replace(hour=start_time.hour, minute=start_time.minute, second=0, microsecond=0) - _now()).total_seconds()
        time_module.sleep(max(1, min(int(seconds), 30)))

    active_trade = None
    attempt = 0
    final_state = "expired_unfilled"
    final_order_price: float | None = None
    final_order_id = ""

    while _now().time() <= end_time:
        now = _now()
        quote, snapshot_ts = fetch_quote_state(api, contract)
        mode = current_buy_mode(now)
        target_price = capped_target_price(action, current_mode_target_price(quote, mode), price_cap)

        if active_trade is not None:
            api.update_status(api.stock_account)
            refreshed = _trade_by_order_id(api, _order_id(active_trade))
            if refreshed is not None:
                active_trade = refreshed
            trade_state = classify_trade_state(active_trade)
            if trade_state == "filled":
                final_state = "filled"
                final_order_id = _order_id(active_trade)
                final_order_price = float(getattr(getattr(active_trade, "order", None), "price", 0) or 0)
                steps.append(
                    ChaseStep(
                        timestamp=now.isoformat(),
                        mode=mode.value,
                        action="filled",
                        target_price=target_price,
                        order_price=final_order_price,
                        remaining_qty=0,
                        order_id=final_order_id,
                        trade_state=final_state,
                        note=f"snapshot_ts={snapshot_ts}",
                    )
                )
                break
            if trade_state in {"failed", "cancelled"}:
                active_trade = None

        if active_trade is None:
            attempt += 1
            custom_field = f"{custom_prefix[:2].upper() or 'CH'}{attempt:03d}"[:6]
            active_trade = _place_order(
                api,
                contract,
                action=action,
                order_lot=order_lot,
                price=target_price,
                quantity=quantity,
                custom_field=custom_field,
            )
            final_order_id = _order_id(active_trade)
            final_order_price = float(getattr(getattr(active_trade, "order", None), "price", 0) or 0)
            steps.append(
                ChaseStep(
                    timestamp=now.isoformat(),
                    mode=mode.value,
                    action="place",
                    target_price=target_price,
                    order_price=final_order_price,
                    remaining_qty=quantity,
                    order_id=final_order_id,
                    trade_state=classify_trade_state(active_trade),
                    note=f"snapshot_ts={snapshot_ts}",
                )
            )
        else:
            managed = _managed_order_from_trade(active_trade)
            remaining_qty = max(quantity - managed.filled_qty, 0)
            order_action = plan_order_action(
                managed,
                target_price=target_price,
                remaining_qty=remaining_qty,
                reprice_threshold_ticks=reprice_threshold_ticks,
            )
            if order_action.action == "cancel_replace":
                cancelled_trade = _cancel_order_and_wait(api, active_trade)
                cancel_state = classify_trade_state(cancelled_trade)
                steps.append(
                    ChaseStep(
                        timestamp=now.isoformat(),
                        mode=mode.value,
                        action="cancel_replace",
                        target_price=target_price,
                        order_price=managed.order_price,
                        remaining_qty=remaining_qty,
                        order_id=managed.order_id,
                        trade_state=cancel_state,
                        note=order_action.reason,
                    )
                )
                if cancel_state == "filled":
                    final_state = "filled"
                    final_order_id = _order_id(cancelled_trade)
                    final_order_price = float(getattr(getattr(cancelled_trade, "order", None), "price", 0) or 0)
                    active_trade = cancelled_trade
                    break
                active_trade = None
                continue
            steps.append(
                ChaseStep(
                    timestamp=now.isoformat(),
                    mode=mode.value,
                    action=order_action.action,
                    target_price=target_price,
                    order_price=managed.order_price,
                    remaining_qty=remaining_qty,
                    order_id=managed.order_id,
                    trade_state=classify_trade_state(active_trade),
                    note=order_action.reason,
                )
            )

        time_module.sleep(max(check_interval_seconds, 1))

    if final_state != "filled" and active_trade is not None:
        api.update_status(api.stock_account)
        refreshed = _trade_by_order_id(api, _order_id(active_trade))
        if refreshed is not None:
            active_trade = refreshed
        if classify_trade_state(active_trade) == "active":
            active_trade = _cancel_order_and_wait(api, active_trade)
            steps.append(
                ChaseStep(
                    timestamp=_now().isoformat(),
                    mode=current_buy_mode(_now()).value,
                    action="cancel_at_end",
                    target_price=final_order_price or initial_target,
                    order_price=float(getattr(getattr(active_trade, "order", None), "price", 0) or 0),
                    remaining_qty=max(quantity - _filled_qty(active_trade), 0),
                    order_id=_order_id(active_trade),
                    trade_state=classify_trade_state(active_trade),
                    note="end_of_chase_window",
                )
            )
        final_state = classify_trade_state(active_trade)
        final_order_id = _order_id(active_trade)
        final_order_price = float(getattr(getattr(active_trade, "order", None), "price", 0) or 0)

    result = ChaseResult(
        stock_id=contract.code,
        stock_name=stock_name,
        action=action,
        order_lot=order_lot,
        quantity=quantity,
        price_cap=price_cap,
        submitted=True,
        final_state=final_state,
        final_order_id=final_order_id,
        final_order_price=final_order_price,
        steps=steps,
        summary_path=Path(""),
    )
    result.summary_path = _write_chase_result(_now().date(), result)
    return result
