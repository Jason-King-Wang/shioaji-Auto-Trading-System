from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from .basket import basket_tag_from_strategy_lot_id, normalize_basket_tag
from .calendar import resolve_week_trade_plan
from .paths import auto_trading_dir_for


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _as_float(raw: object, default: float = 0.0) -> float:
    try:
        if raw in ("", None):
            return default
        return float(raw)
    except (TypeError, ValueError):
        return default


def _as_int(raw: object, default: int = 0) -> int:
    try:
        if raw in ("", None):
            return default
        return int(float(raw))
    except (TypeError, ValueError):
        return default


def _normalize_fill_side(raw: object) -> str:
    text = str(raw).lower()
    if "buy" in text:
        return "Buy"
    if "sell" in text:
        return "Sell"
    return str(raw)


def _bucket_for_lot(
    lots: dict[str, dict[str, object]],
    *,
    strategy_lot_id: str,
    stock_id: str,
    source_trade_date: str,
) -> dict[str, object]:
    bucket = lots.get(strategy_lot_id)
    if bucket is not None:
        return bucket
    bucket = {
        "strategy_lot_id": strategy_lot_id,
        "stock_id": stock_id,
        "stock_name": stock_id,
        "source": "",
        "basket_tag": basket_tag_from_strategy_lot_id(strategy_lot_id),
        "source_trade_date": source_trade_date,
        "last_seen_trade_date": source_trade_date,
        "buy_order_ids": [],
        "sell_order_ids": [],
        "fill_order_ids": [],
        "buy_custom_fields": [],
        "sell_custom_fields": [],
        "fill_custom_fields": [],
        "buy_order_statuses": [],
        "sell_order_statuses": [],
        "buy_fill_qty": 0,
        "sell_fill_qty": 0,
        "buy_fill_amount": 0.0,
        "sell_fill_amount": 0.0,
        "buy_fill_avg_price": 0.0,
        "sell_fill_avg_price": 0.0,
        "allocated_buy_cost": 0.0,
        "realized_pnl": 0.0,
        "conservative_profit": 0.0,
        "closing_qty": 0,
        "closing_buy_total_cost": 0.0,
        "closing_buy_avg_price": 0.0,
        "position_status": "",
        "latest_sell_order_status": "",
        "lot_status": "inactive",
    }
    lots[strategy_lot_id] = bucket
    return bucket


def _append_unique(bucket: dict[str, object], field: str, value: str) -> None:
    if not value:
        return
    items = bucket.setdefault(field, [])
    if isinstance(items, list) and value not in items:
        items.append(value)


def _build_week_lot_ledger(trade_date: date) -> list[dict[str, object]]:
    plan = resolve_week_trade_plan(trade_date)
    lots: dict[str, dict[str, object]] = {}
    for day in plan.week_trade_days or [trade_date]:
        run_dir = auto_trading_dir_for(day)
        day_text = day.isoformat()

        for row in _read_csv_rows(run_dir / "orders.csv"):
            strategy_lot_id = str(row.get("strategy_lot_id", "")).strip()
            stock_id = str(row.get("stock_id", "")).strip()
            if not strategy_lot_id or not stock_id:
                continue
            bucket = _bucket_for_lot(lots, strategy_lot_id=strategy_lot_id, stock_id=stock_id, source_trade_date=day_text)
            bucket["stock_name"] = row.get("stock_name", bucket.get("stock_name", stock_id))
            bucket["source"] = row.get("source", bucket.get("source", ""))
            bucket["basket_tag"] = normalize_basket_tag(row.get("basket_tag") or bucket.get("basket_tag"))
            bucket["last_seen_trade_date"] = day_text
            _append_unique(bucket, "buy_order_ids", str(row.get("order_id", "")).strip())
            _append_unique(bucket, "buy_custom_fields", str(row.get("broker_custom_field", "")).strip())
            _append_unique(bucket, "buy_order_statuses", str(row.get("status", "")).strip())

        for row in _read_csv_rows(run_dir / "fills.csv"):
            strategy_lot_id = str(row.get("strategy_lot_id", "")).strip()
            stock_id = str(row.get("stock_id", "")).strip()
            if not strategy_lot_id or not stock_id:
                continue
            bucket = _bucket_for_lot(lots, strategy_lot_id=strategy_lot_id, stock_id=stock_id, source_trade_date=day_text)
            bucket["last_seen_trade_date"] = day_text
            bucket["basket_tag"] = normalize_basket_tag(row.get("basket_tag") or bucket.get("basket_tag"))
            qty = _as_int(row.get("fill_qty"), 0)
            amount = _as_float(row.get("fill_price"), 0.0) * qty
            if _normalize_fill_side(row.get("side", "")) == "Sell":
                bucket["sell_fill_qty"] = _as_int(bucket.get("sell_fill_qty"), 0) + qty
                bucket["sell_fill_amount"] = _as_float(bucket.get("sell_fill_amount"), 0.0) + amount
            else:
                bucket["buy_fill_qty"] = _as_int(bucket.get("buy_fill_qty"), 0) + qty
                bucket["buy_fill_amount"] = _as_float(bucket.get("buy_fill_amount"), 0.0) + amount
            _append_unique(bucket, "fill_order_ids", str(row.get("broker_fill_id", "")).strip())
            _append_unique(bucket, "fill_custom_fields", str(row.get("broker_custom_field", "")).strip())

        for row in _read_csv_rows(run_dir / "sell_decisions.csv"):
            strategy_lot_id = str(row.get("strategy_lot_id", "")).strip()
            stock_id = str(row.get("stock_id", "")).strip()
            if not strategy_lot_id or not stock_id:
                continue
            bucket = _bucket_for_lot(lots, strategy_lot_id=strategy_lot_id, stock_id=stock_id, source_trade_date=day_text)
            bucket["stock_name"] = row.get("stock_name", bucket.get("stock_name", stock_id))
            bucket["basket_tag"] = normalize_basket_tag(row.get("basket_tag") or bucket.get("basket_tag"))
            bucket["last_seen_trade_date"] = day_text
            bucket["allocated_buy_cost"] = _as_float(bucket.get("allocated_buy_cost"), 0.0) + _as_float(row.get("allocated_buy_cost"), 0.0)
            bucket["realized_pnl"] = _as_float(bucket.get("realized_pnl"), 0.0) + _as_float(row.get("realized_pnl"), 0.0)
            bucket["conservative_profit"] = _as_float(bucket.get("conservative_profit"), 0.0) + _as_float(row.get("conservative_profit"), 0.0)
            bucket["latest_sell_order_status"] = row.get("sell_order_status", bucket.get("latest_sell_order_status", ""))
            _append_unique(bucket, "sell_order_ids", str(row.get("sell_order_id", "")).strip())
            _append_unique(bucket, "sell_custom_fields", str(row.get("broker_custom_field", "")).strip())
            _append_unique(bucket, "sell_order_statuses", str(row.get("sell_order_status", "")).strip())

        for row in _read_csv_rows(run_dir / "positions.csv"):
            strategy_lot_id = str(row.get("strategy_lot_id", "")).strip()
            stock_id = str(row.get("stock_id", "")).strip()
            if not strategy_lot_id or not stock_id:
                continue
            bucket = _bucket_for_lot(lots, strategy_lot_id=strategy_lot_id, stock_id=stock_id, source_trade_date=day_text)
            bucket["stock_name"] = row.get("stock_name", bucket.get("stock_name", stock_id))
            bucket["source"] = row.get("source", bucket.get("source", ""))
            bucket["basket_tag"] = normalize_basket_tag(row.get("basket_tag") or bucket.get("basket_tag"))
            bucket["last_seen_trade_date"] = day_text
            bucket["closing_qty"] = _as_int(row.get("holding_qty"), _as_int(row.get("quantity"), 0))
            bucket["closing_buy_total_cost"] = _as_float(
                row.get("buy_total_cost"),
                _as_float(row.get("buy_avg_price"), 0.0) * _as_int(row.get("holding_qty"), _as_int(row.get("quantity"), 0)),
            )
            bucket["closing_buy_avg_price"] = _as_float(row.get("buy_avg_price"), 0.0)
            bucket["position_status"] = row.get("status", "")

    rows: list[dict[str, object]] = []
    for strategy_lot_id in sorted(lots):
        bucket = lots[strategy_lot_id]
        buy_fill_qty = _as_int(bucket.get("buy_fill_qty"), 0)
        sell_fill_qty = _as_int(bucket.get("sell_fill_qty"), 0)
        buy_fill_amount = _as_float(bucket.get("buy_fill_amount"), 0.0)
        sell_fill_amount = _as_float(bucket.get("sell_fill_amount"), 0.0)
        closing_qty = _as_int(bucket.get("closing_qty"), 0)
        lot_status = "closed"
        if closing_qty > 0:
            lot_status = "open"
        elif sell_fill_qty > 0:
            lot_status = "settled"
        elif buy_fill_qty > 0:
            lot_status = "bought_not_closed"
        row = {
            "strategy_lot_id": strategy_lot_id,
            "stock_id": bucket.get("stock_id", ""),
            "stock_name": bucket.get("stock_name", ""),
            "source": bucket.get("source", ""),
            "basket_tag": bucket.get("basket_tag", ""),
            "source_trade_date": bucket.get("source_trade_date", ""),
            "last_seen_trade_date": bucket.get("last_seen_trade_date", ""),
            "buy_order_ids": "|".join(bucket.get("buy_order_ids", [])),
            "buy_custom_fields": "|".join(bucket.get("buy_custom_fields", [])),
            "buy_order_statuses": "|".join(bucket.get("buy_order_statuses", [])),
            "sell_order_ids": "|".join(bucket.get("sell_order_ids", [])),
            "sell_custom_fields": "|".join(bucket.get("sell_custom_fields", [])),
            "sell_order_statuses": "|".join(bucket.get("sell_order_statuses", [])),
            "fill_order_ids": "|".join(bucket.get("fill_order_ids", [])),
            "fill_custom_fields": "|".join(bucket.get("fill_custom_fields", [])),
            "buy_fill_qty": buy_fill_qty,
            "buy_fill_avg_price": 0.0 if buy_fill_qty <= 0 else buy_fill_amount / buy_fill_qty,
            "sell_fill_qty": sell_fill_qty,
            "sell_fill_avg_price": 0.0 if sell_fill_qty <= 0 else sell_fill_amount / sell_fill_qty,
            "allocated_buy_cost": _as_float(bucket.get("allocated_buy_cost"), 0.0),
            "realized_pnl": _as_float(bucket.get("realized_pnl"), 0.0),
            "conservative_profit": _as_float(bucket.get("conservative_profit"), 0.0),
            "closing_qty": closing_qty,
            "closing_buy_total_cost": _as_float(bucket.get("closing_buy_total_cost"), 0.0),
            "closing_buy_avg_price": _as_float(bucket.get("closing_buy_avg_price"), 0.0),
            "position_status": bucket.get("position_status", ""),
            "latest_sell_order_status": bucket.get("latest_sell_order_status", ""),
            "lot_status": lot_status,
        }
        rows.append(row)
    return rows


def load_week_lot_ledger(trade_date: date) -> list[dict[str, object]]:
    return _build_week_lot_ledger(trade_date)


def load_week_order_id_lot_lookup(trade_date: date) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for row in _build_week_lot_ledger(trade_date):
        strategy_lot_id = str(row.get("strategy_lot_id", "")).strip()
        for field in ("buy_order_ids", "sell_order_ids", "fill_order_ids"):
            raw = str(row.get(field, "")).strip()
            if not raw or not strategy_lot_id:
                continue
            for order_id in raw.split("|"):
                normalized = order_id.strip()
                if normalized:
                    lookup[normalized] = strategy_lot_id
    return lookup


def load_week_custom_field_lot_lookup(trade_date: date) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for row in _build_week_lot_ledger(trade_date):
        strategy_lot_id = str(row.get("strategy_lot_id", "")).strip()
        for field in ("buy_custom_fields", "sell_custom_fields", "fill_custom_fields"):
            raw = str(row.get(field, "")).strip()
            if not raw or not strategy_lot_id:
                continue
            for custom_field in raw.split("|"):
                normalized = custom_field.strip()
                if normalized:
                    lookup[normalized] = strategy_lot_id
    return lookup
