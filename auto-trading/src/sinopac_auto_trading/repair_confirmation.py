from __future__ import annotations

from datetime import datetime
from typing import Iterable


def _text(raw: object) -> str:
    return str(raw or "").strip()


def _as_int(raw: object, default: int = 0) -> int:
    try:
        if raw in ("", None):
            return default
        return int(float(raw))
    except (TypeError, ValueError):
        return default


def _active_order_status(raw: object) -> bool:
    status = _text(raw).lower()
    if not status:
        return False
    if any(token in status for token in ("cancel", "fail", "reject", "blocked", "skip")):
        return False
    return any(token in status for token in ("active", "submit", "pending", "order", "keep"))


def _buy_fill_side(raw: object) -> bool:
    side = _text(raw).lower()
    return not side or "buy" in side


def _aggregate_order_rows(rows: Iterable[dict[str, object]]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for row in rows:
        lot_id = _text(row.get("strategy_lot_id"))
        if not lot_id:
            continue
        bucket = result.setdefault(
            lot_id,
            {
                "order_ids": [],
                "statuses": [],
                "active_qty": 0,
                "filled_qty": 0,
                "order_qty": 0,
                "broker_custom_fields": [],
            },
        )
        order_id = _text(row.get("order_id"))
        if order_id and order_id not in bucket["order_ids"]:
            bucket["order_ids"].append(order_id)
        status = _text(row.get("status") or row.get("order_status"))
        if status and status not in bucket["statuses"]:
            bucket["statuses"].append(status)
        custom_field = _text(row.get("broker_custom_field"))
        if custom_field and custom_field not in bucket["broker_custom_fields"]:
            bucket["broker_custom_fields"].append(custom_field)
        filled_qty = _as_int(row.get("filled_qty"), 0)
        active_qty = _as_int(row.get("active_order_qty"), _as_int(row.get("remaining_qty"), 0))
        order_qty = _as_int(row.get("order_qty"), filled_qty + active_qty)
        target_qty = _as_int(row.get("target_qty"), 0)
        bucket["filled_qty"] = max(_as_int(bucket.get("filled_qty"), 0), filled_qty)
        if _active_order_status(status):
            bucket["active_qty"] = max(_as_int(bucket.get("active_qty"), 0), active_qty)
            bucket["order_qty"] = max(_as_int(bucket.get("order_qty"), 0), order_qty, filled_qty + active_qty)
        elif filled_qty > 0:
            bucket["order_qty"] = max(_as_int(bucket.get("order_qty"), 0), filled_qty, order_qty)
        elif order_id and target_qty > 0 and not status:
            bucket["order_qty"] = max(_as_int(bucket.get("order_qty"), 0), target_qty)
    return result


def _aggregate_fill_rows(rows: Iterable[dict[str, object]]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for row in rows:
        lot_id = _text(row.get("strategy_lot_id"))
        if not lot_id or not _buy_fill_side(row.get("side")):
            continue
        bucket = result.setdefault(
            lot_id,
            {
                "filled_qty": 0,
                "fill_order_ids": [],
                "ambiguous": False,
            },
        )
        bucket["filled_qty"] = _as_int(bucket.get("filled_qty"), 0) + max(_as_int(row.get("fill_qty"), 0), 0)
        fill_order_id = _text(row.get("broker_fill_id") or row.get("order_id"))
        if fill_order_id and fill_order_id not in bucket["fill_order_ids"]:
            bucket["fill_order_ids"].append(fill_order_id)
        if _text(row.get("fill_assignment_status")) == "ambiguous_unmapped_fill":
            bucket["ambiguous"] = True
    return result


def _aggregate_position_rows(rows: Iterable[dict[str, object]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in rows:
        lot_id = _text(row.get("strategy_lot_id"))
        if not lot_id:
            continue
        qty = max(_as_int(row.get("holding_qty"), _as_int(row.get("quantity"), 0)), 0)
        result[lot_id] = max(result.get(lot_id, 0), qty)
    return result


def build_repair_confirmation_rows(
    *,
    intended_rows: Iterable[dict[str, object]],
    order_rows: Iterable[dict[str, object]],
    fill_rows: Iterable[dict[str, object]],
    position_rows: Iterable[dict[str, object]],
    broker_order_rows: Iterable[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    local_orders = _aggregate_order_rows(order_rows)
    broker_orders = _aggregate_order_rows(broker_order_rows or [])
    fills = _aggregate_fill_rows(fill_rows)
    positions = _aggregate_position_rows(position_rows)
    result: list[dict[str, object]] = []

    for intended in intended_rows:
        lot_id = _text(intended.get("strategy_lot_id"))
        stock_id = _text(intended.get("stock_id"))
        if not lot_id or not stock_id:
            continue
        target_qty = max(_as_int(intended.get("target_qty"), 0), 0)
        if target_qty <= 0:
            continue
        local_order = local_orders.get(lot_id, {})
        broker_order = broker_orders.get(lot_id, {})
        fill = fills.get(lot_id, {})
        position_qty = positions.get(lot_id, 0)

        local_filled_qty = max(
            _as_int(local_order.get("filled_qty"), 0),
            _as_int(fill.get("filled_qty"), 0),
            position_qty,
        )
        broker_filled_qty = _as_int(broker_order.get("filled_qty"), 0)
        filled_qty = min(max(local_filled_qty, broker_filled_qty), target_qty)
        local_active_qty = _as_int(local_order.get("active_qty"), 0)
        broker_active_qty = _as_int(broker_order.get("active_qty"), 0)
        active_qty = max(local_active_qty, broker_active_qty)
        sent_order_qty = max(
            _as_int(local_order.get("order_qty"), 0),
            _as_int(broker_order.get("order_qty"), 0),
            filled_qty + active_qty,
        )
        covered_qty = min(max(filled_qty + active_qty, sent_order_qty), target_qty)
        missing_qty = max(target_qty - filled_qty, 0)
        to_submit_qty = max(target_qty - covered_qty, 0)

        if to_submit_qty > 0 and filled_qty > 0:
            confirmation_status = "partial_bought_needs_submit"
        elif to_submit_qty > 0:
            confirmation_status = "not_submitted"
        elif missing_qty > 0 and active_qty > 0:
            confirmation_status = "sent_waiting_fill"
        elif filled_qty >= target_qty:
            confirmation_status = "filled"
        elif filled_qty > 0:
            confirmation_status = "partially_filled"
        elif active_qty > 0:
            confirmation_status = "sent_waiting_fill"
        else:
            confirmation_status = "covered_by_order" if sent_order_qty > 0 else "not_submitted"

        local_order_ids = list(local_order.get("order_ids", []))
        broker_order_ids = list(broker_order.get("order_ids", []))
        fill_order_ids = list(fill.get("fill_order_ids", []))
        result.append(
            {
                "strategy_lot_id": lot_id,
                "stock_id": stock_id,
                "stock_name": _text(intended.get("stock_name")) or stock_id,
                "basket_tag": _text(intended.get("basket_tag")) or "main",
                "target_qty": target_qty,
                "filled_qty": filled_qty,
                "active_order_qty": min(active_qty, max(target_qty - filled_qty, 0)),
                "missing_qty": missing_qty,
                "to_submit_qty": to_submit_qty,
                "confirmation_status": confirmation_status,
                "local_order_ids": "|".join(local_order_ids),
                "broker_order_ids": "|".join(broker_order_ids),
                "fill_order_ids": "|".join(fill_order_ids),
                "local_order_statuses": "|".join(local_order.get("statuses", [])),
                "broker_order_statuses": "|".join(broker_order.get("statuses", [])),
                "broker_custom_field": _text(intended.get("broker_custom_field"))
                or "|".join(local_order.get("broker_custom_fields", []))
                or "|".join(broker_order.get("broker_custom_fields", [])),
                "ambiguous": bool(fill.get("ambiguous")),
            }
        )
    return result


def summarize_repair_confirmation(rows: Iterable[dict[str, object]]) -> dict[str, object]:
    materialized = list(rows)
    intended_qty = sum(_as_int(row.get("target_qty"), 0) for row in materialized)
    filled_qty = sum(_as_int(row.get("filled_qty"), 0) for row in materialized)
    active_qty = sum(_as_int(row.get("active_order_qty"), 0) for row in materialized)
    missing_qty = sum(_as_int(row.get("missing_qty"), 0) for row in materialized)
    to_submit_qty = sum(_as_int(row.get("to_submit_qty"), 0) for row in materialized)
    bought_rows = [row for row in materialized if _as_int(row.get("filled_qty"), 0) > 0]
    active_rows = [
        row
        for row in materialized
        if _as_int(row.get("active_order_qty"), 0) > 0 and _as_int(row.get("missing_qty"), 0) > 0
    ]
    to_submit_rows = [row for row in materialized if _as_int(row.get("to_submit_qty"), 0) > 0]
    ambiguous_rows = [row for row in materialized if bool(row.get("ambiguous"))]
    return {
        "intended_count": len(materialized),
        "intended_qty": intended_qty,
        "bought_count": len(bought_rows),
        "filled_qty": filled_qty,
        "active_count": len(active_rows),
        "active_order_qty": active_qty,
        "missing_count": sum(1 for row in materialized if _as_int(row.get("missing_qty"), 0) > 0),
        "missing_qty": missing_qty,
        "to_submit_count": len(to_submit_rows),
        "to_submit_qty": to_submit_qty,
        "ambiguous_count": len(ambiguous_rows),
        "approval_required": to_submit_qty > 0,
        "approval_allowed": to_submit_qty > 0 and not ambiguous_rows,
    }


def render_repair_confirmation_markdown(
    *,
    trade_date: str,
    buy_source_trade_date: str,
    generated_at: str,
    email_to: str,
    rows: list[dict[str, object]],
    summary: dict[str, object],
) -> str:
    lines = [
        f"# SinoPac repair confirmation {trade_date}",
        "",
        "- policy_scope: `all_future_live_basket_buy_repairs`",
        f"- generated_at: `{generated_at}`",
        f"- buy_source_trade_date: `{buy_source_trade_date}`",
        f"- email_to: `{email_to}`",
        f"- intended: `{summary.get('intended_count')}` stocks / `{summary.get('intended_qty')}` shares",
        f"- bought_or_filled: `{summary.get('bought_count')}` stocks / `{summary.get('filled_qty')}` shares",
        f"- sent_waiting_fill: `{summary.get('active_count')}` stocks / `{summary.get('active_order_qty')}` shares",
        f"- not_yet_submitted_qty: `{summary.get('to_submit_qty')}`",
        f"- ambiguous_count: `{summary.get('ambiguous_count')}`",
        "",
        "請直接回覆這封 mail，表示你同意補下尚未送出的剩餘單。",
        "沒有收到你的回覆以前，不可以補下單；如果任何一列是 ambiguous，也不可以自動續下。",
        "",
        "回覆方式：",
        f"請直接在回信裡寫清楚 {trade_date} 這次要怎麼處理，例如同意補下尚未送出的剩餘單、不同意補下、只補某幾檔，或先停住。",
        "我會依照你的回信內容執行；如果內容不清楚、和報告不一致、或要求補下已成交/已送/狀態不明的項目，就停止並回報。",
        "",
        "補下範圍預設只限 repair_confirmation 報告中 not_submitted / to_submit_qty > 0 的項目；已成交、已送等待成交、狀態不明的不重送。",
        "",
    ]

    def add_table(title: str, filtered_rows: list[dict[str, object]]) -> None:
        lines.extend([f"## {title}", ""])
        if not filtered_rows:
            lines.extend(["None.", ""])
            return
        lines.append("| stock | target | filled | sent_waiting | not_submitted | status | order_ids |")
        lines.append("| --- | ---: | ---: | ---: | ---: | --- | --- |")
        for row in filtered_rows:
            order_ids = _text(row.get("broker_order_ids")) or _text(row.get("local_order_ids")) or _text(row.get("fill_order_ids"))
            lines.append(
                "| "
                f"{_text(row.get('stock_id'))} {_text(row.get('stock_name'))} | "
                f"{_as_int(row.get('target_qty'), 0)} | "
                f"{_as_int(row.get('filled_qty'), 0)} | "
                f"{_as_int(row.get('active_order_qty'), 0)} | "
                f"{_as_int(row.get('to_submit_qty'), 0)} | "
                f"{_text(row.get('confirmation_status'))} | "
                f"{order_ids} |"
            )
        lines.append("")

    add_table("買了什麼 / 已成交", [row for row in rows if _as_int(row.get("filled_qty"), 0) > 0])
    add_table(
        "已送但未完全成交",
        [
            row
            for row in rows
            if _as_int(row.get("active_order_qty"), 0) > 0 and _as_int(row.get("missing_qty"), 0) > 0
        ],
    )
    add_table("什麼還沒買 / 尚未送出", [row for row in rows if _as_int(row.get("to_submit_qty"), 0) > 0])
    add_table("完整明細", rows)
    return "\n".join(lines).rstrip() + "\n"


def now_iso(tz) -> str:
    return datetime.now(tz).isoformat()
