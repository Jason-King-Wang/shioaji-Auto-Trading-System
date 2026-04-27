from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable, Mapping

from .basket import basket_tag_from_strategy_lot_id, normalize_basket_tag
from .order_engine import QuoteState
from .sell_policy import StrategyPosition


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        if value in ("", None):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: object, default: int = 0) -> int:
    try:
        if value in ("", None):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def normalize_fill_side(raw: object) -> str:
    text = str(raw).lower()
    if "buy" in text:
        return "Buy"
    if "sell" in text:
        return "Sell"
    return str(raw)


def _sorted_fill_rows(fills_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    def sort_key(item: tuple[int, dict[str, object]]) -> tuple[int, str, int]:
        index, row = item
        fill_time = str(row.get("fill_time", "")).strip()
        if not fill_time:
            return (1, "", index)
        try:
            parsed = datetime.fromisoformat(fill_time)
        except ValueError:
            return (1, fill_time, index)
        return (0, parsed.isoformat(), index)

    return [row for _, row in sorted(enumerate(fills_rows), key=sort_key)]


def _fill_row_should_skip_reconciliation(row: Mapping[str, object]) -> bool:
    return str(row.get("fill_assignment_status", "")).strip() == "ambiguous_unmapped_fill"


def _quote_last_price(value: object) -> float:
    if isinstance(value, QuoteState):
        return _as_float(value.last_price)
    if isinstance(value, Mapping):
        return _as_float(value.get("last_price"))
    return _as_float(getattr(value, "last_price", 0.0))


def _existing_lot_id_for_stock(states: dict[str, "_PositionState"], stock_id: str) -> str:
    matches = [strategy_lot_id for strategy_lot_id, state in states.items() if state.stock_id == stock_id]
    return matches[0] if len(matches) == 1 else ""


@dataclass(slots=True)
class _PositionState:
    strategy_lot_id: str
    stock_id: str
    stock_name: str
    source: str
    basket_tag: str
    holding_qty: int
    buy_total_cost: float

    @property
    def buy_avg_price(self) -> float:
        if self.holding_qty <= 0:
            return 0.0
        return self.buy_total_cost / self.holding_qty


def _seed_position_states(
    *,
    opening_positions: list[StrategyPosition] | None,
    selection_meta_by_stock: dict[str, dict[str, object]],
    selection_meta_by_strategy_lot: dict[str, dict[str, object]],
    strategy_lot_id_for_stock: Callable[[str], str],
) -> dict[str, _PositionState]:
    states: dict[str, _PositionState] = {}
    for position in opening_positions or []:
        stock_id = str(position.stock_id).strip()
        if not stock_id:
            continue
        strategy_lot_id = str(position.strategy_lot_id or strategy_lot_id_for_stock(stock_id))
        meta = selection_meta_by_strategy_lot.get(strategy_lot_id, {}) or selection_meta_by_stock.get(stock_id, {})
        states[strategy_lot_id] = _PositionState(
            strategy_lot_id=strategy_lot_id,
            stock_id=stock_id,
            stock_name=str(meta.get("stock_name") or position.stock_name or stock_id),
            source=str(meta.get("source") or position.source or ""),
            basket_tag=normalize_basket_tag(meta.get("basket_tag") or position.basket_tag or basket_tag_from_strategy_lot_id(strategy_lot_id)),
            holding_qty=max(int(position.holding_qty), 0),
            buy_total_cost=max(float(position.buy_total_cost), 0.0),
        )
    return states


def _state_for_lot(
    *,
    states: dict[str, _PositionState],
    strategy_lot_id: str,
    stock_id: str,
    selection_meta_by_stock: dict[str, dict[str, object]],
    selection_meta_by_strategy_lot: dict[str, dict[str, object]],
    strategy_lot_id_for_stock: Callable[[str], str],
) -> _PositionState:
    resolved_strategy_lot_id = str(strategy_lot_id or strategy_lot_id_for_stock(stock_id))
    state = states.get(resolved_strategy_lot_id)
    if state is not None:
        return state
    meta = selection_meta_by_strategy_lot.get(resolved_strategy_lot_id, {}) or selection_meta_by_stock.get(stock_id, {})
    state = _PositionState(
        strategy_lot_id=resolved_strategy_lot_id,
        stock_id=stock_id,
        stock_name=str(meta.get("stock_name") or stock_id),
        source=str(meta.get("source") or ""),
        basket_tag=normalize_basket_tag(meta.get("basket_tag") or basket_tag_from_strategy_lot_id(resolved_strategy_lot_id)),
        holding_qty=0,
        buy_total_cost=0.0,
    )
    states[resolved_strategy_lot_id] = state
    return state


def _reconcile_fill_rows(
    *,
    fills_rows: list[dict[str, object]],
    opening_positions: list[StrategyPosition] | None,
    selection_meta_by_stock: dict[str, dict[str, object]],
    selection_meta_by_strategy_lot: dict[str, dict[str, object]],
    strategy_lot_id_for_stock: Callable[[str], str],
    sell_fee_estimator: Callable[[float], float] | None = None,
    sell_tax_estimator: Callable[[float], float] | None = None,
) -> tuple[dict[str, _PositionState], dict[str, dict[str, object]]]:
    states = _seed_position_states(
        opening_positions=opening_positions,
        selection_meta_by_stock=selection_meta_by_stock,
        selection_meta_by_strategy_lot=selection_meta_by_strategy_lot,
        strategy_lot_id_for_stock=strategy_lot_id_for_stock,
    )
    sell_stats: dict[str, dict[str, object]] = {}
    for row in _sorted_fill_rows(fills_rows):
        if _fill_row_should_skip_reconciliation(row):
            continue
        stock_id = str(row.get("stock_id", "")).strip()
        if not stock_id:
            continue
        fill_qty = _as_int(row.get("fill_qty"))
        fill_price = _as_float(row.get("fill_price"))
        if fill_qty <= 0 or fill_price <= 0:
            continue
        strategy_lot_id = (
            str(row.get("strategy_lot_id", "")).strip()
            or _existing_lot_id_for_stock(states, stock_id)
            or strategy_lot_id_for_stock(stock_id)
        )
        state = _state_for_lot(
            states=states,
            strategy_lot_id=strategy_lot_id,
            stock_id=stock_id,
            selection_meta_by_stock=selection_meta_by_stock,
            selection_meta_by_strategy_lot=selection_meta_by_strategy_lot,
            strategy_lot_id_for_stock=strategy_lot_id_for_stock,
        )
        side = normalize_fill_side(row.get("side", ""))
        if side == "Buy":
            state.holding_qty += fill_qty
            state.buy_total_cost += fill_price * fill_qty + _as_float(row.get("fee")) + _as_float(row.get("tax"))
            continue
        if side != "Sell" or state.holding_qty <= 0:
            continue

        sell_qty = min(fill_qty, state.holding_qty)
        if sell_qty <= 0:
            continue

        avg_cost = state.buy_avg_price
        allocated_buy_cost = avg_cost * sell_qty
        gross = fill_price * sell_qty
        explicit_fee = _as_float(row.get("fee"))
        explicit_tax = _as_float(row.get("tax"))
        sell_fee = explicit_fee if explicit_fee > 0 else (sell_fee_estimator(gross) if sell_fee_estimator else 0.0)
        sell_tax = explicit_tax if explicit_tax > 0 else (sell_tax_estimator(gross) if sell_tax_estimator else 0.0)

        bucket = sell_stats.setdefault(
            state.strategy_lot_id,
            {
                "strategy_lot_id": state.strategy_lot_id,
                "stock_id": state.stock_id,
                "sold_qty": 0,
                "gross_proceeds": 0.0,
                "fill_avg_price": 0.0,
                "allocated_buy_cost": 0.0,
                "sell_fee": 0.0,
                "sell_tax": 0.0,
                "realized_pnl": 0.0,
                "basket_tag": state.basket_tag,
                "remaining_qty": state.holding_qty,
            },
        )
        bucket["sold_qty"] = _as_int(bucket.get("sold_qty"), 0) + sell_qty
        bucket["gross_proceeds"] = _as_float(bucket.get("gross_proceeds"), 0.0) + gross
        bucket["allocated_buy_cost"] = _as_float(bucket.get("allocated_buy_cost"), 0.0) + allocated_buy_cost
        bucket["sell_fee"] = _as_float(bucket.get("sell_fee"), 0.0) + sell_fee
        bucket["sell_tax"] = _as_float(bucket.get("sell_tax"), 0.0) + sell_tax

        state.holding_qty -= sell_qty
        state.buy_total_cost = max(state.buy_total_cost - allocated_buy_cost, 0.0)
        bucket["remaining_qty"] = state.holding_qty

    for strategy_lot_id, bucket in sell_stats.items():
        sold_qty = _as_int(bucket.get("sold_qty"), 0)
        gross = _as_float(bucket.get("gross_proceeds"), 0.0)
        allocated_buy_cost = _as_float(bucket.get("allocated_buy_cost"), 0.0)
        sell_fee = _as_float(bucket.get("sell_fee"), 0.0)
        sell_tax = _as_float(bucket.get("sell_tax"), 0.0)
        bucket["fill_avg_price"] = 0.0 if sold_qty <= 0 else gross / sold_qty
        bucket["realized_pnl"] = gross - sell_fee - sell_tax - allocated_buy_cost
        bucket["remaining_qty"] = states.get(strategy_lot_id).holding_qty if strategy_lot_id in states else _as_int(bucket.get("remaining_qty"), 0)
        bucket["strategy_lot_id"] = strategy_lot_id
    return states, sell_stats


def build_positions_rows_from_fills(
    *,
    run_id: str,
    trade_date: date,
    fills_rows: list[dict[str, object]],
    selection_meta_by_stock: dict[str, dict[str, object]],
    quote_rows_by_stock: dict[str, object],
    strategy_lot_id_for_stock: Callable[[str], str],
    selection_meta_by_strategy_lot: dict[str, dict[str, object]] | None = None,
    opening_positions: list[StrategyPosition] | None = None,
    status: str = "strategy_fill_scoped",
) -> list[dict[str, object]]:
    states, _ = _reconcile_fill_rows(
        fills_rows=fills_rows,
        opening_positions=opening_positions,
        selection_meta_by_stock=selection_meta_by_stock,
        selection_meta_by_strategy_lot=selection_meta_by_strategy_lot or {},
        strategy_lot_id_for_stock=strategy_lot_id_for_stock,
    )
    rows: list[dict[str, object]] = []
    for strategy_lot_id in sorted(states, key=lambda item: (states[item].stock_id, item)):
        state = states[strategy_lot_id]
        if state.holding_qty <= 0:
            continue
        current_price = _quote_last_price(quote_rows_by_stock.get(state.stock_id))
        if current_price <= 0:
            current_price = state.buy_avg_price
        rows.append(
            {
                "run_id": run_id,
                "strategy_lot_id": state.strategy_lot_id,
                "stock_id": state.stock_id,
                "stock_name": state.stock_name,
                "source": state.source,
                "basket_tag": state.basket_tag,
                "holding_qty": state.holding_qty,
                "buy_avg_price": state.buy_avg_price,
                "buy_total_cost": state.buy_total_cost,
                "current_price": current_price,
                "status": status,
            }
        )
    return rows


def compute_sell_fill_stats(
    *,
    fills_rows: list[dict[str, object]],
    opening_positions: list[StrategyPosition],
    selection_meta_by_stock: dict[str, dict[str, object]] | None = None,
    selection_meta_by_strategy_lot: dict[str, dict[str, object]] | None = None,
    strategy_lot_id_for_stock: Callable[[str], str] | None = None,
    fees: Any,
) -> dict[str, dict[str, object]]:
    _, sell_stats = _reconcile_fill_rows(
        fills_rows=fills_rows,
        opening_positions=opening_positions,
        selection_meta_by_stock=selection_meta_by_stock or {},
        selection_meta_by_strategy_lot=selection_meta_by_strategy_lot or {},
        strategy_lot_id_for_stock=strategy_lot_id_for_stock or (lambda stock_id: stock_id),
        sell_fee_estimator=fees.estimate_sell_fee,
        sell_tax_estimator=fees.estimate_sell_tax,
    )
    return {strategy_lot_id: sell_stats[strategy_lot_id] for strategy_lot_id in sorted(sell_stats)}


def build_excluded_positions_rows(
    *,
    broker_positions: list[Any],
    strategy_positions_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    strategy_qty_by_stock: dict[str, int] = {}
    strategy_name_by_stock: dict[str, str] = {}
    for row in strategy_positions_rows:
        stock_id = str(row.get("stock_id", "")).strip()
        if not stock_id:
            continue
        strategy_qty_by_stock[stock_id] = strategy_qty_by_stock.get(stock_id, 0) + _as_int(row.get("holding_qty"), 0)
        stock_name = str(row.get("stock_name", "")).strip()
        if stock_name and stock_id not in strategy_name_by_stock:
            strategy_name_by_stock[stock_id] = stock_name
    rows: list[dict[str, object]] = []
    for position in broker_positions:
        stock_id = str(position.stock_id).strip()
        if not stock_id:
            continue
        broker_qty = int(position.quantity)
        strategy_qty = strategy_qty_by_stock.get(stock_id, 0)
        excluded_qty = max(broker_qty - strategy_qty, 0)
        if excluded_qty <= 0:
            continue
        reason = "legacy_non_strategy_holding" if strategy_qty == 0 else "broker_qty_exceeds_strategy_qty"
        rows.append(
            {
                "stock_id": stock_id,
                "stock_name": strategy_name_by_stock.get(stock_id, position.stock_name or stock_id),
                "broker_qty": broker_qty,
                "strategy_qty": strategy_qty,
                "excluded_qty": excluded_qty,
                "reason": reason,
            }
        )
    return rows


def build_pnl_snapshot(
    *,
    run_id: str,
    trade_date: date,
    positions_rows: list[dict[str, object]],
    realized_pnl: float = 0.0,
    realized_cost_basis: float = 0.0,
    snapshot_time: datetime | None = None,
) -> dict[str, object]:
    cash_used = sum(_as_float(row.get("buy_total_cost"), 0.0) for row in positions_rows)
    strategy_equity = sum(
        _as_float(row.get("current_price"), _as_float(row.get("buy_avg_price"), 0.0)) * _as_int(row.get("holding_qty"), 0)
        for row in positions_rows
    )
    unrealized_pnl = strategy_equity - cash_used
    total_pnl_after_fee_tax = unrealized_pnl + realized_pnl
    capital_base = cash_used + max(realized_cost_basis, 0.0)
    strategy_return = 0.0 if capital_base <= 0 else total_pnl_after_fee_tax / capital_base
    resolved_snapshot_time = snapshot_time or datetime.now()
    return {
        "run_id": run_id,
        "trade_date": trade_date.isoformat(),
        "snapshot_time": resolved_snapshot_time.isoformat(timespec="seconds"),
        "strategy_equity": strategy_equity,
        "cash_used": cash_used,
        "unrealized_pnl": unrealized_pnl,
        "realized_pnl": realized_pnl,
        "total_pnl_after_fee_tax": total_pnl_after_fee_tax,
        "strategy_return": strategy_return,
        "twii_return": 0.0,
        "tsmc_return": 0.0,
    }
