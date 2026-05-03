from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .calendar import load_trade_days
from .config import Settings, describe_live_submit_guard, ensure_auto_trading_live_enabled
from .live_order_chase import parse_hhmm, run_single_stock_chase
from .obsidian_sync import sync_obsidian_snapshot
from .paths import PROJECT_ROOT, auto_trading_dir_for
from .shioaji_client import login
from .state_store import SQLiteStateStore
from .time_utils import TAIPEI

TASK_NAME = "SinoPac2330BuyIntradayOdd0910"
ALLOWED_LIVE_ORDER_TEXT = "2330 / Buy / IntradayOdd / 1股 / 09:10 / 價格上限 2100"
TARGET_STOCK_ID = "2330"
TARGET_ACTION = "Buy"
TARGET_ORDER_LOT = "IntradayOdd"
TARGET_QUANTITY = 1
TARGET_PRICE_CAP = 2100.0
TARGET_START_TIME = "09:10"
TARGET_END_TIME = "13:20"
TARGET_EXCHANGE = "TSE"
CHECK_INTERVAL_SECONDS = 300
REPRICE_THRESHOLD_TICKS = 5
CUSTOM_PREFIX = "T23"

_ALLOWED_LIVE_GUARD_STATUS = {
    "allow_live_submit_disabled": "skipped_live_submit_disabled",
    "config_live_disabled": "skipped_config_live_disabled",
    "weekly_execution_disabled": "skipped_weekly_execution_disabled",
    "weekly_budget_missing": "skipped_weekly_budget_missing",
    "weekly_execution_week_mismatch": "skipped_weekly_execution_week_mismatch",
    "confirm_live_missing": "skipped_confirm_live_missing",
    "auto_trade_live_env_missing": "skipped_auto_trade_live_env_missing",
}


@dataclass(slots=True)
class AllowedLiveOrderTaskStatus:
    trade_date: str
    task_name: str
    status: str
    message: str
    matched_order_id: str = ""
    final_state: str = ""
    final_order_id: str = ""
    summary_path: str = ""


def _now() -> datetime:
    return datetime.now(TAIPEI)


def _normalize_text(value: object) -> str:
    return "".join(char.lower() for char in str(value or "") if char.isalnum())


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _parse_datetime_like(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        resolved = value
    else:
        text = str(value).strip()
        if not text:
            return None
        candidates = [text]
        if "T" in text:
            candidates.append(text.replace("T", " "))
        for candidate in candidates:
            try:
                resolved = datetime.fromisoformat(candidate)
                break
            except ValueError:
                resolved = None
        if resolved is None:
            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    resolved = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
        if resolved is None:
            return None
    if resolved.tzinfo is None:
        return resolved.replace(tzinfo=TAIPEI)
    return resolved.astimezone(TAIPEI)


def calendar_allows_live_order(trade_date: date, *, calendar_path: Path | None = None) -> tuple[bool, str]:
    trade_days, missing_warning, source_path = load_trade_days(calendar_path)
    if missing_warning or source_path is None or not trade_days:
        return False, "calendar_missing"
    return (trade_date in trade_days, "trade_day" if trade_date in trade_days else "non_trade_day")


def _trade_record_date(trade: Any) -> date | None:
    status = getattr(trade, "status", None)
    for field_name in ("order_datetime", "modified_time"):
        resolved = _parse_datetime_like(getattr(status, field_name, None))
        if resolved is not None:
            return resolved.date()
    return None


def matching_allowed_live_trade(trade: Any, *, trade_date: date) -> bool:
    contract = getattr(trade, "contract", None)
    order = getattr(trade, "order", None)
    if str(getattr(contract, "code", "")).strip() != TARGET_STOCK_ID:
        return False
    if "buy" not in _normalize_text(getattr(order, "action", "")):
        return False
    if "intradayodd" not in _normalize_text(getattr(order, "order_lot", "")):
        return False
    if _safe_int(getattr(order, "quantity", 0)) != TARGET_QUANTITY:
        return False
    record_date = _trade_record_date(trade)
    return record_date in {None, trade_date}


def find_existing_allowed_live_trade(trades: list[Any], *, trade_date: date) -> Any | None:
    for trade in trades:
        if matching_allowed_live_trade(trade, trade_date=trade_date):
            return trade
    return None


def _order_id(trade: Any) -> str:
    return str(getattr(getattr(trade, "order", None), "id", "") or "")


def _status_name(trade: Any) -> str:
    return str(getattr(getattr(trade, "status", None), "status", "") or "")


def _write_status_file(run_dir: Path, status: AllowedLiveOrderTaskStatus) -> Path:
    path = run_dir / "allowed_live_order_2330_task.json"
    path.write_text(json.dumps(asdict(status), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _record_status(
    store: SQLiteStateStore,
    *,
    trade_date: date,
    status: AllowedLiveOrderTaskStatus,
    stock_id: str = TARGET_STOCK_ID,
) -> Path:
    store.initialize()
    path = _write_status_file(store.run_dir, status)
    store.append_event(
        run_id=f"auto-{trade_date.isoformat()}",
        timestamp=_now().isoformat(),
        level="INFO" if status.status != "failed" else "ERROR",
        event_type="allowed_live_order_task",
        stock_id=stock_id,
        message=status.message,
        metadata=asdict(status),
    )
    return path


def _auto_enable_allowed_live_config_if_authorized(settings: Settings) -> tuple[bool, str]:
    if not bool(getattr(settings, "allow_live_submit", False)):
        return False, ""
    if os.getenv("AUTO_TRADE_LIVE") != "1":
        return False, ""

    project_root = Path(getattr(settings, "project_root", PROJECT_ROOT))
    changed, config_path = ensure_auto_trading_live_enabled(project_root / "config")
    auto_trading = getattr(settings, "auto_trading", None)
    if auto_trading is not None:
        try:
            setattr(auto_trading, "live_enabled", True)
        except (AttributeError, TypeError):
            pass
    return changed, str(config_path)


def _best_effort_obsidian_sync(settings: Settings, trade_date: date, *, event_summary: str) -> None:
    if trade_date != _now().date():
        return
    try:
        sync_obsidian_snapshot(settings, trade_date, event_summary=event_summary)
    except Exception:
        return


def run_allowed_live_order_task(
    settings: Settings | None = None,
    *,
    trade_date: date | None = None,
) -> AllowedLiveOrderTaskStatus:
    resolved_settings = settings or Settings.from_env()
    resolved_trade_date = trade_date or _now().date()
    run_dir = auto_trading_dir_for(resolved_trade_date)
    store = SQLiteStateStore(run_dir)

    allowed, reason = calendar_allows_live_order(resolved_trade_date)
    if not allowed:
        status = AllowedLiveOrderTaskStatus(
            trade_date=resolved_trade_date.isoformat(),
            task_name=TASK_NAME,
            status="skipped_non_trade_day" if reason == "non_trade_day" else "skipped_calendar_missing",
            message=f"Skipped allowed live order task: {reason}.",
        )
        _record_status(store, trade_date=resolved_trade_date, status=status)
        _best_effort_obsidian_sync(resolved_settings, resolved_trade_date, event_summary=status.message)
        return status

    allowed_live, live_reason = resolved_settings.evaluate_live_submit_guard(
        confirm_live=True,
        trade_date=resolved_trade_date,
    )
    if not allowed_live and live_reason == "config_live_disabled":
        config_changed, config_path = _auto_enable_allowed_live_config_if_authorized(resolved_settings)
        if config_changed:
            store.initialize()
            store.append_event(
                run_id=f"auto-{resolved_trade_date.isoformat()}",
                timestamp=_now().isoformat(),
                level="INFO",
                event_type="allowed_live_order_guard",
                stock_id=TARGET_STOCK_ID,
                message=(
                    "Auto-enabled auto_trading.live_enabled for the authorized guarded live order "
                    f"before evaluating live-submit guard: {config_path}"
                ),
                metadata={
                    "trade_date": resolved_trade_date.isoformat(),
                    "task_name": TASK_NAME,
                    "config_path": config_path,
                },
            )
        allowed_live, live_reason = resolved_settings.evaluate_live_submit_guard(
            confirm_live=True,
            trade_date=resolved_trade_date,
        )
    if not allowed_live:
        status = AllowedLiveOrderTaskStatus(
            trade_date=resolved_trade_date.isoformat(),
            task_name=TASK_NAME,
            status=_ALLOWED_LIVE_GUARD_STATUS.get(live_reason, "skipped_live_guard"),
            message=f"Skipped allowed live order task: {describe_live_submit_guard(live_reason)}",
        )
        _record_status(store, trade_date=resolved_trade_date, status=status)
        _best_effort_obsidian_sync(resolved_settings, resolved_trade_date, event_summary=status.message)
        return status

    try:
        api, _accounts = login(resolved_settings, simulation=False, fetch_contract=False)
        api.update_status(api.stock_account)
        existing_trade = find_existing_allowed_live_trade(list(api.list_trades()), trade_date=resolved_trade_date)
        if existing_trade is not None:
            matched_order_id = _order_id(existing_trade)
            status = AllowedLiveOrderTaskStatus(
                trade_date=resolved_trade_date.isoformat(),
                task_name=TASK_NAME,
                status="skipped_existing_order",
                message=(
                    "Skipped allowed live order task: an existing 2330 intraday odd-lot buy order "
                    f"was already found for {resolved_trade_date.isoformat()}."
                ),
                matched_order_id=matched_order_id,
                final_state=_status_name(existing_trade),
            )
            _record_status(store, trade_date=resolved_trade_date, status=status)
            _best_effort_obsidian_sync(resolved_settings, resolved_trade_date, event_summary=status.message)
            return status

        chase_result = run_single_stock_chase(
            settings=resolved_settings,
            stock_id=TARGET_STOCK_ID,
            exchange=TARGET_EXCHANGE,
            action=TARGET_ACTION,
            order_lot=TARGET_ORDER_LOT,
            quantity=TARGET_QUANTITY,
            price_cap=TARGET_PRICE_CAP,
            live=True,
            submit=True,
            confirm_live=True,
            start_time=parse_hhmm(TARGET_START_TIME),
            end_time=parse_hhmm(TARGET_END_TIME),
            check_interval_seconds=CHECK_INTERVAL_SECONDS,
            reprice_threshold_ticks=REPRICE_THRESHOLD_TICKS,
            custom_prefix=CUSTOM_PREFIX,
        )
        status = AllowedLiveOrderTaskStatus(
            trade_date=resolved_trade_date.isoformat(),
            task_name=TASK_NAME,
            status="submitted",
            message=(
                "Allowed live order task executed: "
                f"{ALLOWED_LIVE_ORDER_TEXT}, final_state={chase_result.final_state}."
            ),
            final_state=chase_result.final_state,
            final_order_id=chase_result.final_order_id,
            summary_path=str(chase_result.summary_path),
        )
        _record_status(store, trade_date=resolved_trade_date, status=status)
        _best_effort_obsidian_sync(resolved_settings, resolved_trade_date, event_summary=status.message)
        return status
    except Exception as exc:
        status = AllowedLiveOrderTaskStatus(
            trade_date=resolved_trade_date.isoformat(),
            task_name=TASK_NAME,
            status="failed",
            message=f"Allowed live order task failed: {exc}",
        )
        _record_status(store, trade_date=resolved_trade_date, status=status)
        _best_effort_obsidian_sync(resolved_settings, resolved_trade_date, event_summary=status.message)
        return status
