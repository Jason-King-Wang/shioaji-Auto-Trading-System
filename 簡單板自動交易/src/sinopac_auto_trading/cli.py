from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time as time_module
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable
import xml.etree.ElementTree as ET

from .accounting import (
    build_excluded_positions_rows,
    build_pnl_snapshot,
    build_positions_rows_from_fills,
    compute_sell_fill_stats,
    normalize_fill_side,
)
from .allowed_live_order import (
    TARGET_STOCK_ID as ALLOWED_LIVE_ORDER_TARGET_STOCK_ID,
    TASK_NAME as ALLOWED_LIVE_ORDER_TASK_NAME,
    run_allowed_live_order_task,
)
from .basket import (
    DEFAULT_BASKET_TAG,
    basket_tag_from_strategy_lot_id,
    broker_custom_field_for_strategy_lot,
    normalize_basket_tag,
    strategy_lot_id_for,
)
from .benchmark import BenchmarkSnapshot
from .broker_adapter import FakeBrokerAdapter, ManagedOrderSnapshot, ShioajiSinoPacBrokerAdapter
from .calendar import load_trade_days, resolve_week_trade_plan
from .config import (
    Settings,
    describe_live_submit_guard,
    set_auto_trading_weekly_execution,
    weekly_execution_week_id_for,
)
from .finalizer import finalize_selection
from .live_order_chase import parse_hhmm, run_single_stock_chase
from .ledger import load_week_custom_field_lot_lookup, load_week_lot_ledger, load_week_order_id_lot_lookup
from .llm_selection_workflow import load_llm_decision_items, write_final_list_csv, write_llm_review_bundle
from .live_smoke_test import run_live_smoke_test
from .obsidian_sync import sync_obsidian_snapshot
from .order_engine import ManagedOrder, QuoteState, current_buy_mode, current_mode_target_price, plan_order_action
from .paths import (
    DATA_DIR,
    PROJECT_ROOT,
    auto_trading_dir_for,
    current_html_report_path,
    current_snapshot_json_path,
    daily_html_report_path,
    daily_note_path,
    dated_snapshot_json_path,
    dated_html_report_path,
    ensure_runtime_directories,
    input_dir_for,
    llm_selection_decisions_path,
    weekly_html_report_path,
    weekly_note_path,
    weekly_snapshot_json_path,
)
from .providers import (
    AbLlmPreselectJsonSelectionProvider,
    GenericCsvSelectionProvider,
    ManualCsvSelectionProvider,
    MockSelectionProvider,
    StockModelVaultExportSelectionProvider,
)
from .quote_provider import load_fake_quotes_csv
from .model_order_interface import command_model_orders
from .quick_simulator import command_simple_buy, command_simple_order, command_simple_sell, command_simulate_buy
from .report_writer import (
    PROVIDER_DISPLAY_LABELS,
    _normalize_display_text,
    _action_with_inline_label,
    _mapped_with_inline_label,
    _status_with_inline_label,
    render_daily_report,
    render_weekly_settlement,
)
from .repair_confirmation import (
    build_repair_confirmation_rows,
    now_iso as repair_confirmation_now_iso,
    render_repair_confirmation_markdown,
    summarize_repair_confirmation,
)
from .risk_controls import (
    affordable_buy_qty,
    estimate_buy_order_cost,
    live_buy_quote_gate,
    parse_quote_timestamp,
    quote_is_stale,
)
from .selection_provider import SelectionItem, SelectionProvider
from .sell_policy import (
    StrategyPosition,
    SellQuote,
    basket_recommendation,
    basket_recommendations_by_tag,
    effective_basket_sell_signal,
    evaluate_sell_decision,
    live_sell_submission_gate,
)
from .shioaji_client import describe_account, login, resolve_stock_contract, submit_stock_order
from .sizing import secondary_add_active_for_trade_date, size_selection
from .state_store import SQLiteStateStore
from .tick import tick_distance
from .time_utils import TAIPEI


def _today() -> date:
    return datetime.now(TAIPEI).date()


def _parse_trade_date(raw: str | None) -> date:
    return date.fromisoformat(raw) if raw else _today()


A_PRESELECT_CONFIRMATION_REMINDER = "10點後才會執行永豐自動交易的確定A預選工作。"
A_PRESELECT_CONFIRMATION_POLICY = "trade_date_10:00_taipei"


def _a_preselect_confirmation_applies(settings: Settings) -> bool:
    providers = getattr(settings, "providers", None)
    return str(getattr(providers, "active", "")).strip() == "ab_llm_preselect_json"


def _a_preselect_confirmation_start_time(settings: Settings) -> time:
    auto_trading = getattr(settings, "auto_trading", None)
    raw = str(getattr(auto_trading, "a_preselect_confirmation_start_time", "10:00") or "10:00").strip()
    try:
        return parse_hhmm(raw)
    except ValueError as exc:
        raise RuntimeError(
            "auto_trading.a_preselect_confirmation_start_time must use HH:MM format, for example 10:00."
        ) from exc


def _a_preselect_confirmation_start_at(settings: Settings, trade_date: date) -> datetime:
    return datetime.combine(trade_date, _a_preselect_confirmation_start_time(settings), tzinfo=TAIPEI)


def _parse_iso_datetime_or_none(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=TAIPEI)
    return parsed.astimezone(TAIPEI)


def _a_preselect_confirmation_time_status(
    settings: Settings,
    trade_date: date,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    if not _a_preselect_confirmation_applies(settings):
        return {"required": False, "ready": True, "status": "not_required"}
    resolved_now = now or datetime.now(TAIPEI)
    start_at = _a_preselect_confirmation_start_at(settings, trade_date)
    if resolved_now < start_at:
        return {
            "required": True,
            "ready": False,
            "status": "pending_until_a_preselect_confirmation_start_time",
            "reason": "before_a_preselect_confirmation_start_time",
            "start_at": start_at.isoformat(timespec="seconds"),
            "now": resolved_now.isoformat(timespec="seconds"),
            "reminder": A_PRESELECT_CONFIRMATION_REMINDER,
        }
    return {
        "required": True,
        "ready": True,
        "status": "a_preselect_confirmation_time_ready",
        "start_at": start_at.isoformat(timespec="seconds"),
        "now": resolved_now.isoformat(timespec="seconds"),
        "reminder": A_PRESELECT_CONFIRMATION_REMINDER,
    }


def _a_preselect_sizing_confirmation_status(
    settings: Settings,
    trade_date: date,
    state_data: dict[str, object],
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    time_status = _a_preselect_confirmation_time_status(settings, trade_date, now=now)
    if not time_status.get("required"):
        return time_status
    if not time_status.get("ready"):
        return time_status
    start_at = _a_preselect_confirmation_start_at(settings, trade_date)
    confirmed_at = _parse_iso_datetime_or_none(state_data.get("a_preselect_confirmed_at"))
    if confirmed_at is None or confirmed_at < start_at:
        return {
            **time_status,
            "ready": False,
            "status": "a_preselect_confirmation_missing_for_sizing",
            "reason": "sizing_not_confirmed_after_a_preselect_start_time",
            "confirmed_at": confirmed_at.isoformat(timespec="seconds") if confirmed_at else "",
        }
    return {
        **time_status,
        "ready": True,
        "status": "a_preselect_confirmed_for_sizing",
        "confirmed_at": confirmed_at.isoformat(timespec="seconds"),
    }


def _provider_from_settings(settings: Settings) -> SelectionProvider:
    active = settings.providers.active
    options = settings.provider_options()
    if active == "ab_llm_preselect_json":
        preselect_dir = options.get("preselect_dir")
        if not preselect_dir:
            raise RuntimeError("providers.ab_llm_preselect_json.preselect_dir is required.")
        preselect_root = Path(preselect_dir)
        if not preselect_root.is_absolute():
            preselect_root = settings.project_root / preselect_dir
        daily_output_dir = options.get("daily_output_dir")
        daily_output_root: Path | None = None
        if daily_output_dir:
            daily_output_root = Path(daily_output_dir)
            if not daily_output_root.is_absolute():
                daily_output_root = settings.project_root / daily_output_dir
        return AbLlmPreselectJsonSelectionProvider(
            preselect_dir=preselect_root,
            daily_output_dir=daily_output_root,
            use_a_preselect_as_final_list=bool(options.get("use_a_preselect_as_final_list", True)),
        )
    if active == "manual_csv":
        input_root = options.get("input_root")
        resolved = settings.project_root / input_root if input_root else None
        return ManualCsvSelectionProvider(resolved)
    if active == "generic_csv":
        return GenericCsvSelectionProvider(
            preselect_path=options.get("preselect_path"),
            final_list_path=options.get("final_list_path"),
        )
    if active == "stock_model_vault_export":
        export_dir = options.get("export_dir")
        if not export_dir:
            raise RuntimeError("providers.stock_model_vault_export.export_dir is required.")
        return StockModelVaultExportSelectionProvider(
            export_dir=export_dir,
            preselect_filename=options.get("preselect_filename", "auto_trade_preselect.csv"),
            final_list_filename=options.get("final_list_filename", "auto_trade_final_list.csv"),
        )
    if active == "mock_provider":
        return MockSelectionProvider()
    raise RuntimeError(f"Unsupported provider: {active}")


def _load_fake_quote_provider(settings: Settings):
    example_path = settings.project_root / "examples" / "fake_quotes_example.csv"
    return load_fake_quotes_csv(example_path) if example_path.exists() else None


def _estimated_prices_for_finalize(
    items: list[SelectionItem],
    quote_provider,
    *,
    prefer_reference_price: bool = False,
) -> tuple[dict[str, float], list[str]]:
    estimated_prices: dict[str, float] = {}
    unresolved_stock_ids: list[str] = []
    for item in items:
        snapshot = quote_provider.get_snapshot(item.stock_id) if quote_provider else None
        if item.reference_price is not None and item.reference_price > 0:
            if prefer_reference_price or not (snapshot and snapshot.last_price > 0):
                estimated_prices[item.stock_id] = float(item.reference_price)
                continue
        if snapshot and snapshot.last_price > 0:
            estimated_prices[item.stock_id] = snapshot.last_price
            continue
        estimated_prices[item.stock_id] = 10.0
        unresolved_stock_ids.append(item.stock_id)
    return estimated_prices, unresolved_stock_ids


def _run_id(trade_date: date) -> str:
    return f"auto-{trade_date.isoformat()}"


def _week_id(plan) -> str:
    if not plan.week_trade_days:
        return f"{plan.anchor_date.isoformat()}_{plan.anchor_date.isoformat()}"
    return f"{plan.week_trade_days[0].isoformat()}_{plan.week_trade_days[-1].isoformat()}"


def _selection_snapshot_key(stock_id: object, basket_tag: object = DEFAULT_BASKET_TAG) -> str:
    return f"{str(stock_id).strip()}::{normalize_basket_tag(basket_tag)}"


def _provider_final_list_origin(provider_name: object) -> str:
    normalized = str(provider_name).strip()
    if normalized == "manual_csv":
        return "manual_final_list"
    if normalized == "ab_llm_preselect_json":
        return "same_day_a_preselect_final_list"
    return "provider_final_list"


def _selection_rows(items: list[SelectionItem], provider_name: str) -> list[dict[str, object]]:
    return [
        {
            "stock_id": item.stock_id,
            "stock_name": item.stock_name,
            "source": item.source,
            "basket_tag": item.normalized_basket_tag(),
            "source_weight": item.normalized_source_weight(),
            "a_flag": item.a_flag,
            "b_flag": item.b_flag,
            "role_level": item.role_level,
            "theme": item.theme,
            "model_rank": item.model_rank,
            "model_score": item.model_score,
            "user_priority": item.user_priority,
            "target_qty": item.target_qty,
            "reference_price": item.reference_price,
            "provider_name": provider_name,
            "note": item.note,
        }
        for item in items
    ]


def _write_selection_csv(path: Path, items: list[SelectionItem]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "stock_id",
        "stock_name",
        "source",
        "basket_tag",
        "source_weight",
        "a_flag",
        "b_flag",
        "role_level",
        "theme",
        "catalyst_flag",
        "model_rank",
        "model_score",
        "user_priority",
        "force_include",
        "force_exclude",
        "target_weight",
        "target_qty",
        "reference_price",
        "note",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in items:
            writer.writerow(asdict(item))


def _manual_trade_dir(trade_date: date) -> Path:
    path = input_dir_for(trade_date)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _best_effort_obsidian_sync(
    settings: Settings,
    trade_date: date,
    *,
    include_live_status: bool = False,
    event_summary: str | None = None,
) -> None:
    try:
        written = sync_obsidian_snapshot(
            settings,
            trade_date,
            include_live_status=include_live_status,
            event_summary=event_summary,
        )
    except Exception as exc:
        print(f"obsidian_sync_warning: {exc}", file=sys.stderr)
        return
    for path in written:
        print(f"obsidian_synced: {path}")


def _update_run_state(
    store: SQLiteStateStore,
    trade_date: date,
    *,
    status: str,
    **fields: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "trade_date": trade_date.isoformat(),
        "run_id": _run_id(trade_date),
        "status": status,
        "updated_at": datetime.now(TAIPEI).isoformat(timespec="seconds"),
    }
    payload.update(fields)
    return store.merge_state_json(payload)


def _repair_confirmation_required_payload(
    *,
    required: bool,
    reason: str,
    email_to: str = "ops@example.com",
) -> dict[str, object]:
    return {
        "required": required,
        "policy_scope": "all_future_live_basket_buy_repairs",
        "reason": reason,
        "email_to": email_to,
        "command": "python run.py repair_confirmation --trade-date YYYY-MM-DD --live --email-to ops@example.com",
        "mail_requirement": "email the report and wait for the user's reply before submitting any not-yet-submitted remainder",
        "continue_command": "$env:AUTO_TRADE_LIVE='1'; python run.py buy_loop --trade-date YYYY-MM-DD --live --confirm-live",
    }


def _record_live_buy_repair_required(
    *,
    trade_date: date,
    reason: str,
    error: str = "",
) -> None:
    run_dir = auto_trading_dir_for(trade_date)
    store = SQLiteStateStore(run_dir)
    store.initialize()
    payload = _repair_confirmation_required_payload(required=True, reason=reason)
    if error:
        payload["error"] = error
    store.merge_state_json({"repair_confirmation_required": payload})
    store.append_event(
        run_id=_run_id(trade_date),
        timestamp=datetime.now(TAIPEI).isoformat(),
        level="WARNING",
        event_type="repair_confirmation_required",
        message=(
            "Live basket buy repair requires broker/local reconciliation, email confirmation, "
            "and user reply before continuation."
        ),
        metadata=payload,
    )


def _live_buy_repair_required_reason(
    *,
    requested_live: bool,
    can_go_live: bool,
    live_guard: str,
    order_rows: Iterable[dict[str, object]],
) -> str:
    if not requested_live:
        return ""
    if not can_go_live:
        return f"live_gate:{live_guard or 'blocked'}"

    problems: list[str] = []
    for row in order_rows:
        stock_id = str(row.get("stock_id", "")).strip() or "unknown"
        status = str(row.get("status", "")).strip().lower()
        action = str(row.get("action", "")).strip().lower()
        note = str(row.get("note", "")).strip().lower()
        target_qty = _as_int(row.get("target_qty"), 0)
        filled_qty = _as_int(row.get("filled_qty"), 0)
        active_qty = _as_int(row.get("active_order_qty"), 0)
        covered_qty = min(max(filled_qty + active_qty, filled_qty), target_qty)

        if any(token in status for token in ("fail", "reject", "error")):
            problems.append(f"{stock_id}:{status or 'failed'}")
            continue
        if status.startswith("blocked_") or note.startswith("blocked_"):
            problems.append(f"{stock_id}:{status or note}")
            continue
        if covered_qty < target_qty and status != "secondary_add_waiting_trade_day" and action != "done":
            problems.append(f"{stock_id}:not_fully_submitted")

    if not problems:
        return ""
    shown = ",".join(problems[:12])
    omitted = len(problems) - 12
    if omitted > 0:
        shown = f"{shown},+{omitted}_more"
    return f"live_buy_incomplete:{shown}"


def _workflow_status_note_path(trade_date: date) -> Path:
    return daily_note_path(trade_date).with_name(f"{trade_date.isoformat()}_workflow_status.md")


def _resolved_provider_name(
    *,
    state_data: dict[str, object] | None = None,
    settings: Settings | None = None,
) -> str:
    provider_name = str((state_data or {}).get("provider_name", "")).strip()
    if provider_name:
        return provider_name
    if settings is not None:
        return str(getattr(settings.providers, "active", "")).strip()
    return ""


def _first_trade_day_for_plan(plan) -> date | None:
    week_trade_days = list(getattr(plan, "week_trade_days", []) or [])
    return week_trade_days[0] if week_trade_days else None


def _allow_buy_after_first_trade_day(settings: Settings | None) -> bool:
    if settings is None:
        return True
    auto = getattr(settings, "auto_trading", None)
    return bool(getattr(auto, "allow_buy_after_first_trade_day", True))


def _effective_buy_cutoff_day(settings: Settings | None, plan) -> date | None:
    if _allow_buy_after_first_trade_day(settings):
        return getattr(plan, "buy_cutoff_day", None)
    return _first_trade_day_for_plan(plan)


def _buy_loop_source_trade_date(settings: Settings, trade_date: date, plan) -> date:
    first_trade_day = _first_trade_day_for_plan(plan)
    if first_trade_day is None:
        return trade_date
    auto = getattr(settings, "auto_trading", None)
    if (
        hasattr(auto, "allow_buy_after_first_trade_day")
        and trade_date > first_trade_day
        and _allow_buy_after_first_trade_day(settings)
    ):
        return first_trade_day
    return trade_date


def _buy_loop_skip_reason(settings: Settings, trade_date: date, plan) -> str:
    if getattr(plan, "last_trade_day", None) == trade_date:
        return "last_trade_day"
    if not _allow_buy_after_first_trade_day(settings):
        first_trade_day = _first_trade_day_for_plan(plan)
        if first_trade_day is not None and trade_date > first_trade_day:
            return "after_first_trade_day_buy_chase_disabled"
    buy_cutoff_day = getattr(plan, "buy_cutoff_day", None)
    if buy_cutoff_day and trade_date > buy_cutoff_day:
        return "after_buy_cutoff_day"
    return ""


def _buy_loop_existing_order_rows(plan, trade_date: date, source_trade_date: date) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for day in list(getattr(plan, "week_trade_days", []) or [trade_date]):
        if source_trade_date <= day <= trade_date:
            rows.extend(_read_csv_rows(auto_trading_dir_for(day) / "orders.csv"))
    return rows


def _already_bought_qty_by_lot(trade_date: date) -> dict[str, int]:
    bought_qty_by_lot: dict[str, int] = {}
    for row in load_week_lot_ledger(trade_date):
        strategy_lot_id = str(row.get("strategy_lot_id", "")).strip()
        if not strategy_lot_id:
            continue
        bought_qty = max(
            _as_int(row.get("buy_fill_qty"), 0),
            _as_int(row.get("closing_qty"), 0),
        )
        if bought_qty > 0:
            bought_qty_by_lot[strategy_lot_id] = max(bought_qty_by_lot.get(strategy_lot_id, 0), bought_qty)
    return bought_qty_by_lot


def _workflow_status_rows(
    *,
    trade_date: date,
    run_dir: Path,
    input_dir: Path,
    plan,
    state_data: dict[str, object] | None = None,
    settings: Settings | None = None,
) -> list[dict[str, str]]:
    week_start = plan.week_trade_days[0] if plan.week_trade_days else trade_date
    week_end = plan.week_trade_days[-1] if plan.week_trade_days else trade_date

    steps: list[tuple[str, Path | None, str]] = [
        ("prepare_week", input_dir / "auto_trade_preselect.csv", "手動 / provider 的 preselect 檔已就緒"),
        ("prepare_llm_selection", input_dir / "llm_selection_review_payload.json", "LLM review payload 已就緒"),
        ("llm_decisions", input_dir / "llm_selection_decisions.json", "LLM decisions 檔已保存"),
        ("apply_llm_selection", input_dir / "auto_trade_final_list.csv", "已從 review 產出 final_list"),
        ("track_until_final", run_dir / "quote_snapshots.csv", "已擷取 quote snapshots"),
        ("finalize", run_dir / "sizing.csv", "已產出 sizing.csv"),
        ("buy_loop", run_dir / "orders.csv", "已產出 orders.csv"),
        ("fills", run_dir / "fills.csv", "已產出 fills.csv"),
        ("positions", run_dir / "positions.csv", "已產出 positions.csv"),
        ("excluded_positions", run_dir / "excluded_positions.csv", "已產出 excluded_positions.csv"),
        ("pnl_snapshots", run_dir / "pnl_snapshots.csv", "已產出 pnl_snapshots.csv"),
        ("post_guarded_order_check", run_dir / "post_guarded_order_check.json", "已記錄受保護下單後檢查"),
        ("sell_loop_readiness", run_dir / "sell_loop_readiness.json", "已記錄賣出就緒狀態"),
        ("sell_loop", run_dir / "sell_decisions.csv", "已產出 sell_decisions.csv"),
        ("render_report", daily_html_report_path(trade_date), "已產出每日 HTML 儀表板"),
        ("workflow_status", _workflow_status_note_path(trade_date), "已產出工作流筆記"),
        ("settle_week", weekly_html_report_path(week_start, week_end), "已產出每週 HTML 摘要"),
    ]

    rows: list[dict[str, str]] = []
    for step, path, check in steps:
        exists = bool(path and path.exists())
        rows.append(
            {
                "step": step,
                "status": "done" if exists else "pending",
                "check": check,
                "path": str(path) if path else "",
            }
        )

    provider_name = _resolved_provider_name(state_data=state_data, settings=settings)
    if provider_name == "ab_llm_preselect_json":
        refresh_details = (
            _ab_same_day_source_refresh_details(
                trade_date=trade_date,
                settings=settings,
                run_dir=run_dir,
                input_dir=input_dir,
            )
            if settings is not None
            else {"prepare_week": False, "finalize": False, "trigger_artifacts": ""}
        )
        for row in rows:
            if row["step"] in {"prepare_llm_selection", "llm_decisions"}:
                row["status"] = "done"
                row["check"] = "direct A 預選 provider 不需要這一步"
            if row["step"] == "apply_llm_selection":
                row["check"] = "direct A 預選 provider 的 final_list artifact 已就緒"
            if row["step"] == "prepare_week" and refresh_details.get("prepare_week", False):
                artifacts = str(refresh_details.get("trigger_artifacts", "")).strip()
                row["status"] = "pending"
                row["check"] = (
                    f"同日 A 來源比本地預選產物更新（{artifacts}）"
                    if artifacts
                    else "同日 A 來源比本地預選產物更新"
                )
            if row["step"] == "finalize" and refresh_details.get("finalize", False):
                row["status"] = "pending"
                if refresh_details.get("prepare_week", False):
                    row["check"] = "同日 A 來源刷新後，finalize 前需要先重跑 prepare_week"
                else:
                    artifacts = str(refresh_details.get("trigger_artifacts", "")).strip()
                    row["check"] = (
                        f"同日 A 來源比本地定稿產物更新（{artifacts}）"
                        if artifacts
                        else "同日 A 來源比本地定稿產物更新"
                    )

    today_submission_status = str((state_data or {}).get("today_new_order_submission_status", "")).strip()
    sell_readiness_reason = str((state_data or {}).get("sell_loop_readiness_blocking_reason", "")).strip()
    sell_readiness_positions = _as_int((state_data or {}).get("sell_loop_readiness_positions_count"), 0)
    if today_submission_status == "no_auto_new_buy_paths_remaining_today":
        for row in rows:
            if row["status"] != "pending":
                continue
            if row["step"] == "track_until_final" and sell_readiness_reason == "no_strategy_positions" and sell_readiness_positions <= 0:
                row["status"] = "closed"
                row["check"] = "今天已沒有可執行的自動買單路徑或策略部位，quote tracking 不再有實際作用"
            elif row["step"] == "buy_loop":
                row["status"] = "closed"
                row["check"] = "今天的自動新買單視窗已關閉"
            elif row["step"] in {"fills", "positions", "pnl_snapshots"} and sell_readiness_reason == "no_strategy_positions" and sell_readiness_positions <= 0:
                row["status"] = "closed"
                row["check"] = "今天沒有建立任何策略成交或部位，因此這個 artifact 不會出現"
    return rows


def _ab_llm_preselect_source_path(
    *,
    trade_date: date,
    settings: Settings | None = None,
) -> Path:
    def configured_root() -> Path:
        if settings is not None:
            options = settings.providers.options("ab_llm_preselect_json")
            configured = options.get("preselect_dir")
            if configured:
                root = Path(str(configured))
                if not root.is_absolute():
                    root = settings.project_root / root
                return root
            return settings.project_root / "data" / "ab_llm_preselect"
        return Path("data") / "ab_llm_preselect"

    def json_date(value: object) -> date | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None

    def targets_trade_date(payload: dict[str, object]) -> bool:
        for key in ("target_trade_date", "source_target_trade_date"):
            if json_date(payload.get(key)) == trade_date:
                return True
        return False

    def has_target_trade_date(payload: dict[str, object]) -> bool:
        return any(json_date(payload.get(key)) is not None for key in ("target_trade_date", "source_target_trade_date"))

    root = configured_root()
    exact_path = root / f"{trade_date.isoformat()}.json"
    if exact_path.exists():
        try:
            payload = json.loads(exact_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return exact_path
        if isinstance(payload, dict) and (targets_trade_date(payload) or not has_target_trade_date(payload)):
            return exact_path

    candidates: list[tuple[float, Path]] = []
    candidate_paths = (
        [path for path in sorted(root.glob("*.json")) if path.name.lower() != "latest.json"]
        if root.exists()
        else []
    )
    latest_path = root / "latest.json"
    if latest_path.exists():
        candidate_paths.append(latest_path)
    for path in candidate_paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or not targets_trade_date(payload):
            continue
        candidates.append((_path_mtime(path) or 0.0, path))
    if candidates:
        dated_candidates = [item for item in candidates if item[1].name.lower() != "latest.json"]
        return max(dated_candidates or candidates, key=lambda item: (item[0], item[1].name))[1]

    return exact_path


def _path_mtime(path: Path) -> float | None:
    try:
        if not path.exists():
            return None
        return path.stat().st_mtime
    except OSError:
        return None


def _path_modified_at_text(path: Path) -> str:
    modified_at = _path_mtime(path)
    if modified_at is None:
        return ""
    return datetime.fromtimestamp(modified_at, TAIPEI).isoformat(timespec="seconds")


def _artifacts_need_refresh(source_path: Path, artifacts: list[Path]) -> bool:
    source_mtime = _path_mtime(source_path)
    if source_mtime is None:
        return False
    for artifact in artifacts:
        artifact_mtime = _path_mtime(artifact)
        if artifact_mtime is None or artifact_mtime < source_mtime:
            return True
    return False


def _artifact_refresh_names(source_path: Path, artifacts: list[tuple[str, Path]]) -> list[str]:
    source_mtime = _path_mtime(source_path)
    if source_mtime is None:
        return []
    names: list[str] = []
    for name, artifact in artifacts:
        artifact_mtime = _path_mtime(artifact)
        if artifact_mtime is None or artifact_mtime < source_mtime:
            names.append(name)
    return names


def _ab_same_day_source_refresh_details(
    *,
    trade_date: date,
    settings: Settings,
    run_dir: Path | None = None,
    input_dir: Path | None = None,
) -> dict[str, object]:
    active_provider = str(getattr(settings.providers, "active", "ab_llm_preselect_json")).strip()
    if active_provider != "ab_llm_preselect_json":
        return {"prepare_week": False, "finalize": False}

    source_path = _ab_llm_preselect_source_path(trade_date=trade_date, settings=settings)
    resolved_run_dir = run_dir or auto_trading_dir_for(trade_date)
    resolved_input_dir = input_dir or input_dir_for(trade_date)
    prepare_artifacts = [
        ("preselect.csv", resolved_run_dir / "preselect.csv"),
        ("auto_trade_preselect.csv", resolved_input_dir / "auto_trade_preselect.csv"),
    ]
    finalize_artifacts = [
        ("auto_trade_final_list.csv", resolved_input_dir / "auto_trade_final_list.csv"),
        ("sizing.csv", resolved_run_dir / "sizing.csv"),
    ]
    prepare_artifact_names = _artifact_refresh_names(source_path, prepare_artifacts)
    finalize_artifact_names = _artifact_refresh_names(source_path, finalize_artifacts)
    prepare_week = bool(prepare_artifact_names)
    finalize = bool(finalize_artifact_names)
    if prepare_week:
        finalize = True
        finalize_artifact_names = finalize_artifact_names or [name for name, _ in finalize_artifacts]

    if prepare_week:
        trigger_status = "same_day_source_newer_than_local_preselect_artifacts"
        trigger_artifacts = ", ".join(prepare_artifact_names)
        trigger_note = (
            f"同日 A 預選來源比本地 preselect 產物更新，因此 refresh_dashboard 需重跑 prepare_week，並連帶重跑 finalize；受影響檔案：{trigger_artifacts}。"
        )
    elif finalize:
        trigger_status = "same_day_source_newer_than_local_finalize_artifacts"
        trigger_artifacts = ", ".join(finalize_artifact_names)
        trigger_note = (
            f"同日 A 預選來源比本地 finalize 產物更新，因此 refresh_dashboard 需重跑 finalize；受影響檔案：{trigger_artifacts}。"
        )
    else:
        trigger_status = ""
        trigger_artifacts = ""
        trigger_note = ""

    return {
        "prepare_week": prepare_week,
        "finalize": finalize,
        "trigger_status": trigger_status,
        "trigger_artifacts": trigger_artifacts,
        "trigger_note": trigger_note,
    }


def _ab_same_day_source_refresh_flags(
    *,
    trade_date: date,
    settings: Settings,
    run_dir: Path | None = None,
    input_dir: Path | None = None,
) -> dict[str, bool]:
    details = _ab_same_day_source_refresh_details(
        trade_date=trade_date,
        settings=settings,
        run_dir=run_dir,
        input_dir=input_dir,
    )
    return {
        "prepare_week": bool(details.get("prepare_week", False)),
        "finalize": bool(details.get("finalize", False)),
    }




def _selection_source_summary(
    *,
    trade_date: date,
    provider_name: str,
    preselect_count: object = 0,
    final_list_count: object = 0,
    settings: Settings | None = None,
) -> dict[str, str]:
    return _selection_source_summary_clean(
        trade_date=trade_date,
        provider_name=provider_name,
        preselect_count=preselect_count,
        final_list_count=final_list_count,
        settings=settings,
    )


def _today_ordering_summary(
    *,
    guarded_effective_recommendation: object = "",
    selection_source_status: object = "",
    trade_date: date | None = None,
    buy_cutoff_day: date | None = None,
    last_trade_day: date | None = None,
) -> dict[str, str]:
    return _today_ordering_summary_clean(
        guarded_effective_recommendation=guarded_effective_recommendation,
        selection_source_status=selection_source_status,
        trade_date=trade_date,
        buy_cutoff_day=buy_cutoff_day,
        last_trade_day=last_trade_day,
    )


def _today_ordering_conflict_summary(
    *,
    selection_source_status: object = "",
    trade_date: date | None = None,
    buy_cutoff_day: date | None = None,
    last_trade_day: date | None = None,
) -> dict[str, str]:
    return _today_ordering_conflict_summary_clean(
        selection_source_status=selection_source_status,
        trade_date=trade_date,
        buy_cutoff_day=buy_cutoff_day,
        last_trade_day=last_trade_day,
    )


def _today_ordering_conflict_resolution_summary(
    *,
    today_ordering_conflict_status: object = "",
    trade_date: date | None = None,
    next_trade_day: date | None = None,
) -> dict[str, str]:
    return _today_ordering_conflict_resolution_summary_clean(
        today_ordering_conflict_status=today_ordering_conflict_status,
        trade_date=trade_date,
        next_trade_day=next_trade_day,
    )


def _today_new_order_submission_summary(
    *,
    today_ordering_status: object = "",
) -> dict[str, object]:
    return _today_new_order_submission_summary_clean(
        today_ordering_status=today_ordering_status,
    )


def _selection_source_summary_clean(
    *,
    trade_date: date,
    provider_name: str,
    preselect_count: object = 0,
    final_list_count: object = 0,
    settings: Settings | None = None,
) -> dict[str, str]:
    if str(provider_name).strip() != "ab_llm_preselect_json":
        return {}

    source_path = _ab_llm_preselect_source_path(trade_date=trade_date, settings=settings)
    display_path = source_path
    if settings is not None:
        try:
            display_path = source_path.relative_to(settings.project_root)
        except ValueError:
            display_path = source_path
    source_path_text = display_path.as_posix()
    source_exists = source_path.exists()
    source_last_modified = ""
    if source_exists:
        try:
            source_last_modified = datetime.fromtimestamp(source_path.stat().st_mtime, TAIPEI).isoformat(
                timespec="seconds"
            )
        except OSError:
            source_last_modified = ""

    refresh_flags = {"prepare_week": False, "finalize": False}
    if source_exists and settings is not None:
        refresh_flags = _ab_same_day_source_refresh_flags(trade_date=trade_date, settings=settings)

    resolved_preselect_count = _as_int(preselect_count, 0)
    resolved_final_count = _as_int(final_list_count, 0)

    if source_exists and refresh_flags.get("prepare_week", False):
        return {
            "selection_source_path": source_path_text,
            "selection_source_last_modified": source_last_modified,
            "selection_source_status": "same_day_a_preselect_available_pending_materialization",
            "selection_source_note": (
                f"同日 A 預選 JSON 已到位：{source_path_text}；本地整包產物尚未同步到最新來源，還沒補齊預選 / 定稿產物。"
            ),
        }
    if source_exists and refresh_flags.get("finalize", False):
        return {
            "selection_source_path": source_path_text,
            "selection_source_last_modified": source_last_modified,
            "selection_source_status": "same_day_a_preselect_loaded_pre_finalize",
            "selection_source_note": (
                f"同日 A 預選 JSON 已到位：{source_path_text}；preselect 已更新，但 auto_trade_final_list.csv / sizing.csv 尚未重建。"
            ),
        }
    if resolved_final_count > 0:
        return {
            "selection_source_path": source_path_text,
            "selection_source_last_modified": source_last_modified,
            "selection_source_status": "same_day_a_preselect_loaded",
            "selection_source_note": (
                f"同日 A 預選來源已同步成目前本地整包產物；來源檔：{source_path_text}。"
            ),
        }
    if resolved_preselect_count > 0:
        return {
            "selection_source_path": source_path_text,
            "selection_source_last_modified": source_last_modified,
            "selection_source_status": "same_day_a_preselect_loaded_pre_finalize",
            "selection_source_note": (
                f"同日 A 預選已同步成 preselect 產物；來源檔：{source_path_text}。接下來仍需 finalize / sizing。"
            ),
        }
    if source_exists:
        return {
            "selection_source_path": source_path_text,
            "selection_source_last_modified": source_last_modified,
            "selection_source_status": "same_day_a_preselect_available_pending_materialization",
            "selection_source_note": (
                f"AB 每日預選 JSON 已到位：{source_path_text}；這是獨立專案的每日輸出，"
                "是否需要展開為永豐自動交易整包，改由週一買進流程與本地訂版狀態判斷。"
            ),
        }
    return {
        "selection_source_path": source_path_text,
        "selection_source_last_modified": "",
        "selection_source_status": "same_day_a_preselect_missing_pass",
        "selection_source_note": (
            f"今天沒有同日 A 預選 JSON：{source_path_text}；整包 A 主線直接 pass，不回退前一天。"
        ),
    }


def _today_ordering_summary_clean(
    *,
    guarded_effective_recommendation: object = "",
    selection_source_status: object = "",
    trade_date: date | None = None,
    buy_cutoff_day: date | None = None,
    last_trade_day: date | None = None,
) -> dict[str, str]:
    guarded = str(guarded_effective_recommendation).strip()
    selection = str(selection_source_status).strip()
    parts: list[str] = []
    statuses: list[str] = []

    if guarded == "historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill":
        statuses.append("guarded_time_passed_no_backfill")
        parts.append("2330 受保護下單的保護條件已修好；若仍在 09:10-13:20 視窗內，排程應補跑送單，視窗關閉後才不補。")
    elif guarded == "historical_guard_issue_already_fixed_wait_for_next_schedule":
        statuses.append("guarded_wait_for_next_schedule")
        parts.append("2330 受保護下單的歷史保護條件問題已修好，等待下一次排程；今天不補單。")

    if selection == "same_day_a_preselect_missing_pass":
        statuses.append("basket_a_same_day_json_missing_pass")
        parts.append("今天沒有同日 A 預選 JSON，所以整包 A 主線直接 pass，不回退前一天。")
    elif selection == "same_day_a_preselect_loaded":
        statuses.append("basket_a_loaded")
        parts.append("同日 A 預選已同步成目前本地整包產物。")
    elif selection == "same_day_a_preselect_loaded_pre_finalize":
        statuses.append("basket_a_loaded_pre_finalize")
        parts.append("同日 A 預選已載入預選名單，但 finalize / sizing 尚未完成。")
    elif selection == "same_day_a_preselect_available_pending_materialization":
        statuses.append("basket_a_source_ready_pending_local_materialization")
        parts.append("同日 A 預選 JSON 已到位，但本地整包還沒補齊 preselect / finalize 產物。")

    if selection in {
        "same_day_a_preselect_loaded",
        "same_day_a_preselect_loaded_pre_finalize",
        "same_day_a_preselect_available_pending_materialization",
    }:
        if trade_date is not None and last_trade_day == trade_date:
            statuses.append("basket_buy_window_closed_last_trade_day")
            parts.append(
                f"今天是本週最後交易日（{trade_date.isoformat()}），整包買進迴圈不再開新買單；"
                "AB 每日預選是獨立專案輸出，非買進日是否有新預選不會觸發永豐自動交易補買。"
            )
        elif trade_date is not None and buy_cutoff_day is not None and trade_date > buy_cutoff_day:
            statuses.append("basket_buy_window_closed_after_buy_cutoff")
            parts.append(
                f"今天已超過整包買進截止日（{buy_cutoff_day.isoformat()}），整包買進迴圈不再開新買單；"
                "AB 每日預選是獨立專案輸出，非買進日是否有新預選不會觸發永豐自動交易補買。"
            )

    if not parts:
        return {}

    return {
        "today_ordering_status": "+".join(statuses),
        "today_ordering_note": " ".join(parts),
    }


def _today_ordering_conflict_summary_clean(
    *,
    selection_source_status: object = "",
    trade_date: date | None = None,
    buy_cutoff_day: date | None = None,
    last_trade_day: date | None = None,
) -> dict[str, str]:
    # AB daily preselects are independent public outputs; the auto-trading
    # system only uses the weekly buy-day materialized basket.
    return {}


def _today_ordering_conflict_resolution_summary_clean(
    *,
    today_ordering_conflict_status: object = "",
    trade_date: date | None = None,
    next_trade_day: date | None = None,
) -> dict[str, str]:
    status = str(today_ordering_conflict_status).strip()
    if not status:
        return {}

    if status == "same_day_a_source_arrived_after_basket_buy_window_closed":
        trade_date_text = trade_date.isoformat() if trade_date is not None else "今天"
        return {
            "today_ordering_conflict_resolution_status": "strategy_scope_clarified",
            "today_ordering_conflict_resolution_action": "no_materialization_required_for_non_buy_day_daily_preselect",
            "today_ordering_conflict_resolution_note": (
                f"{trade_date_text} 的 AB 每日預選屬於獨立專案輸出；永豐自動交易只在每週買進日依已訂版整包建立部位。"
                "非買進日的每日預選只作呈現與觀察，不需要調整整包買窗，也不需要補出新的自動買單產物。"
            ),
        }
    if status == "same_day_a_source_arrived_after_basket_buy_cutoff_closed":
        return {
            "today_ordering_conflict_resolution_status": "strategy_scope_clarified",
            "today_ordering_conflict_resolution_action": "no_materialization_required_for_non_buy_day_daily_preselect",
            "today_ordering_conflict_resolution_note": (
                "AB 每日預選屬於獨立專案輸出；永豐自動交易只在每週買進日依已訂版整包建立部位。"
                "非買進日的每日預選只作呈現與觀察，不需要調整整包買窗，也不需要補出新的自動買單產物。"
            ),
        }
    return {}


def _today_new_order_submission_summary_clean(
    *,
    today_ordering_status: object = "",
) -> dict[str, object]:
    raw = str(today_ordering_status).strip()
    if not raw:
        return {}

    tokens = {token for token in raw.split("+") if token}
    guarded_closed = "guarded_time_passed_no_backfill" in tokens
    basket_closed = any(
        token in tokens
        for token in {
            "basket_a_same_day_json_missing_pass",
            "basket_buy_window_closed_last_trade_day",
            "basket_buy_window_closed_after_buy_cutoff",
        }
    )
    if guarded_closed and basket_closed:
        return {
            "today_new_order_submission_open": False,
            "today_new_order_submission_status": "no_auto_new_buy_paths_remaining_today",
            "today_new_order_submission_note": "今天已沒有任何自動新買單路徑可送出；2330 受保護下單路徑與整包買進路徑都已關閉。",
        }
    return {}


def _today_status_summary(
    *,
    trade_date: date,
    last_trade_day: date | None = None,
    buy_execution_rows: list[dict[str, object]] | None = None,
    sell_rows: list[dict[str, object]] | None = None,
    positions_rows: list[dict[str, object]] | None = None,
    today_new_order_submission_status: object = "",
) -> dict[str, str]:
    buy_rows = buy_execution_rows or []
    sell_candidates = sell_rows or []
    positions = positions_rows or []
    submission_status = str(today_new_order_submission_status).strip()

    if sell_candidates and last_trade_day == trade_date:
        return {
            "today_status": "selling",
            "today_status_note": "本週最後交易日已有 sell-loop 候選部位，因此系統目前處於賣出評估 / 執行模式。",
        }
    if submission_status == "no_auto_new_buy_paths_remaining_today":
        if positions:
            return {
                "today_status": "holding",
                "today_status_note": "今天已沒有任何自動新買單路徑可送出；策略只會持有既有部位。",
            }
        if buy_rows:
            return {
                "today_status": "buy_window_closed",
                "today_status_note": "選股與 sizing 產物已存在，但今天所有自動新買單送出路徑都已關閉。",
            }
        return {
            "today_status": "tracking_complete_for_today",
            "today_status_note": "今天已沒有任何自動新買單路徑可送出，而且目前沒有策略持股。",
        }
    if buy_rows:
        return {
            "today_status": "buying",
            "today_status_note": "買入執行列已存在，而且今天至少還有一條自動新買單路徑可能會執行。",
        }
    if positions:
        return {
            "today_status": "holding",
            "today_status_note": "策略目前有持股，但沒有作用中的買入執行列。",
        }
    return {
        "today_status": "tracking",
        "today_status_note": "系統正在監看實際產物，並等待下一個可執行步驟。",
    }


def _next_trade_day_after(anchor_date: date) -> date:
    trade_days, _, _ = load_trade_days()
    for trade_day in trade_days:
        if trade_day > anchor_date:
            return trade_day
    fallback = anchor_date + timedelta(days=1)
    while fallback.weekday() >= 5:
        fallback += timedelta(days=1)
    return fallback




def _selection_source_carry_forward_summary(
    *,
    selection_source_status: object = "",
    trade_date: date | None = None,
) -> dict[str, object]:
    selection = str(selection_source_status).strip()
    if selection not in {
        "same_day_a_preselect_loaded",
        "same_day_a_preselect_loaded_pre_finalize",
        "same_day_a_preselect_available_pending_materialization",
    }:
        return {}
    if trade_date is None:
        return {}
    next_trade_day = _next_trade_day_after(trade_date)
    return {
        "selection_source_carry_forward_open": False,
        "selection_source_carry_forward_status": "same_day_source_expires_after_trade_date",
        "selection_source_carry_forward_next_trade_day": next_trade_day.isoformat(),
        "selection_source_carry_forward_note": (
            f"{trade_date.isoformat()} 的 AB 每日預選只代表當日獨立輸出，不延用成下一個交易日的自動買單來源；"
            "永豐自動交易下次選股 / 買進依下一個週一買進流程處理。"
        ),
    }


def _selection_materialization_summary(
    *,
    trade_date: date,
    selection_source_status: object = "",
    buy_cutoff_day: date | None = None,
    last_trade_day: date | None = None,
) -> dict[str, object]:
    selection = str(selection_source_status).strip()
    if selection not in {
        "same_day_a_preselect_loaded",
        "same_day_a_preselect_loaded_pre_finalize",
        "same_day_a_preselect_available_pending_materialization",
    }:
        return {}

    missing_artifacts: list[str]
    if selection == "same_day_a_preselect_available_pending_materialization":
        missing_artifacts = ["auto_trade_preselect.csv", "auto_trade_final_list.csv", "sizing.csv"]
    elif selection == "same_day_a_preselect_loaded_pre_finalize":
        missing_artifacts = ["auto_trade_final_list.csv", "sizing.csv"]
    else:
        run_dir = auto_trading_dir_for(trade_date)
        missing_artifacts = []
        if not (run_dir / "sizing.csv").exists():
            missing_artifacts.append("sizing.csv")

    if not missing_artifacts:
        return {
            "selection_materialization_open": False,
            "selection_materialization_status": "local_materialization_current",
            "selection_materialization_missing_artifacts": "",
            "selection_materialization_note": "同日 A 預選來源已同步成目前本地整包產物。",
            "selection_materialization_next_action": "materialization_current_no_action_required",
            "selection_materialization_next_action_note": "本地整包產物已就緒；是否能送出新買單，改看買窗與風控。",
        }

    missing_text = ", ".join(missing_artifacts)
    if last_trade_day == trade_date or (buy_cutoff_day is not None and trade_date > buy_cutoff_day):
        return {
            "selection_materialization_open": False,
            "selection_materialization_status": "daily_preselect_observed_no_auto_materialization_required",
            "selection_materialization_missing_artifacts": "",
            "selection_materialization_note": (
                "AB 每日預選來源已到；它是獨立專案的每日輸出。"
                "永豐自動交易本週只使用買進日已訂版的整包，非買進日不需要為同日預選補出新的買單產物。"
            ),
            "selection_materialization_next_action": "no_materialization_required_for_non_buy_day_daily_preselect",
            "selection_materialization_next_action_note": (
                "維持目前持倉、市值與風控追蹤；下次自動交易選股 / 買進依下一個週一買進流程處理。"
            ),
        }

    return {
        "selection_materialization_open": True,
        "selection_materialization_status": "local_materialization_pending",
        "selection_materialization_missing_artifacts": missing_text,
        "selection_materialization_note": (
            f"同日 A 預選來源已到，但本地仍缺 {missing_text}；還需要補跑 prepare_week / finalize 才能產生最新整包產物。"
        ),
        "selection_materialization_next_action": "run_prepare_week_and_finalize_from_same_day_a_source",
        "selection_materialization_next_action_note": (
            f"先跑 prepare_week / finalize，補出 {missing_text}，再決定是否進入後續買入流程。"
        ),
    }


def _weekly_settlement_summary(
    *,
    trade_date: date,
    state_data: dict[str, object] | None = None,
) -> dict[str, object]:
    plan = resolve_week_trade_plan(trade_date)
    first_day = plan.week_trade_days[0] if plan.week_trade_days else trade_date
    last_day = plan.week_trade_days[-1] if plan.week_trade_days else trade_date
    raw_weekly_outputs = state_data.get("weekly_outputs", {}) if isinstance(state_data, dict) else {}
    weekly_outputs = raw_weekly_outputs if isinstance(raw_weekly_outputs, dict) else {}

    default_note_path = weekly_note_path(first_day, last_day)
    default_html_path = weekly_html_report_path(first_day, last_day)
    default_snapshot_path = weekly_snapshot_json_path(first_day, last_day)

    note_path_text = str(weekly_outputs.get("weekly_note", "")).strip()
    html_path_text = str(weekly_outputs.get("weekly_html", "")).strip()
    snapshot_path_text = str(weekly_outputs.get("weekly_snapshot_json", "")).strip()

    note_path = Path(note_path_text) if note_path_text else default_note_path
    html_path = Path(html_path_text) if html_path_text else default_html_path
    snapshot_path = Path(snapshot_path_text) if snapshot_path_text else default_snapshot_path

    present_paths: list[str] = []
    missing_labels: list[str] = []
    for label, path in (
        ("weekly_note", note_path),
        ("weekly_html", html_path),
        ("weekly_snapshot_json", snapshot_path),
    ):
        if path.exists():
            present_paths.append(str(path))
        else:
            missing_labels.append(label)

    present_text = ", ".join(present_paths)
    missing_text = ", ".join(missing_labels)
    week_text = f"{first_day.isoformat()} to {last_day.isoformat()}"

    if not missing_labels:
        return {
            "weekly_settlement_open": False,
            "weekly_settlement_status": "weekly_settlement_current",
            "weekly_settlement_artifacts": present_text,
            "weekly_settlement_note": f"本週結算產物已齊備，涵蓋區間 {week_text}。",
            "weekly_settlement_next_action": "weekly_settlement_current_no_action_required",
            "weekly_settlement_next_action_note": (
                "本週結算產物已是最新；除非你想重新產出週報，否則不需要額外的週結算動作。"
            ),
        }

    if trade_date != last_day:
        return {
            "weekly_settlement_open": False,
            "weekly_settlement_status": "weekly_settlement_wait_for_last_trade_day",
            "weekly_settlement_artifacts": present_text,
            "weekly_settlement_note": (
                f"本週結算要等到本週最後交易日（{last_day.isoformat()}）後才會開放。"
            ),
            "weekly_settlement_next_action": "wait_for_last_trade_day_then_settle_week",
            "weekly_settlement_next_action_note": (
                f"等到 {last_day.isoformat()} 收盤後，再執行 settle_week 產出週筆記 / HTML / snapshot。"
            ),
        }

    return {
        "weekly_settlement_open": True,
        "weekly_settlement_status": "weekly_settlement_pending_after_close",
        "weekly_settlement_artifacts": present_text,
        "weekly_settlement_note": (
            f"本週結算產物尚未完成，區間 {week_text} 仍缺 {missing_text}。"
        ),
        "weekly_settlement_next_action": "run_settle_week_after_close",
        "weekly_settlement_next_action_note": (
            f"收盤後執行 settle_week，補出 {missing_text}。"
        ),
    }





def _normalize_guarded_next_run_message(message: object) -> str:
    text = str(message or "").strip()
    if not text:
        return ""
    exact_map = {
        "Current config and scheduled runner look ready for the next guarded live run.": (
            "目前設定、排程 runner 與 Windows 工作排程看起來都已準備好，可等待下一次受保護下單真實執行。"
        ),
        "Current config, scheduled runner, and Windows task look ready for the next guarded live run.": (
            "目前設定、排程 runner 與 Windows 工作排程看起來都已準備好，可等待下一次受保護下單真實執行。"
        ),
        "Next guarded live run is still blocked because SINOPAC_ALLOW_LIVE_SUBMIT is not enabled.": (
            "下一次受保護下單真實執行仍被擋住，因為尚未啟用 SINOPAC_ALLOW_LIVE_SUBMIT。"
        ),
        "Next guarded live run is still blocked because auto_trading.live_enabled is false in config.": (
            "下一次受保護下單真實執行仍被擋住，因為設定檔中的 auto_trading.live_enabled 仍為 false。"
        ),
        "Next guarded live run is still blocked because the scheduled runner does not set AUTO_TRADE_LIVE=1.": (
            "下一次受保護下單真實執行仍被擋住，因為排程 runner 沒有設定 AUTO_TRADE_LIVE=1。"
        ),
        "Next guarded live run is still blocked because the Windows scheduled task is disabled.": (
            "下一次受保護下單真實執行仍被擋住，因為 Windows 工作排程被停用。"
        ),
        "Next guarded live run readiness is incomplete because the Windows scheduled task could not be verified.": (
            "下一次受保護下單真實執行的就緒狀態尚未完整確認，因為無法核對 Windows 工作排程。"
        ),
        "Next guarded live run readiness is incomplete because the Windows scheduled task has no next run time.": (
            "下一次受保護下單真實執行的就緒狀態尚未完整確認，因為 Windows 工作排程沒有下一次執行時間。"
        ),
    }
    mapped = exact_map.get(text)
    if mapped:
        return mapped
    ready_prefix = "Current config, scheduled runner, and Windows task look ready for the next guarded live run at "
    if text.startswith(ready_prefix) and text.endswith("."):
        schedule_time = text[len(ready_prefix) : -1].strip()
        return f"目前設定、排程 runner 與 Windows 工作排程看起來都已準備好，可等待下一次受保護下單真實執行（{schedule_time}）。"
    not_ready_prefix = "Next guarded live run is not ready because the Windows scheduled task state is "
    if text.startswith(not_ready_prefix) and text.endswith("."):
        schedule_state = text[len(not_ready_prefix) : -1].strip()
        return f"下一次受保護下單真實執行尚未就緒，因為 Windows 工作排程狀態是 {schedule_state}。"
    past_prefix = "Guard settings look fixed now, but the Windows scheduled task time already passed at "
    past_suffix = "; missed guarded orders do not backfill automatically."
    if text.startswith(past_prefix) and text.endswith(past_suffix):
        schedule_time = text[len(past_prefix) : -len(past_suffix)].strip()
        return (
            f"保護條件設定現在看起來已修好，但 Windows 排程時間 {schedule_time} 已過，"
            "且補跑視窗已關閉；今天不再送這筆受保護下單。"
        )
    return (
        text.replace("guard 設定", "保護條件設定")
        .replace("guarded 真實執行", "受保護下單真實執行")
        .replace("guarded 單", "受保護下單")
        .replace("下一次 受保護下單真實執行", "下一次受保護下單真實執行")
        .replace("錯過的 受保護下單", "錯過的受保護下單")
        .replace("這次 受保護下單執行", "這次受保護下單執行")
    )


def _normalize_guarded_schedule_description(description: object) -> str:
    def _cleanup(text: str) -> str:
        return (
            text.replace("price cap", "價格上限")
            .replace("duplicate-order guard", "重複單保護")
            .replace("重複單 guard", "重複單保護")
            .replace(" minutes", " 分鐘")
            .replace(" minute", " 分鐘")
        )

    text = str(description or "").strip()
    if not text:
        return ""
    prefix = "Run the only allowed SinoPac live order automation on "
    if not text.startswith(prefix) or ": " not in text:
        if "只允許的永豐真實下單自動化" in text:
            return _cleanup(text)
        return text
    after_prefix = text[len(prefix) :]
    trade_date_text, order_part = after_prefix.split(": ", 1)
    duplicate_guard = False
    if ", with duplicate-order guard." in order_part:
        order_part = order_part.replace(", with duplicate-order guard.", "").strip()
        duplicate_guard = True
    elif order_part.endswith("."):
        order_part = order_part[:-1].strip()
    if " with price cap " not in order_part:
        return text
    if " share at " not in order_part and " share from " not in order_part:
        return text
    order_text, price_cap_text = order_part.split(" with price cap ", 1)
    price_cap_text = price_cap_text.strip()
    retry_text = ""
    if ", retry every " in price_cap_text:
        price_cap_text, retry_part = price_cap_text.split(", retry every ", 1)
        retry_text = retry_part.strip()
    order_tokens = order_text.split()
    until_time = ""
    if len(order_tokens) >= 7 and order_tokens[4] == "share" and order_tokens[5] == "at":
        order_time = order_tokens[6]
    elif len(order_tokens) >= 9 and order_tokens[4] == "share" and order_tokens[5] == "from" and order_tokens[7] == "to":
        order_time = order_tokens[6]
        until_time = order_tokens[8]
    else:
        return text
    stock_id = order_tokens[0]
    side = order_tokens[1]
    lot = order_tokens[2]
    qty = order_tokens[3]
    side_text = {"Buy": "買進", "Sell": "賣出"}.get(side, side)
    lot_text = {"IntradayOdd": "盤中零股"}.get(lot, lot)
    duplicate_text = "，含重複單保護" if duplicate_guard else ""
    time_text = f"{order_time}-{until_time}" if until_time else order_time
    retry_display = f"，每 {retry_text}重試" if retry_text else ""
    return _cleanup(
        f"只允許的永豐真實下單自動化：{trade_date_text}，{stock_id} {side_text}{lot_text} {qty} 股，"
        f"{time_text}，價格上限 {price_cap_text}{retry_display}{duplicate_text}。"
    )


def _workflow_note_display_text(value: object) -> str:
    return _normalize_display_text(str(value or ""))


WORKFLOW_CURRENT_STATE_LABELS = {
    "status": "狀態",
    "provider_name": "來源提供者",
    "buy_cutoff_day": "買進截止日",
    "last_trade_day": "最後交易日",
    "updated_at": "更新時間",
    "completed_steps": "已完成步數",
    "pending_steps": "待處理步數",
    "closed_steps": "今日關閉步數",
    "today_status": "今日狀態",
    "today_status_note": "今日狀態說明",
    "selection_source_path": "選股來源路徑",
    "selection_source_last_modified": "選股來源更新時間",
    "selection_source_status": "選股來源狀態",
    "selection_source_note": "選股來源說明",
    "dashboard_refresh_status": "儀表板刷新狀態",
    "dashboard_refresh_steps": "儀表板刷新步驟",
    "dashboard_refresh_note": "儀表板刷新說明",
    "dashboard_refresh_trigger_status": "儀表板刷新觸發狀態",
    "dashboard_refresh_trigger_artifacts": "儀表板刷新觸發產物",
    "dashboard_refresh_trigger_note": "儀表板刷新觸發說明",
    "dashboard_last_materialization_status": "最近一次物化狀態",
    "dashboard_last_materialization_steps": "最近一次物化步驟",
    "dashboard_last_materialization_note": "最近一次物化說明",
    "dashboard_last_materialization_trigger_status": "最近一次物化觸發狀態",
    "dashboard_last_materialization_trigger_artifacts": "最近一次物化觸發產物",
    "dashboard_last_materialization_trigger_note": "最近一次物化觸發說明",
    "weekly_settlement_open": "週結算開啟",
    "weekly_settlement_status": "週結算狀態",
    "weekly_settlement_artifacts": "週結算產物",
    "weekly_settlement_note": "週結算說明",
    "weekly_settlement_next_action": "週結算下一步",
    "weekly_settlement_next_action_note": "週結算下一步說明",
    "today_ordering_status": "今日下單狀態",
    "today_ordering_note": "今日下單說明",
    "today_ordering_conflict_status": "策略範圍狀態",
    "today_ordering_conflict_note": "策略範圍說明",
    "today_ordering_conflict_resolution_status": "策略範圍處理狀態",
    "today_ordering_conflict_resolution_action": "策略範圍處理動作",
    "today_ordering_conflict_resolution_note": "策略範圍處理說明",
    "today_new_order_submission_open": "今日新單送出開啟",
    "today_new_order_submission_status": "今日新單送出狀態",
    "today_new_order_submission_note": "今日新單送出說明",
    "selection_source_carry_forward_open": "選股來源延用開啟",
    "selection_source_carry_forward_status": "選股來源延用狀態",
    "selection_source_carry_forward_next_trade_day": "選股來源下個交易日",
    "selection_source_carry_forward_note": "選股來源延用說明",
    "selection_materialization_open": "選股物化開啟",
    "selection_materialization_status": "選股物化狀態",
    "selection_materialization_missing_artifacts": "選股物化缺少產物",
    "selection_materialization_note": "選股物化說明",
    "selection_materialization_next_action": "選股物化下一步",
    "selection_materialization_next_action_note": "選股物化下一步說明",
}


def _workflow_current_state_label(key: str) -> str:
    return WORKFLOW_CURRENT_STATE_LABELS.get(str(key or "").strip(), str(key or "").strip())


WORKFLOW_GUARDED_TASK_LABELS = {
    "status": "狀態",
    "exit_code": "結束碼",
    "message": "訊息",
    "latest_log": "最新 log",
}


WORKFLOW_GUARDED_SCHEDULE_LABELS = {
    "status": "狀態",
    "task_name": "排程名稱",
    "state": "排程狀態",
    "next_run_time": "下次執行時間",
    "last_run_time": "上次執行時間",
    "last_task_result": "上次執行結果",
    "description": "說明",
    "message": "訊息",
}


WORKFLOW_GUARDED_POST_CHECK_LABELS = {
    "after_status": "後檢查狀態",
    "recommendation": "建議動作",
    "recommendation_note": "建議動作說明",
    "effective_recommendation": "目前有效建議",
    "effective_recommendation_note": "目前有效建議說明",
    "reconciled": "已對帳",
    "fills_count": "成交筆數",
    "positions_count": "部位筆數",
    "next_run_guard_status": "下次受保護下單排程狀態",
    "next_run_guard_message": "下次受保護下單排程說明",
    "config_timing_status": "設定時序狀態",
    "config_timing_message": "設定時序說明",
    "config_path": "設定檔路徑",
    "config_last_modified": "設定檔更新時間",
    "task_recorded_at": "排程記錄時間",
}


WORKFLOW_SELL_READINESS_LABELS = {
    "blocking_reason": "阻塞原因",
    "post_guarded_recommendation": "受保護下單後建議動作",
    "post_guarded_recommendation_note": "受保護下單後建議說明",
    "next_action": "下一步",
    "next_action_note": "下一步說明",
    "positions_ready": "部位已就緒",
    "positions_count": "部位筆數",
    "post_guarded_effective_recommendation": "受保護下單後目前有效建議",
    "post_guarded_effective_recommendation_note": "受保護下單後目前有效建議說明",
    "post_guarded_next_run_guard_status": "受保護下單後下次排程狀態",
    "post_guarded_next_run_guard_message": "受保護下單後下次排程說明",
    "post_guarded_config_timing_status": "受保護下單後設定時序狀態",
    "post_guarded_config_timing_message": "受保護下單後設定時序說明",
    "post_guarded_config_path": "受保護下單後設定檔路徑",
    "post_guarded_config_last_modified": "受保護下單後設定檔更新時間",
    "post_guarded_task_recorded_at": "受保護下單後排程記錄時間",
}


def _workflow_section_label(key: str, labels: dict[str, str]) -> str:
    token = str(key or "").strip()
    return labels.get(token, token)


def _workflow_step_display(step: str) -> str:
    return _workflow_note_display_text(step)


def _workflow_row_status_display(status: str) -> str:
    token = str(status or "").strip()
    labels = {
        "done": "已完成",
        "pending": "待處理",
        "closed": "今日關閉",
    }
    label = labels.get(token, "")
    if label and label != token:
        return f"{label} ({token})"
    return token


def _dashboard_refresh_summary(state_data: dict[str, object]) -> dict[str, object]:
    return _dashboard_refresh_summary_clean(state_data)


def _dashboard_refresh_note(status: str, steps_text: str, fallback_note: str = "") -> str:
    if status == "materialized_and_buy_loop_ran":
        return f"最近一次 refresh_dashboard 重跑了 {steps_text}，並且已進入 buy_loop。"
    if status == "materialized_without_buy_loop":
        return f"最近一次 refresh_dashboard 重跑了 {steps_text}，但沒有進入 buy_loop。"
    if status == "report_only_refresh":
        return f"最近一次 refresh_dashboard 只重跑了 {steps_text}，沒有重做 prepare_week / finalize。"
    return fallback_note


def _dashboard_refresh_trigger_note(trigger_status: str, trigger_note: str = "") -> str:
    if trigger_status == "historical_materialization_reason_not_recorded":
        return "這次 materializing refresh 的 trigger reason 沒有在當時被記錄下來；目前只能從已有產物反推。"
    return trigger_note


def _dashboard_refresh_summary_clean(state_data: dict[str, object]) -> dict[str, object]:
    refresh = state_data.get("dashboard_refresh")
    if not isinstance(refresh, dict):
        status = str(state_data.get("dashboard_refresh_status", "")).strip()
        steps_text = str(state_data.get("dashboard_refresh_steps", "")).strip()
        trigger_status = str(state_data.get("dashboard_refresh_trigger_status", "")).strip()
        trigger_artifacts = str(state_data.get("dashboard_refresh_trigger_artifacts", "")).strip()
        trigger_note = str(state_data.get("dashboard_refresh_trigger_note", "")).strip()
        note = _dashboard_refresh_note(status, steps_text, str(state_data.get("dashboard_refresh_note", "")).strip())
        if not any((status, steps_text, note, trigger_status, trigger_artifacts, trigger_note)):
            return {}
        summary: dict[str, object] = {
            "dashboard_refresh_status": status,
            "dashboard_refresh_steps": steps_text,
            "dashboard_refresh_note": note,
        }
        if trigger_status:
            summary.update(
                {
                    "dashboard_refresh_trigger_status": trigger_status,
                    "dashboard_refresh_trigger_artifacts": trigger_artifacts,
                    "dashboard_refresh_trigger_note": _dashboard_refresh_trigger_note(trigger_status, trigger_note),
                }
            )
        return summary

    raw_steps = refresh.get("steps_run", [])
    if not isinstance(raw_steps, list):
        return {}
    steps = [str(step).strip() for step in raw_steps if str(step).strip()]
    if not steps:
        return {}

    steps_text = ", ".join(steps)
    if "prepare_week" in steps or "finalize" in steps:
        status = "materialized_and_buy_loop_ran" if "buy_loop" in steps else "materialized_without_buy_loop"
    else:
        status = "report_only_refresh"

    summary: dict[str, object] = {
        "dashboard_refresh_status": status,
        "dashboard_refresh_steps": steps_text,
        "dashboard_refresh_note": _dashboard_refresh_note(status, steps_text),
    }

    trigger_status = str(refresh.get("source_refresh_trigger_status", "")).strip()
    trigger_artifacts = str(refresh.get("source_refresh_trigger_artifacts", "")).strip()
    trigger_note = str(refresh.get("source_refresh_trigger_note", "")).strip()
    if trigger_status:
        summary.update(
            {
                "dashboard_refresh_trigger_status": trigger_status,
                "dashboard_refresh_trigger_artifacts": trigger_artifacts,
                "dashboard_refresh_trigger_note": _dashboard_refresh_trigger_note(trigger_status, trigger_note),
            }
        )
    elif status.startswith("materialized_"):
        summary.update(
            {
                "dashboard_refresh_trigger_status": "historical_materialization_reason_not_recorded",
                "dashboard_refresh_trigger_artifacts": "",
                "dashboard_refresh_trigger_note": _dashboard_refresh_trigger_note(
                    "historical_materialization_reason_not_recorded"
                ),
            }
        )
    return summary


def _dashboard_last_materializing_summary(state_data: dict[str, object]) -> dict[str, object]:
    candidate = state_data.get("dashboard_refresh_last_materializing")
    if isinstance(candidate, dict):
        summary = _dashboard_refresh_summary({"dashboard_refresh": candidate})
    else:
        status = str(state_data.get("dashboard_last_materialization_status", "")).strip()
        steps = str(state_data.get("dashboard_last_materialization_steps", "")).strip()
        note = str(state_data.get("dashboard_last_materialization_note", "")).strip()
        trigger_status = str(state_data.get("dashboard_last_materialization_trigger_status", "")).strip()
        trigger_artifacts = str(state_data.get("dashboard_last_materialization_trigger_artifacts", "")).strip()
        trigger_note = str(state_data.get("dashboard_last_materialization_trigger_note", "")).strip()
        if any((status, steps, note, trigger_status, trigger_artifacts, trigger_note)):
            return {
                "dashboard_last_materialization_status": status,
                "dashboard_last_materialization_steps": steps,
                "dashboard_last_materialization_note": note,
                "dashboard_last_materialization_trigger_status": trigger_status,
                "dashboard_last_materialization_trigger_artifacts": trigger_artifacts,
                "dashboard_last_materialization_trigger_note": trigger_note,
            }
        summary = _dashboard_refresh_summary(state_data)
        if not str(summary.get("dashboard_refresh_status", "")).startswith("materialized_"):
            return {}
    return {
        "dashboard_last_materialization_status": str(summary.get("dashboard_refresh_status", "")).strip(),
        "dashboard_last_materialization_steps": str(summary.get("dashboard_refresh_steps", "")).strip(),
        "dashboard_last_materialization_note": str(summary.get("dashboard_refresh_note", "")).strip(),
        "dashboard_last_materialization_trigger_status": str(summary.get("dashboard_refresh_trigger_status", "")).strip(),
        "dashboard_last_materialization_trigger_artifacts": str(summary.get("dashboard_refresh_trigger_artifacts", "")).strip(),
        "dashboard_last_materialization_trigger_note": str(summary.get("dashboard_refresh_trigger_note", "")).strip(),
    }


def _refresh_dashboard_event_payload(trade_date: date) -> dict[str, object]:
    path = auto_trading_dir_for(trade_date) / "event_log.jsonl"
    if not path.exists():
        return {}
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(raw.get("event_type", "")).strip() != "refresh_dashboard":
            continue
        metadata = raw.get("metadata", {}) if isinstance(raw.get("metadata", {}), dict) else {}
        raw_steps = metadata.get("steps_run", [])
        if not isinstance(raw_steps, list):
            return {}
        steps = [str(step).strip() for step in raw_steps if str(step).strip()]
        if not steps:
            return {}
        payload: dict[str, object] = {
            "steps_run": steps,
            "live": bool(metadata.get("live", False)),
            "confirm_live": bool(metadata.get("confirm_live", False)),
        }
        for key in (
            "source_refresh_trigger_status",
            "source_refresh_trigger_artifacts",
            "source_refresh_trigger_note",
        ):
            value = metadata.get(key)
            if value not in ("", None):
                payload[key] = value
        return payload
    return {}


def _refresh_dashboard_event_summary(trade_date: date) -> dict[str, object]:
    payload = _refresh_dashboard_event_payload(trade_date)
    if not payload:
        return {}
    return _dashboard_refresh_summary({"dashboard_refresh": payload})


def _current_dashboard_refresh_payload(trade_date: date, state_data: dict[str, object]) -> dict[str, object]:
    candidate = state_data.get("dashboard_refresh")
    if isinstance(candidate, dict) and candidate:
        return candidate
    return _refresh_dashboard_event_payload(trade_date)


def _resolve_last_materializing_refresh_payload(
    trade_date: date,
    state_data: dict[str, object],
) -> dict[str, object] | None:
    candidate = state_data.get("dashboard_refresh_last_materializing")
    if isinstance(candidate, dict):
        summary = _dashboard_refresh_summary({"dashboard_refresh": candidate})
        if str(summary.get("dashboard_refresh_status", "")).startswith("materialized_"):
            return candidate

    refresh = state_data.get("dashboard_refresh")
    if isinstance(refresh, dict):
        summary = _dashboard_refresh_summary({"dashboard_refresh": refresh})
        if str(summary.get("dashboard_refresh_status", "")).startswith("materialized_"):
            return refresh

    summary = _dashboard_refresh_summary(state_data)
    if str(summary.get("dashboard_refresh_status", "")).startswith("materialized_"):
        steps_text = str(summary.get("dashboard_refresh_steps", "")).strip()
        steps = [step.strip() for step in steps_text.split(",") if step.strip()]
        if steps:
            payload: dict[str, object] = {
                "steps_run": steps,
                "live": False,
                "confirm_live": False,
            }
            trigger_status = str(summary.get("dashboard_refresh_trigger_status", "")).strip()
            trigger_artifacts = str(summary.get("dashboard_refresh_trigger_artifacts", "")).strip()
            trigger_note = str(summary.get("dashboard_refresh_trigger_note", "")).strip()
            if trigger_status:
                payload["source_refresh_trigger_status"] = trigger_status
                payload["source_refresh_trigger_artifacts"] = trigger_artifacts
                payload["source_refresh_trigger_note"] = trigger_note
            return payload

    payload = _refresh_dashboard_event_payload(trade_date)
    if not payload:
        return None
    summary = _dashboard_refresh_summary({"dashboard_refresh": payload})
    if str(summary.get("dashboard_refresh_status", "")).startswith("materialized_"):
        return payload
    return None



def _allowed_live_task_log_evidence(
    trade_date: date,
    *,
    log_dir: Path | None = None,
) -> dict[str, str] | None:
    resolved_log_dir = log_dir or (DATA_DIR / "task_logs" / "allowed_2330_live_order")
    if not resolved_log_dir.exists():
        return None
    candidates = sorted(resolved_log_dir.glob(f"{trade_date.isoformat()}_*.log"))
    if not candidates:
        return None
    latest = candidates[-1]
    try:
        raw_content = latest.read_bytes()
        if raw_content.startswith((b"\xff\xfe", b"\xfe\xff")):
            content = raw_content.decode("utf-16", errors="replace")
        else:
            content = raw_content.decode("utf-8-sig", errors="replace")
            if "\x00" in content:
                content = raw_content.decode("utf-16", errors="replace")
    except OSError:
        return {
            "status": "unreadable_log",
            "exit_code": "",
            "message": "latest task log could not be read",
            "path": str(latest),
        }
    exit_code = ""
    message = ""
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if "exit_code=" in line:
            exit_code = line.rsplit("exit_code=", 1)[-1].strip()
        if not message and (
            "can't open file" in line
            or "Traceback" in line
            or "ERROR" in line
            or "Exception" in line
            or "NativeCommandError" in line
        ):
            message = line
    if not message:
        message = "已找到任務日誌"
    status = "success" if exit_code == "0" else ("failed" if exit_code else "unknown")
    return {
        "status": status,
        "exit_code": exit_code,
        "message": message,
        "path": str(latest),
    }


def _summarize_scheduler_query_error(text: object, default: str = "scheduled_task_query_failed") -> str:
    raw = " ".join(str(text or "").split())
    if not raw:
        return default
    lowered = raw.lower()
    if (
        "access is denied" in lowered
        or "access denied" in lowered
        or "permissiondenied" in lowered
        or "hresult 0x80041003" in lowered
    ):
        return "permission_denied"
    if "cannot find the path specified" in lowered:
        return "path_not_found"
    if "non-json" in lowered:
        return "non_json_output"
    return raw


def _scheduled_task_evidence(
    task_name: str = ALLOWED_LIVE_ORDER_TASK_NAME,
    *,
    runner=None,
) -> dict[str, str]:
    if sys.platform != "win32":
        return {
            "status": "unsupported",
            "task_name": task_name,
            "state": "",
            "next_run_time": "",
            "last_run_time": "",
            "last_task_result": "",
            "description": "",
            "message": "Windows Scheduled Task query is only available on Windows.",
        }
    task_name_ps = task_name.replace("'", "''")
    task_name_schtasks = task_name if task_name.startswith("\\") else f"\\{task_name}"
    script = f"""
$ErrorActionPreference = 'Stop'
$task = Get-ScheduledTask -TaskName '{task_name_ps}'
$info = Get-ScheduledTaskInfo -TaskName '{task_name_ps}'
[pscustomobject]@{{
  task_name = [string]$task.TaskName
  state = [string]$task.State
  next_run_time = [string]$info.NextRunTime
  last_run_time = [string]$info.LastRunTime
  last_task_result = [string]$info.LastTaskResult
  description = [string]$task.Description
}} | ConvertTo-Json -Compress
"""
    run = runner or subprocess.run
    try:
        completed = run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            text=True,
            timeout=8,
        )
    except Exception as exc:
        return {
            "status": "query_failed",
            "task_name": task_name,
            "state": "",
            "next_run_time": "",
            "last_run_time": "",
            "last_task_result": "",
            "description": "",
            "message": str(exc),
        }

    stdout = str(getattr(completed, "stdout", "") or "").strip()
    stderr = str(getattr(completed, "stderr", "") or "").strip()
    if int(getattr(completed, "returncode", 1) or 0) != 0:
        power_shell_failure = _summarize_scheduler_query_error(stderr or stdout)
    else:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            power_shell_failure = _summarize_scheduler_query_error("scheduled task query returned non-JSON output")
        else:
            state = str(payload.get("state", "")).strip()
            status = state.lower() if state else "unknown"
            return {
                "status": status,
                "task_name": str(payload.get("task_name", task_name)),
                "state": state,
                "next_run_time": str(payload.get("next_run_time", "")),
                "last_run_time": str(payload.get("last_run_time", "")),
                "last_task_result": str(payload.get("last_task_result", "")),
                "description": str(payload.get("description", "")),
                "message": "已讀到 Windows 工作排程狀態",
            }

    try:
        fallback = run(
            ["schtasks.exe", "/Query", "/TN", task_name_schtasks, "/V", "/FO", "LIST"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            text=True,
            timeout=8,
        )
    except Exception as exc:
        return {
            "status": "query_failed",
            "task_name": task_name,
            "state": "",
            "next_run_time": "",
            "last_run_time": "",
            "last_task_result": "",
            "description": "",
            "message": f"{power_shell_failure}; schtasks fallback failed: {exc}",
        }

    fallback_stdout = str(getattr(fallback, "stdout", "") or "").strip()
    fallback_stderr = str(getattr(fallback, "stderr", "") or "").strip()
    if int(getattr(fallback, "returncode", 1) or 0) != 0:
        schtasks_failure = _summarize_scheduler_query_error(fallback_stderr or fallback_stdout)
    else:
        payload: dict[str, str] = {}
        current_key = ""
        for raw_line in fallback_stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if ":" in line:
                key, value = line.split(":", 1)
                current_key = key.strip().lower().replace(" ", "_")
                payload[current_key] = value.strip()
            elif current_key:
                payload[current_key] = f"{payload[current_key]} {line}".strip()

        state = str(payload.get("status", "") or payload.get("scheduled_task_state", "")).strip()
        status = state.lower() if state else "unknown"
        return {
            "status": status,
            "task_name": str(payload.get("taskname", task_name_schtasks)),
            "state": state,
            "next_run_time": str(payload.get("next_run_time", "")),
            "last_run_time": str(payload.get("last_run_time", "")),
            "last_task_result": str(payload.get("last_result", "")),
            "description": str(payload.get("comment", "")),
            "message": f"已透過 schtasks 讀到排程狀態 (powershell fallback: {power_shell_failure})",
        }

    task_xml_path = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "Tasks"
    for part in task_name.strip("\\").split("\\"):
        if part:
            task_xml_path /= part
    try:
        root = ET.parse(task_xml_path).getroot()
    except Exception as exc:
        return {
            "status": "query_failed",
            "task_name": task_name,
            "state": "",
            "next_run_time": "",
            "last_run_time": "",
            "last_task_result": "",
            "description": "",
            "message": (
                f"{power_shell_failure}; "
                f"schtasks fallback failed: {schtasks_failure}; "
                f"task xml fallback failed: {_summarize_scheduler_query_error(exc, default='task_xml_parse_failed')}"
            ),
        }

    namespace = {}
    if root.tag.startswith("{") and "}" in root.tag:
        namespace["t"] = root.tag[1 : root.tag.index("}")]
        prefix = "t:"
    else:
        prefix = ""

    def _find_text(xpath: str) -> str:
        found = root.find(xpath, namespace)
        return str(found.text).strip() if found is not None and found.text else ""

    start_boundary = ""
    repetition_interval = ""
    repetition_duration = ""
    triggers = root.find(f"./{prefix}Triggers", namespace)
    if triggers is not None:
        for trigger in list(triggers):
            start_node = trigger.find(f"./{prefix}StartBoundary", namespace)
            start_boundary = str(start_node.text).strip() if start_node is not None and start_node.text else ""
            repetition = trigger.find(f"./{prefix}Repetition", namespace)
            if repetition is not None:
                interval_node = repetition.find(f"./{prefix}Interval", namespace)
                duration_node = repetition.find(f"./{prefix}Duration", namespace)
                repetition_interval = (
                    str(interval_node.text).strip() if interval_node is not None and interval_node.text else ""
                )
                repetition_duration = (
                    str(duration_node.text).strip() if duration_node is not None and duration_node.text else ""
                )
            if start_boundary:
                break

    next_run_time = start_boundary
    if start_boundary:
        try:
            normalized_boundary = start_boundary.replace("Z", "+00:00")
            scheduled_at = datetime.fromisoformat(normalized_boundary)
            if scheduled_at.tzinfo is None:
                scheduled_at = scheduled_at.replace(tzinfo=TAIPEI)
            else:
                scheduled_at = scheduled_at.astimezone(TAIPEI)
            next_at = _next_repetition_run_at(
                start_at=scheduled_at,
                interval=_parse_windows_task_duration(repetition_interval),
                duration=_parse_windows_task_duration(repetition_duration),
            )
            next_run_time = next_at.isoformat(timespec="seconds")
        except ValueError:
            next_run_time = start_boundary

    enabled = _find_text(f"./{prefix}Settings/{prefix}Enabled").lower()
    state = "Ready" if enabled == "true" else "Disabled"
    uri = _find_text(f"./{prefix}RegistrationInfo/{prefix}URI") or task_name_schtasks
    description = _find_text(f"./{prefix}RegistrationInfo/{prefix}Description")
    return {
        "status": state.lower(),
        "task_name": uri,
        "state": state,
        "next_run_time": next_run_time,
        "last_run_time": "",
        "last_task_result": "",
        "description": description,
        "message": (
            "已透過 task xml 讀到排程狀態 "
            f"(powershell fallback: {power_shell_failure}; schtasks fallback failed: {schtasks_failure})"
        ),
    }


def _read_json_safely(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _parse_scheduler_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        normalized = text.replace("Z", "+00:00")
        resolved = datetime.fromisoformat(normalized)
    except ValueError:
        resolved = None
    if resolved is None:
        import re

        match = re.match(
            r"^(?P<year>\d{4})/(?P<month>\d{1,2})/(?P<day>\d{1,2})\s+"
            r"(?P<period>上午|下午|AM|PM)\s+"
            r"(?P<hour>\d{1,2}):(?P<minute>\d{2}):(?P<second>\d{2})$",
            text,
        )
        if not match:
            return None
        hour = int(match.group("hour"))
        period = match.group("period").upper()
        if period in {"下午", "PM"} and hour != 12:
            hour += 12
        if period in {"上午", "AM"} and hour == 12:
            hour = 0
        resolved = datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            hour,
            int(match.group("minute")),
            int(match.group("second")),
            tzinfo=TAIPEI,
        )
    if resolved.tzinfo is None:
        return resolved.replace(tzinfo=TAIPEI)
    return resolved.astimezone(TAIPEI)


def _parse_windows_task_duration(value: object) -> timedelta | None:
    text = str(value or "").strip().upper()
    if not text:
        return None
    match = re.fullmatch(
        r"P(?:(?P<days>\d+(?:\.\d+)?)D)?"
        r"(?:T(?:(?P<hours>\d+(?:\.\d+)?)H)?"
        r"(?:(?P<minutes>\d+(?:\.\d+)?)M)?"
        r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?",
        text,
    )
    if not match:
        return None
    values = {key: float(value) if value else 0.0 for key, value in match.groupdict().items()}
    duration = timedelta(
        days=values["days"],
        hours=values["hours"],
        minutes=values["minutes"],
        seconds=values["seconds"],
    )
    return duration if duration.total_seconds() > 0 else None


def _next_repetition_run_at(
    *,
    start_at: datetime,
    interval: timedelta | None,
    duration: timedelta | None,
    now: datetime | None = None,
) -> datetime:
    if interval is None or interval.total_seconds() <= 0:
        return start_at
    resolved_now = now or datetime.now(TAIPEI)
    end_at = start_at + duration if duration is not None else None
    if resolved_now <= start_at:
        return start_at
    if end_at is not None and resolved_now >= end_at:
        return start_at

    elapsed_seconds = (resolved_now - start_at).total_seconds()
    interval_seconds = interval.total_seconds()
    intervals_elapsed = int(elapsed_seconds // interval_seconds) + 1
    candidate = start_at + (interval * intervals_elapsed)
    if end_at is not None and candidate > end_at:
        return start_at
    return candidate


def _guarded_live_order_status_summary(
    trade_date: date,
    *,
    live_task_evidence: dict[str, str] | None = None,
    scheduled_task_evidence: dict[str, str] | None = None,
) -> dict[str, object]:
    run_dir = auto_trading_dir_for(trade_date)
    task_json = _read_json_safely(run_dir / "allowed_live_order_2330_task.json")
    chase_json = _read_json_safely(run_dir / "chase_2330.json")
    orders_rows = _read_csv_rows(run_dir / "orders.csv")
    fills_rows = _read_csv_rows(run_dir / "fills.csv")
    positions_rows = _read_csv_rows(run_dir / "positions.csv")
    pnl_rows = _read_csv_rows(run_dir / "pnl_snapshots.csv")
    task_evidence = live_task_evidence if live_task_evidence is not None else _allowed_live_task_log_evidence(trade_date)
    schedule_evidence = (
        scheduled_task_evidence if scheduled_task_evidence is not None else _scheduled_task_evidence()
    )

    task_status = str(task_json.get("status", "")).strip()
    chase_submitted = bool(chase_json.get("submitted", False))
    fills_count = len(fills_rows)
    positions_count = len(positions_rows)
    task_log_status = str((task_evidence or {}).get("status", "")).strip()
    schedule_status = str((schedule_evidence or {}).get("status", "")).strip()
    next_run_time = str((schedule_evidence or {}).get("next_run_time", "")).strip()
    next_run_at = _parse_scheduler_datetime(next_run_time)
    now = datetime.now(TAIPEI)

    if fills_count > 0:
        status = "reconciled_with_fills"
        recommendation = "fills_found_review_positions_and_sell_loop"
    elif task_status == "submitted" or chase_submitted or orders_rows:
        status = "submitted_no_fills_yet"
        recommendation = "run_reconcile_broker_state_after_market_updates"
    elif task_log_status == "failed":
        status = "task_failed"
        recommendation = "inspect_latest_task_log_before_resubmitting"
    elif task_status.startswith("skipped_"):
        status = task_status
        recommendation = {
            "skipped_allow_live_submit_disabled": "enable_allow_live_submit_before_next_scheduled_run",
            "skipped_config_live_disabled": "enable_live_in_config_before_next_scheduled_run",
            "skipped_weekly_execution_disabled": "set_weekly_execution_enabled_after_user_command",
            "skipped_weekly_budget_missing": "set_weekly_budget_after_user_command",
            "skipped_weekly_execution_week_mismatch": "set_weekly_execution_week_id_for_trade_week",
            "skipped_confirm_live_missing": "confirm_live_before_next_manual_run",
            "skipped_auto_trade_live_env_missing": "ensure_auto_trade_live_env_for_next_scheduled_run",
        }.get(task_status, "inspect_guard_reason_before_next_scheduled_run")
    elif next_run_at is not None and next_run_at <= now:
        status = "scheduled_time_passed_without_artifacts"
        recommendation = "inspect_task_log_and_run_read_only_reconcile"
    elif next_run_time:
        status = "scheduled_waiting"
        recommendation = "wait_for_scheduled_run"
    elif schedule_status == "query_failed":
        status = "schedule_query_failed"
        recommendation = "cannot_verify_scheduler_from_current_context"
    else:
        status = "no_execution_artifacts"
        recommendation = "no_order_evidence_found"

    return {
        "trade_date": trade_date.isoformat(),
        "stock_id": ALLOWED_LIVE_ORDER_TARGET_STOCK_ID,
        "status": status,
        "recommendation": recommendation,
        "recommendation_note": _describe_workflow_action(recommendation),
        "run_dir": str(run_dir),
        "task_json_status": task_status,
        "task_json_message": str(task_json.get("message", "")),
        "task_log_status": task_log_status,
        "task_log_exit_code": str((task_evidence or {}).get("exit_code", "")),
        "task_log_message": str((task_evidence or {}).get("message", "")),
        "task_log_path": str((task_evidence or {}).get("path", "")),
        "schedule_task_name": str((schedule_evidence or {}).get("task_name", "")),
        "schedule_status": schedule_status,
        "schedule_state": str((schedule_evidence or {}).get("state", "")),
        "schedule_next_run_time": next_run_time,
        "schedule_last_run_time": str((schedule_evidence or {}).get("last_run_time", "")),
        "schedule_last_task_result": str((schedule_evidence or {}).get("last_task_result", "")),
        "schedule_description": _normalize_guarded_schedule_description(
            str((schedule_evidence or {}).get("description", ""))
        ),
        "schedule_message": str((schedule_evidence or {}).get("message", "")),
        "chase_submitted": chase_submitted,
        "chase_final_state": str(chase_json.get("final_state", "")),
        "chase_final_order_id": str(chase_json.get("final_order_id", "")),
        "orders_count": len(orders_rows),
        "fills_count": fills_count,
        "positions_count": positions_count,
        "pnl_snapshots_count": len(pnl_rows),
        "artifacts": {
            "allowed_task_json": str(run_dir / "allowed_live_order_2330_task.json"),
            "chase_json": str(run_dir / "chase_2330.json"),
            "orders_csv": str(run_dir / "orders.csv"),
            "fills_csv": str(run_dir / "fills.csv"),
            "positions_csv": str(run_dir / "positions.csv"),
            "pnl_snapshots_csv": str(run_dir / "pnl_snapshots.csv"),
        },
    }


def _post_guarded_order_check_report_summary(trade_date: date) -> dict[str, object]:
    path = auto_trading_dir_for(trade_date) / "post_guarded_order_check.json"
    summary = _read_json_safely(path)
    if summary:
        normalized_summary = _normalize_post_guarded_check_for_display(summary)
        changed = normalized_summary != summary
        summary = normalized_summary

        if changed:
            try:
                path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            except OSError:
                pass
    return summary


def _sell_loop_readiness_report_summary(trade_date: date) -> dict[str, object]:
    path = auto_trading_dir_for(trade_date) / "sell_loop_readiness.json"
    summary = _read_json_safely(path)
    if summary:
        original_message = str(summary.get("post_guarded_next_run_guard_message", ""))
        normalized_message = _normalize_guarded_next_run_message(original_message)
        summary["post_guarded_next_run_guard_message"] = normalized_message
        if normalized_message != original_message:
            try:
                path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            except OSError:
                pass
    return summary


def _normalize_post_guarded_check_for_display(summary: dict[str, object] | None) -> dict[str, object]:
    if not isinstance(summary, dict):
        return {}
    normalized = dict(summary)

    original_message = str(normalized.get("next_run_guard_message", ""))
    normalized["next_run_guard_message"] = _normalize_guarded_next_run_message(original_message)

    normalized["task_log_message"] = _normalize_guarded_task_log_message(normalized.get("task_log_message", ""))
    normalized["config_timing_message"] = _workflow_note_display_text(
        normalized.get("config_timing_message", "")
    )

    schedule_message = str(normalized.get("schedule_message", ""))
    if schedule_message.startswith("scheduled task query ok via task xml"):
        normalized["schedule_message"] = schedule_message.replace(
            "scheduled task query ok via task xml",
            "已透過 task xml 讀到排程狀態",
            1,
        )
    elif schedule_message.startswith("scheduled task query ok via schtasks"):
        normalized["schedule_message"] = schedule_message.replace(
            "scheduled task query ok via schtasks",
            "已透過 schtasks 讀到排程狀態",
            1,
        )
    elif schedule_message == "scheduled task query ok":
        normalized["schedule_message"] = "已讀到 Windows 工作排程狀態"

    normalized["schedule_description"] = _normalize_guarded_schedule_description(
        normalized.get("schedule_description", "")
    )
    return normalized


def _normalize_guarded_task_log_message(message: object) -> str:
    text = str(message or "").strip()
    if not text:
        return ""
    if text in {"task log found", "已找到 task log"}:
        return "已找到任務日誌"
    if text == "latest task log could not be read":
        return "最新任務日誌無法讀取"
    return _workflow_note_display_text(text)


def _effective_guarded_post_recommendation(post_guarded_check: dict[str, object]) -> str:
    status = str(post_guarded_check.get("after_status", "")).strip()
    stored_effective_recommendation = str(post_guarded_check.get("effective_recommendation", "")).strip()
    recommendation = stored_effective_recommendation or str(post_guarded_check.get("recommendation", "")).strip()
    next_run_guard_status = str(post_guarded_check.get("next_run_guard_status", "")).strip()
    if status.startswith("skipped_"):
        guard_status_recommendations = {
            "live_guard_ready": "historical_guard_issue_already_fixed_wait_for_next_schedule",
            "allow_live_submit_disabled": "enable_allow_live_submit_before_next_scheduled_run",
            "config_live_disabled": "enable_live_in_config_before_next_scheduled_run",
            "runner_missing_auto_trade_live_env": "ensure_auto_trade_live_env_for_next_scheduled_run",
            "scheduled_task_disabled": "enable_windows_scheduled_task_before_next_scheduled_run",
            "scheduled_task_unverified": "verify_windows_scheduled_task_before_next_scheduled_run",
            "scheduled_task_not_ready": "fix_windows_scheduled_task_state_before_next_scheduled_run",
            "scheduled_task_missing_next_run_time": "fix_windows_scheduled_task_schedule_before_next_scheduled_run",
            "scheduled_task_time_passed": "historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill",
        }
        if next_run_guard_status in guard_status_recommendations:
            return guard_status_recommendations[next_run_guard_status]
    return recommendation




def _describe_workflow_action(action: object) -> str:
    return _describe_workflow_action_clean(action)


def _describe_workflow_action_clean(action: object) -> str:
    token = str(action or "").strip()
    descriptions = {
        "wait_for_scheduled_run": "等待 Windows 排程到預定時間自動執行。",
        "enable_allow_live_submit_before_next_scheduled_run": "下次排程前，先啟用 SINOPAC_ALLOW_LIVE_SUBMIT。",
        "enable_live_in_config_before_next_scheduled_run": "下次排程前，先在設定中啟用 auto_trading.live_enabled。",
        "ensure_auto_trade_live_env_for_next_scheduled_run": "下次排程前，先確認 runner 會帶上 AUTO_TRADE_LIVE=1。",
        "enable_windows_scheduled_task_before_next_scheduled_run": "下次排程前，先重新啟用 Windows 工作排程。",
        "verify_windows_scheduled_task_before_next_scheduled_run": "下次排程前，先確認 Windows 工作排程存在且設定正確。",
        "fix_windows_scheduled_task_state_before_next_scheduled_run": "下次排程前，先修正 Windows 工作排程狀態。",
        "fix_windows_scheduled_task_schedule_before_next_scheduled_run": "下次排程前，先修正 Windows 工作排程時間。",
        "historical_guard_issue_already_fixed_wait_for_next_schedule": "歷史保護條件問題已修好，等待下一次排程；今天不補單。",
        "historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill": "保護條件問題已修好；交易視窗內應補跑送單，視窗關閉後才不補。",
        "today_guarded_run_missed_wait_for_next_guarded_schedule_no_backfill": "今天的受保護下單排程已錯過；若仍在 09:10-13:20 內應補跑，視窗關閉後才等下一次排程。",
        "resolve_guarded_order_status_then_reconcile_positions": "先確認 guarded 訂單實際狀態，再決定是否回寫成交與部位。",
        "run_read_only_broker_reconcile_after_fills_exist": "等成交存在後，做只讀 broker reconcile。",
        "inspect_guarded_task_log_before_sell_loop": "先檢查受保護下單任務日誌，再決定是否進入賣出迴圈。",
        "run_post_guarded_order_check_with_live_reconcile_after_market_updates": "等市場資料更新後，跑 post_guarded_order_check --live --reconcile 做只讀核對。",
        "run_sell_loop_prepare_only_after_market_open": "開盤後先跑 sell_loop --prepare-only --live --confirm-live 做只讀賣出準備，真正送單仍等最後交易日與你的 live 命令。",
        "run_sell_loop_dry_run_or_live_with_guardrails_on_last_trade_day": "到本週最後交易日後，再依保護條件執行賣出迴圈；開盤後可先用 prepare-only 完成只讀準備。",
        "scheduled_task_time_passed": "Windows 工作排程時間已過且補跑視窗已關閉；交易視窗內應由重試排程補跑。",
        "run_prepare_week_and_finalize_from_same_day_a_source": "同日 A 預選來源已到位，先重跑 prepare_week / finalize 補出最新 sizing 產物。",
        "wait_for_next_trade_day_same_day_a_then_materialize": "今天 basket 買窗已過；等下一個交易日拿到新的同日 A 預選後，再跑 prepare_week / finalize。",
        "materialization_current_no_action_required": "本地整包產物已與同日 A 來源對齊；是否能送出新買單，改看買窗與風控。",
        "no_materialization_required_for_non_buy_day_daily_preselect": "AB 每日預選可獨立呈現；非買進日不需要展開成新的自動買單產物。",
        "align_a_source_timing_or_basket_buy_window_rule": "AB 每日預選與永豐週一買進流程已拆開；非買進日不需調整買窗。",
    }
    if token.startswith("wait_until_last_trade_day:"):
        return f"等到本週最後交易日（{token.split(':', 1)[-1]}）後再繼續。"
    return descriptions.get(token, "")


def _workflow_status_markdown(
    *,
    trade_date: date,
    state_data: dict[str, object],
    rows: list[dict[str, str]],
    selection_source: dict[str, str] | None = None,
    live_task_evidence: dict[str, str] | None = None,
    scheduled_task_evidence: dict[str, str] | None = None,
    post_guarded_check: dict[str, object] | None = None,
    sell_loop_readiness: dict[str, object] | None = None,
) -> str:
    evidence = live_task_evidence if live_task_evidence is not None else _allowed_live_task_log_evidence(trade_date)
    schedule_evidence = (
        scheduled_task_evidence if scheduled_task_evidence is not None else _scheduled_task_evidence()
    )
    resolved_selection_source = selection_source or _selection_source_summary(
        trade_date=trade_date,
        provider_name=str(state_data.get("provider_name", "")).strip(),
        preselect_count=state_data.get("preselect_count", 0),
        final_list_count=state_data.get("final_list_count", 0),
    )
    guarded_post_check = (
        post_guarded_check if post_guarded_check is not None else _post_guarded_order_check_report_summary(trade_date)
    )
    lines = [
        f"# {trade_date.isoformat()} 工作流狀態",
        "",
        "## 目前狀態",
        f"- {_workflow_current_state_label('status')}: `{_status_with_inline_label(state_data.get('status', ''))}`",
        f"- {_workflow_current_state_label('provider_name')}: `{_mapped_with_inline_label(state_data.get('provider_name', ''), PROVIDER_DISPLAY_LABELS)}`",
        f"- {_workflow_current_state_label('buy_cutoff_day')}: `{state_data.get('buy_cutoff_day', '')}`",
        f"- {_workflow_current_state_label('last_trade_day')}: `{state_data.get('last_trade_day', '')}`",
        f"- {_workflow_current_state_label('updated_at')}: `{state_data.get('updated_at', '')}`",
        *(
            [
                f"- {_workflow_current_state_label('completed_steps')}: `{state_data.get('workflow_status', {}).get('completed_steps', '')}`",
                f"- {_workflow_current_state_label('pending_steps')}: `{state_data.get('workflow_status', {}).get('pending_steps', '')}`",
                f"- {_workflow_current_state_label('closed_steps')}: `{state_data.get('workflow_status', {}).get('closed_steps', '')}`",
            ]
            if isinstance(state_data.get("workflow_status", {}), dict)
            else []
        ),
        *(
            [
                f"- {_workflow_current_state_label('today_status')}: `{_status_with_inline_label(state_data.get('today_status', ''))}`",
                f"- {_workflow_current_state_label('today_status_note')}: {_workflow_note_display_text(state_data.get('today_status_note', ''))}",
            ]
            if str(state_data.get("today_status", "")).strip() or str(state_data.get("today_status_note", "")).strip()
            else []
        ),
        *(
            [
                f"- {_workflow_current_state_label('selection_source_path')}: `{resolved_selection_source.get('selection_source_path', '')}`",
                f"- {_workflow_current_state_label('selection_source_last_modified')}: `{resolved_selection_source.get('selection_source_last_modified', '')}`",
                f"- {_workflow_current_state_label('selection_source_status')}: `{_status_with_inline_label(resolved_selection_source.get('selection_source_status', ''))}`",
                f"- {_workflow_current_state_label('selection_source_note')}: {_workflow_note_display_text(resolved_selection_source.get('selection_source_note', ''))}",
            ]
            if resolved_selection_source
            else []
        ),
        *(
            [
                f"- {_workflow_current_state_label('dashboard_refresh_status')}: `{_status_with_inline_label(_dashboard_refresh_summary(state_data).get('dashboard_refresh_status', ''))}`",
                f"- {_workflow_current_state_label('dashboard_refresh_steps')}: `{_workflow_note_display_text(_dashboard_refresh_summary(state_data).get('dashboard_refresh_steps', ''))}`",
                f"- {_workflow_current_state_label('dashboard_refresh_note')}: {_workflow_note_display_text(_dashboard_refresh_summary(state_data).get('dashboard_refresh_note', ''))}",
                f"- {_workflow_current_state_label('dashboard_refresh_trigger_status')}: `{_status_with_inline_label(_dashboard_refresh_summary(state_data).get('dashboard_refresh_trigger_status', ''))}`",
                f"- {_workflow_current_state_label('dashboard_refresh_trigger_artifacts')}: `{_dashboard_refresh_summary(state_data).get('dashboard_refresh_trigger_artifacts', '')}`",
                f"- {_workflow_current_state_label('dashboard_refresh_trigger_note')}: {_workflow_note_display_text(_dashboard_refresh_summary(state_data).get('dashboard_refresh_trigger_note', ''))}",
            ]
            if _dashboard_refresh_summary(state_data)
            else []
        ),
        *(
            [
                f"- {_workflow_current_state_label('dashboard_last_materialization_status')}: `{_status_with_inline_label(_dashboard_last_materializing_summary(state_data).get('dashboard_last_materialization_status', ''))}`",
                f"- {_workflow_current_state_label('dashboard_last_materialization_steps')}: `{_workflow_note_display_text(_dashboard_last_materializing_summary(state_data).get('dashboard_last_materialization_steps', ''))}`",
                f"- {_workflow_current_state_label('dashboard_last_materialization_note')}: {_workflow_note_display_text(_dashboard_last_materializing_summary(state_data).get('dashboard_last_materialization_note', ''))}",
                f"- {_workflow_current_state_label('dashboard_last_materialization_trigger_status')}: `{_status_with_inline_label(_dashboard_last_materializing_summary(state_data).get('dashboard_last_materialization_trigger_status', ''))}`",
                f"- {_workflow_current_state_label('dashboard_last_materialization_trigger_artifacts')}: `{_dashboard_last_materializing_summary(state_data).get('dashboard_last_materialization_trigger_artifacts', '')}`",
                f"- {_workflow_current_state_label('dashboard_last_materialization_trigger_note')}: {_workflow_note_display_text(_dashboard_last_materializing_summary(state_data).get('dashboard_last_materialization_trigger_note', ''))}",
            ]
            if _dashboard_last_materializing_summary(state_data)
            else []
        ),
        *(
            [
                f"- {_workflow_current_state_label('weekly_settlement_open')}: `{state_data.get('weekly_settlement_open', False)}`",
                f"- {_workflow_current_state_label('weekly_settlement_status')}: `{_status_with_inline_label(state_data.get('weekly_settlement_status', ''))}`",
                f"- {_workflow_current_state_label('weekly_settlement_artifacts')}: `{state_data.get('weekly_settlement_artifacts', '')}`",
                f"- {_workflow_current_state_label('weekly_settlement_note')}: {_workflow_note_display_text(state_data.get('weekly_settlement_note', ''))}",
                f"- {_workflow_current_state_label('weekly_settlement_next_action')}: `{_action_with_inline_label(state_data.get('weekly_settlement_next_action', ''))}`",
                f"- {_workflow_current_state_label('weekly_settlement_next_action_note')}: {_workflow_note_display_text(state_data.get('weekly_settlement_next_action_note', ''))}",
            ]
            if str(state_data.get("weekly_settlement_status", "")).strip()
            or str(state_data.get("weekly_settlement_note", "")).strip()
            else []
        ),
        *(
            [
                f"- {_workflow_current_state_label('today_ordering_status')}: `{_status_with_inline_label(state_data.get('today_ordering_status', ''))}`",
                f"- {_workflow_current_state_label('today_ordering_note')}: {_workflow_note_display_text(state_data.get('today_ordering_note', ''))}",
            ]
            if str(state_data.get("today_ordering_status", "")).strip() or str(state_data.get("today_ordering_note", "")).strip()
            else []
        ),
        *(
            [
                f"- {_workflow_current_state_label('today_ordering_conflict_status')}: `{_status_with_inline_label(state_data.get('today_ordering_conflict_status', ''))}`",
                f"- {_workflow_current_state_label('today_ordering_conflict_note')}: {_workflow_note_display_text(state_data.get('today_ordering_conflict_note', ''))}",
            ]
            if str(state_data.get("today_ordering_conflict_status", "")).strip()
            or str(state_data.get("today_ordering_conflict_note", "")).strip()
            else []
        ),
        *(
            [
                f"- {_workflow_current_state_label('today_ordering_conflict_resolution_status')}: `{_status_with_inline_label(state_data.get('today_ordering_conflict_resolution_status', ''))}`",
                f"- {_workflow_current_state_label('today_ordering_conflict_resolution_action')}: `{_action_with_inline_label(state_data.get('today_ordering_conflict_resolution_action', ''))}`",
                f"- {_workflow_current_state_label('today_ordering_conflict_resolution_note')}: {_workflow_note_display_text(state_data.get('today_ordering_conflict_resolution_note', ''))}",
            ]
            if str(state_data.get("today_ordering_conflict_resolution_status", "")).strip()
            or str(state_data.get("today_ordering_conflict_resolution_note", "")).strip()
            else []
        ),
        *(
            [
                f"- {_workflow_current_state_label('today_new_order_submission_open')}: `{state_data.get('today_new_order_submission_open', False)}`",
                f"- {_workflow_current_state_label('today_new_order_submission_status')}: `{_status_with_inline_label(state_data.get('today_new_order_submission_status', ''))}`",
                f"- {_workflow_current_state_label('today_new_order_submission_note')}: {_workflow_note_display_text(state_data.get('today_new_order_submission_note', ''))}",
            ]
            if str(state_data.get("today_new_order_submission_status", "")).strip()
            or str(state_data.get("today_new_order_submission_note", "")).strip()
            else []
        ),
        *(
            [
                f"- {_workflow_current_state_label('selection_source_carry_forward_open')}: `{state_data.get('selection_source_carry_forward_open', False)}`",
                f"- {_workflow_current_state_label('selection_source_carry_forward_status')}: `{_status_with_inline_label(state_data.get('selection_source_carry_forward_status', ''))}`",
                f"- {_workflow_current_state_label('selection_source_carry_forward_next_trade_day')}: `{state_data.get('selection_source_carry_forward_next_trade_day', '')}`",
                f"- {_workflow_current_state_label('selection_source_carry_forward_note')}: {_workflow_note_display_text(state_data.get('selection_source_carry_forward_note', ''))}",
            ]
            if str(state_data.get("selection_source_carry_forward_status", "")).strip()
            or str(state_data.get("selection_source_carry_forward_note", "")).strip()
            else []
        ),
        *(
            [
                f"- {_workflow_current_state_label('selection_materialization_open')}: `{state_data.get('selection_materialization_open', False)}`",
                f"- {_workflow_current_state_label('selection_materialization_status')}: `{_status_with_inline_label(state_data.get('selection_materialization_status', ''))}`",
                f"- {_workflow_current_state_label('selection_materialization_missing_artifacts')}: `{state_data.get('selection_materialization_missing_artifacts', '')}`",
                f"- {_workflow_current_state_label('selection_materialization_note')}: {_workflow_note_display_text(state_data.get('selection_materialization_note', ''))}",
                f"- {_workflow_current_state_label('selection_materialization_next_action')}: `{_action_with_inline_label(state_data.get('selection_materialization_next_action', ''))}`",
                f"- {_workflow_current_state_label('selection_materialization_next_action_note')}: {_workflow_note_display_text(state_data.get('selection_materialization_next_action_note', ''))}",
            ]
            if str(state_data.get("selection_materialization_status", "")).strip()
            or str(state_data.get("selection_materialization_note", "")).strip()
            else []
        ),
        "",
        "## 檢查清單",
        "",
        "| 步驟 | 狀態 | 檢查 | 路徑 |",
        "| --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {_workflow_step_display(row['step'])} | {_workflow_row_status_display(row['status'])} | {row['check']} | {row['path']} |"
        )

    pending = [row["step"] for row in rows if row["status"] == "pending"]
    closed = [row["step"] for row in rows if row["status"] == "closed"]
    evidence_lines = [
        "",
        "## 受保護下單任務證據",
    ]
    if evidence:
        evidence_lines.extend(
            [
                f"- {_workflow_section_label('status', WORKFLOW_GUARDED_TASK_LABELS)}: `{evidence.get('status', '')}`",
                f"- {_workflow_section_label('exit_code', WORKFLOW_GUARDED_TASK_LABELS)}: `{evidence.get('exit_code', '')}`",
                f"- {_workflow_section_label('message', WORKFLOW_GUARDED_TASK_LABELS)}: {evidence.get('message', '')}",
                f"- {_workflow_section_label('latest_log', WORKFLOW_GUARDED_TASK_LABELS)}: `{evidence.get('path', '')}`",
            ]
        )
    else:
        evidence_lines.append("- 這個 trade date 尚未找到受保護下單任務 log。")
    schedule_lines = [
        "",
        "## 受保護下單排程證據",
    ]
    if schedule_evidence:
        schedule_lines.extend(
            [
                f"- {_workflow_section_label('status', WORKFLOW_GUARDED_SCHEDULE_LABELS)}: `{schedule_evidence.get('status', '')}`",
                f"- {_workflow_section_label('task_name', WORKFLOW_GUARDED_SCHEDULE_LABELS)}: `{schedule_evidence.get('task_name', '')}`",
                f"- {_workflow_section_label('state', WORKFLOW_GUARDED_SCHEDULE_LABELS)}: `{schedule_evidence.get('state', '')}`",
                f"- {_workflow_section_label('next_run_time', WORKFLOW_GUARDED_SCHEDULE_LABELS)}: `{schedule_evidence.get('next_run_time', '')}`",
                f"- {_workflow_section_label('last_run_time', WORKFLOW_GUARDED_SCHEDULE_LABELS)}: `{schedule_evidence.get('last_run_time', '')}`",
                f"- {_workflow_section_label('last_task_result', WORKFLOW_GUARDED_SCHEDULE_LABELS)}: `{schedule_evidence.get('last_task_result', '')}`",
                f"- {_workflow_section_label('description', WORKFLOW_GUARDED_SCHEDULE_LABELS)}: {_normalize_guarded_schedule_description(schedule_evidence.get('description', ''))}",
                f"- {_workflow_section_label('message', WORKFLOW_GUARDED_SCHEDULE_LABELS)}: {schedule_evidence.get('message', '')}",
            ]
        )
    else:
        schedule_lines.append("- 目前沒有可用的受保護下單排程證據。")
    guarded_post_lines = [
        "",
        "## 受保護下單後檢查",
    ]
    if guarded_post_check:
        guarded_post_lines.extend(
            [
                f"- {_workflow_section_label('after_status', WORKFLOW_GUARDED_POST_CHECK_LABELS)}: `{guarded_post_check.get('after_status', '')}`",
                f"- {_workflow_section_label('recommendation', WORKFLOW_GUARDED_POST_CHECK_LABELS)}: `{_action_with_inline_label(guarded_post_check.get('recommendation', ''))}`",
                f"- {_workflow_section_label('recommendation_note', WORKFLOW_GUARDED_POST_CHECK_LABELS)}: {_describe_workflow_action(guarded_post_check.get('recommendation', ''))}",
                f"- {_workflow_section_label('effective_recommendation', WORKFLOW_GUARDED_POST_CHECK_LABELS)}: `{_status_with_inline_label(_effective_guarded_post_recommendation(guarded_post_check))}`",
                f"- {_workflow_section_label('effective_recommendation_note', WORKFLOW_GUARDED_POST_CHECK_LABELS)}: {_describe_workflow_action(_effective_guarded_post_recommendation(guarded_post_check))}",
                f"- {_workflow_section_label('reconciled', WORKFLOW_GUARDED_POST_CHECK_LABELS)}: `{guarded_post_check.get('reconciled', False)}`",
                f"- {_workflow_section_label('fills_count', WORKFLOW_GUARDED_POST_CHECK_LABELS)}: `{guarded_post_check.get('fills_count', 0)}`",
                f"- {_workflow_section_label('positions_count', WORKFLOW_GUARDED_POST_CHECK_LABELS)}: `{guarded_post_check.get('positions_count', 0)}`",
                f"- {_workflow_section_label('next_run_guard_status', WORKFLOW_GUARDED_POST_CHECK_LABELS)}: `{_status_with_inline_label(guarded_post_check.get('next_run_guard_status', ''))}`",
                f"- {_workflow_section_label('next_run_guard_message', WORKFLOW_GUARDED_POST_CHECK_LABELS)}: {_workflow_note_display_text(_normalize_guarded_next_run_message(guarded_post_check.get('next_run_guard_message', '')))}",
                f"- {_workflow_section_label('config_timing_status', WORKFLOW_GUARDED_POST_CHECK_LABELS)}: `{_status_with_inline_label(guarded_post_check.get('config_timing_status', ''))}`",
                f"- {_workflow_section_label('config_timing_message', WORKFLOW_GUARDED_POST_CHECK_LABELS)}: {_workflow_note_display_text(guarded_post_check.get('config_timing_message', ''))}",
                f"- {_workflow_section_label('config_path', WORKFLOW_GUARDED_POST_CHECK_LABELS)}: `{guarded_post_check.get('config_path', '')}`",
                f"- {_workflow_section_label('config_last_modified', WORKFLOW_GUARDED_POST_CHECK_LABELS)}: `{guarded_post_check.get('config_last_modified', '')}`",
                f"- {_workflow_section_label('task_recorded_at', WORKFLOW_GUARDED_POST_CHECK_LABELS)}: `{guarded_post_check.get('task_recorded_at', '')}`",
            ]
        )
    else:
        guarded_post_lines.append("- 目前沒有可用的受保護下單後檢查產物。")
    sell_readiness_lines = [
        "",
        "## 賣出就緒狀態",
    ]
    if sell_loop_readiness:
        next_action = str(sell_loop_readiness.get("next_action", "")).strip()
        post_guarded_effective = str(
            sell_loop_readiness.get("post_guarded_effective_recommendation", "")
        ).strip()
        sell_readiness_lines.extend(
            [
                f"- {_workflow_section_label('blocking_reason', WORKFLOW_SELL_READINESS_LABELS)}: `{_status_with_inline_label(sell_loop_readiness.get('blocking_reason', ''))}`",
                f"- {_workflow_section_label('post_guarded_recommendation', WORKFLOW_SELL_READINESS_LABELS)}: `{_action_with_inline_label(sell_loop_readiness.get('post_guarded_recommendation', ''))}`",
                f"- {_workflow_section_label('post_guarded_recommendation_note', WORKFLOW_SELL_READINESS_LABELS)}: {_describe_workflow_action(sell_loop_readiness.get('post_guarded_recommendation', ''))}",
                f"- {_workflow_section_label('next_action', WORKFLOW_SELL_READINESS_LABELS)}: `{_status_with_inline_label(next_action)}`",
                f"- {_workflow_section_label('next_action_note', WORKFLOW_SELL_READINESS_LABELS)}: {_describe_workflow_action(next_action)}",
                f"- {_workflow_section_label('positions_ready', WORKFLOW_SELL_READINESS_LABELS)}: `{sell_loop_readiness.get('positions_ready', False)}`",
                f"- {_workflow_section_label('positions_count', WORKFLOW_SELL_READINESS_LABELS)}: `{sell_loop_readiness.get('positions_count', 0)}`",
                f"- {_workflow_section_label('post_guarded_effective_recommendation', WORKFLOW_SELL_READINESS_LABELS)}: `{_status_with_inline_label(post_guarded_effective)}`",
                f"- {_workflow_section_label('post_guarded_effective_recommendation_note', WORKFLOW_SELL_READINESS_LABELS)}: {_describe_workflow_action(post_guarded_effective)}",
                f"- {_workflow_section_label('post_guarded_next_run_guard_status', WORKFLOW_SELL_READINESS_LABELS)}: `{_status_with_inline_label(sell_loop_readiness.get('post_guarded_next_run_guard_status', ''))}`",
                f"- {_workflow_section_label('post_guarded_next_run_guard_message', WORKFLOW_SELL_READINESS_LABELS)}: {_workflow_note_display_text(_normalize_guarded_next_run_message(sell_loop_readiness.get('post_guarded_next_run_guard_message', '')))}",
                f"- {_workflow_section_label('post_guarded_config_timing_status', WORKFLOW_SELL_READINESS_LABELS)}: `{_status_with_inline_label(sell_loop_readiness.get('post_guarded_config_timing_status', ''))}`",
                f"- {_workflow_section_label('post_guarded_config_timing_message', WORKFLOW_SELL_READINESS_LABELS)}: {_workflow_note_display_text(sell_loop_readiness.get('post_guarded_config_timing_message', ''))}",
                f"- {_workflow_section_label('post_guarded_config_path', WORKFLOW_SELL_READINESS_LABELS)}: `{sell_loop_readiness.get('post_guarded_config_path', '')}`",
                f"- {_workflow_section_label('post_guarded_config_last_modified', WORKFLOW_SELL_READINESS_LABELS)}: `{sell_loop_readiness.get('post_guarded_config_last_modified', '')}`",
                f"- {_workflow_section_label('post_guarded_task_recorded_at', WORKFLOW_SELL_READINESS_LABELS)}: `{sell_loop_readiness.get('post_guarded_task_recorded_at', '')}`",
            ]
        )
    else:
        sell_readiness_lines.append("- 目前沒有可用的賣出就緒產物。")
    lines.extend(
        [
            "",
            "## 待處理",
            *([f"- {_workflow_step_display(step)}" for step in pending] or ["- 無"]),
            "",
            "## 今日關閉",
            *([f"- {_workflow_step_display(step)}" for step in closed] or ["- 無"]),
            *evidence_lines,
            *schedule_lines,
            *guarded_post_lines,
            *sell_readiness_lines,
            "",
            "## 備註",
            "- 這份 note 只根據實際專案 artifact 產生，不依賴人工記憶。",
            "- 繼續開發功能本身，不會自動新增新的真實交易排程。",
            "- 受保護下單任務證據區只反映任務日誌，不會建立或重送委託。",
            "- 受保護下單排程證據區只查 Windows Task Scheduler；查詢失敗不會直接視為下單失敗。",
            "- 受保護下單排程應在 09:10-13:20 內補跑/重試；每次送單前都要先查券商委託，避免重複下單。",
            "",
        ]
    )
    return "\n".join(lines)


def _guarded_live_task_warning(trade_date: date, evidence: dict[str, str] | None = None) -> str:
    resolved = evidence if evidence is not None else _allowed_live_task_log_evidence(trade_date)
    if not resolved or resolved.get("status") != "failed":
        return ""
    exit_code = resolved.get("exit_code", "")
    message = resolved.get("message", "")
    path = resolved.get("path", "")
    return (
        f"Guarded live task failed for {trade_date.isoformat()}"
        f" (exit_code={exit_code}): {message}. Log: {path}"
    )


def _allowed_live_next_run_guard_summary(
    settings: Settings,
    *,
    scheduled_task_evidence: dict[str, str] | None = None,
) -> dict[str, object]:
    project_root = Path(getattr(settings, "project_root", PROJECT_ROOT))
    runner_script = project_root / "scripts" / "run_allowed_2330_live_order_task.ps1"
    runner_sets_auto_trade_live = False
    if runner_script.exists():
        try:
            script_text = runner_script.read_text(encoding="utf-8", errors="replace")
        except OSError:
            script_text = ""
        runner_sets_auto_trade_live = '$env:AUTO_TRADE_LIVE = "1"' in script_text or "$env:AUTO_TRADE_LIVE = '1'" in script_text
    allow_live_submit = bool(getattr(settings, "allow_live_submit", False))
    auto_trading = getattr(settings, "auto_trading", None)
    live_enabled = bool(getattr(auto_trading, "live_enabled", False))
    schedule_evidence = scheduled_task_evidence if scheduled_task_evidence is not None else _scheduled_task_evidence()
    schedule_status = str((schedule_evidence or {}).get("status", "")).strip().lower()
    schedule_state = str((schedule_evidence or {}).get("state", "")).strip()
    schedule_next_run_time = str((schedule_evidence or {}).get("next_run_time", "")).strip()
    schedule_message = str((schedule_evidence or {}).get("message", "")).strip()
    schedule_next_run_at = _parse_scheduler_datetime(schedule_next_run_time)
    now = datetime.now(TAIPEI)

    if not allow_live_submit:
        status = "allow_live_submit_disabled"
        message = "下一次 guarded 真實執行仍被擋住，因為尚未啟用 SINOPAC_ALLOW_LIVE_SUBMIT。"
    elif not live_enabled:
        status = "config_live_disabled"
        message = "下一次 guarded 真實執行仍被擋住，因為設定檔中的 auto_trading.live_enabled 仍為 false。"
    elif not runner_sets_auto_trade_live:
        status = "runner_missing_auto_trade_live_env"
        message = "下一次 guarded 真實執行仍被擋住，因為排程 runner 沒有設定 AUTO_TRADE_LIVE=1。"
    elif schedule_status == "disabled":
        status = "scheduled_task_disabled"
        message = "下一次 guarded 真實執行仍被擋住，因為 Windows 工作排程被停用。"
    elif schedule_status in {"query_failed", "unsupported"} or not schedule_status:
        status = "scheduled_task_unverified"
        message = "下一次 guarded 真實執行的就緒狀態尚未完整確認，因為無法核對 Windows 工作排程。"
        if schedule_message:
            message = f"{message} {schedule_message}"
    elif schedule_status not in {"ready", "running"}:
        status = "scheduled_task_not_ready"
        message = (
            "下一次 guarded 真實執行尚未就緒，因為 Windows 工作排程狀態是 "
            f"{schedule_state or schedule_status}。"
        )
    elif schedule_next_run_at is not None and schedule_next_run_at <= now:
        status = "scheduled_task_time_passed"
        message = (
            "保護條件設定現在看起來已修好，但 Windows 排程時間 "
            f"{schedule_next_run_time} 已過，且補跑視窗已關閉；今天不再送這筆 guarded 單。"
        )
    elif not schedule_next_run_time:
        status = "scheduled_task_missing_next_run_time"
        message = "下一次 guarded 真實執行的就緒狀態尚未完整確認，因為 Windows 工作排程沒有下一次執行時間。"
    else:
        status = "live_guard_ready"
        message = (
            "目前設定、排程 runner 與 Windows 工作排程看起來都已準備好，可等待下一次受保護下單真實執行"
            f"（{schedule_next_run_time}）。"
        )

    return {
        "status": status,
        "message": message,
        "runner_script_path": str(runner_script),
        "runner_sets_auto_trade_live": runner_sets_auto_trade_live,
        "allow_live_submit": allow_live_submit,
        "live_enabled": live_enabled,
    }


def _guarded_config_timing_summary(
    *,
    settings: Settings,
    trade_date: date,
    guarded_status: object = "",
    schedule_next_run_time: object = "",
) -> dict[str, object]:
    auto_trading = getattr(settings, "auto_trading", None)
    if auto_trading is None:
        return {
            "status": "",
            "message": "",
            "config_path": "",
            "config_last_modified": "",
            "task_recorded_at": "",
            "config_fixed_after_task_recorded": False,
            "config_fixed_after_scheduled_run": False,
        }
    project_root = Path(getattr(settings, "project_root", PROJECT_ROOT))
    config_path = project_root / "config" / "auto_trading.yaml"
    task_path = auto_trading_dir_for(trade_date) / "allowed_live_order_2330_task.json"
    config_last_modified = _path_modified_at_text(config_path)
    task_recorded_at = _path_modified_at_text(task_path)
    current_live_enabled = bool(getattr(auto_trading, "live_enabled", False))
    status = ""
    message = ""
    config_fixed_after_task_recorded = False
    config_fixed_after_scheduled_run = False

    if str(guarded_status).strip() == "skipped_config_live_disabled":
        config_last_modified_at = _parse_scheduler_datetime(config_last_modified)
        task_recorded_time = _parse_scheduler_datetime(task_recorded_at)
        scheduled_run_at = _parse_scheduler_datetime(str(schedule_next_run_time or "").strip())

        if not config_path.exists():
            status = "config_file_missing"
            message = "找不到 config/auto_trading.yaml，無法核對這次 guarded skip 之後設定是何時修好的。"
        elif not current_live_enabled:
            status = "live_enabled_still_disabled"
            message = "目前設定檔中的 auto_trading.live_enabled 仍為 false，guard 還沒修好。"
        elif config_last_modified_at is not None and task_recorded_time is not None and config_last_modified_at > task_recorded_time:
            config_fixed_after_task_recorded = True
            if scheduled_run_at is not None and config_last_modified_at > scheduled_run_at:
                config_fixed_after_scheduled_run = True
                status = "live_enabled_fixed_after_scheduled_run"
                message = (
                    "設定檔在 "
                    f"{config_last_modified} 才改成 auto_trading.live_enabled=true，"
                    f"晚於本次排程時間 {schedule_next_run_time}；所以今天這次受保護下單執行仍被略過。"
                )
            else:
                status = "live_enabled_fixed_after_task_recorded"
                message = (
                    "設定檔在 "
                    f"{config_last_modified} 才改成 auto_trading.live_enabled=true，"
                    f"晚於任務記錄時間 {task_recorded_at}；所以這次受保護下單執行仍被略過。"
                )

    return {
        "status": status,
        "message": message,
        "config_path": str(config_path),
        "config_last_modified": config_last_modified,
        "task_recorded_at": task_recorded_at,
        "config_fixed_after_task_recorded": config_fixed_after_task_recorded,
        "config_fixed_after_scheduled_run": config_fixed_after_scheduled_run,
    }


def _buy_loop_can_go_live(
    settings: Settings,
    *,
    live: bool,
    confirm_live: bool,
    trade_date: date | None = None,
) -> tuple[bool, str]:
    if not live:
        return False, "live_not_requested"
    evaluate_guard = getattr(settings, "evaluate_live_submit_guard", None)
    if callable(evaluate_guard):
        return evaluate_guard(confirm_live=confirm_live, trade_date=trade_date)
    if not getattr(settings, "allow_live_submit", False):
        return False, "allow_live_submit_disabled"
    auto_trading = getattr(settings, "auto_trading", None)
    if not getattr(auto_trading, "live_enabled", False):
        return False, "config_live_disabled"
    if hasattr(auto_trading, "weekly_execution_enabled") and not getattr(
        auto_trading,
        "weekly_execution_enabled",
        False,
    ):
        return False, "weekly_execution_disabled"
    if hasattr(auto_trading, "weekly_budget") and float(getattr(auto_trading, "weekly_budget", 0.0) or 0.0) <= 0:
        return False, "weekly_budget_missing"
    if not confirm_live:
        return False, "confirm_live_missing"
    confirm_method = getattr(settings, "live_trading_confirmed", None)
    if callable(confirm_method):
        if not confirm_method(confirm_live=confirm_live):
            return False, "auto_trade_live_env_missing"
    elif os.getenv("AUTO_TRADE_LIVE") != "1":
        return False, "auto_trade_live_env_missing"
    return True, "live_confirmed"


def _buy_loop_sizing_budget_guard(
    settings: Settings,
    state_data: dict[str, object],
    *,
    trade_date: date,
) -> tuple[bool, str]:
    if not hasattr(settings.auto_trading, "weekly_budget") or not hasattr(settings.auto_trading, "hard_budget"):
        return True, "sizing_budget_not_applicable"
    if "sizing_weekly_budget" not in state_data or "sizing_hard_budget" not in state_data:
        return False, "sizing_budget_snapshot_missing"
    snapshot_budget = _as_float(state_data.get("sizing_weekly_budget"), -1.0)
    snapshot_hard_budget = _as_float(state_data.get("sizing_hard_budget"), -1.0)
    if abs(snapshot_budget - settings.auto_trading.weekly_budget) > 0.01:
        return False, "sizing_budget_mismatch"
    if abs(snapshot_hard_budget - settings.auto_trading.hard_budget) > 0.01:
        return False, "sizing_budget_mismatch"
    snapshot_week_id = str(state_data.get("sizing_weekly_execution_week_id") or "").strip()
    if snapshot_week_id and snapshot_week_id != weekly_execution_week_id_for(trade_date):
        return False, "sizing_week_mismatch"
    return True, "sizing_budget_confirmed"


def _secondary_add_allowed_on_trade_date(settings: Settings, trade_date: date, plan) -> bool:
    return secondary_add_active_for_trade_date(
        trade_date=trade_date,
        week_trade_days=getattr(plan, "week_trade_days", None),
        auto=settings.auto_trading,
        smoke_test=False,
    )


def _sell_order_lot_for_qty(quantity: int) -> str:
    return "common" if quantity >= 1000 and quantity % 1000 == 0 else "intraday_odd_lot"


def _managed_order_from_snapshot(snapshot: ManagedOrderSnapshot) -> ManagedOrder:
    return ManagedOrder(
        strategy_lot_id=snapshot.order_id,
        stock_id=snapshot.stock_id,
        order_id=snapshot.order_id,
        order_price=snapshot.order_price,
        order_qty=snapshot.order_qty,
        filled_qty=snapshot.filled_qty,
        remaining_qty=snapshot.remaining_qty,
        active=snapshot.status == "active",
    )


def _wait_for_cancel_resolution(
    broker: ShioajiSinoPacBrokerAdapter,
    order_id: str,
    *,
    timeout_seconds: int = 20,
) -> ManagedOrderSnapshot | None:
    deadline = time_module.time() + timeout_seconds
    latest = broker.get_managed_order(order_id)
    while time_module.time() < deadline:
        if latest is None or latest.status in {"cancelled", "filled", "failed", "unknown"}:
            return latest
        time_module.sleep(2)
        latest = broker.get_managed_order(order_id)
    return latest


def _buy_loop_quote(
    *,
    stock_id: str,
    estimated_buy_price: float,
    quote_provider,
    broker: FakeBrokerAdapter | ShioajiSinoPacBrokerAdapter,
    can_go_live: bool,
) -> tuple[QuoteState, str, str, str]:
    if can_go_live and isinstance(broker, ShioajiSinoPacBrokerAdapter):
        return broker.get_quote_state(stock_id)
    snapshot = quote_provider.get_snapshot(stock_id) if quote_provider else None
    quote = QuoteState(
        last_price=snapshot.last_price if snapshot else estimated_buy_price,
        bid1=snapshot.bid1 if snapshot else None,
        ask1=snapshot.ask1 if snapshot else None,
    )
    return quote, stock_id, "", snapshot.timestamp.isoformat() if snapshot else datetime.now(TAIPEI).isoformat()


def _strategy_lot_id(trade_date: date, stock_id: str, basket_tag: object = DEFAULT_BASKET_TAG) -> str:
    return strategy_lot_id_for(trade_date, stock_id, basket_tag)


def _broker_custom_field(strategy_lot_id: object, prefix: object = "AT") -> str:
    return broker_custom_field_for_strategy_lot(strategy_lot_id, prefix=prefix)


def _strategy_lot_candidates_by_stock(trade_date: date) -> dict[str, list[str]]:
    candidates: dict[str, set[str]] = {}
    for row in load_week_lot_ledger(trade_date):
        stock_id = str(row.get("stock_id", "")).strip()
        strategy_lot_id = str(row.get("strategy_lot_id", "")).strip()
        if not stock_id or not strategy_lot_id:
            continue
        candidates.setdefault(stock_id, set()).add(strategy_lot_id)
    return {stock_id: sorted(strategy_lot_ids) for stock_id, strategy_lot_ids in candidates.items()}


def _resolve_strategy_lot_for_fill(
    *,
    trade_date: date,
    stock_id: str,
    fill: dict[str, object],
    strategy_lot_by_order_id: dict[str, str],
    strategy_lot_by_custom_field: dict[str, str],
    strategy_lot_candidates_by_stock: dict[str, list[str]],
) -> tuple[str, str]:
    explicit_strategy_lot_id = str(fill.get("strategy_lot_id", "")).strip()
    if explicit_strategy_lot_id:
        return explicit_strategy_lot_id, "resolved_broker_strategy_lot"
    order_id = str(fill.get("order_id", "")).strip()
    mapped_by_order_id = strategy_lot_by_order_id.get(order_id, "")
    if mapped_by_order_id:
        return mapped_by_order_id, "resolved_order_id"
    broker_custom_field = str(fill.get("broker_custom_field", "")).strip()
    mapped_by_custom_field = strategy_lot_by_custom_field.get(broker_custom_field, "")
    if mapped_by_custom_field:
        return mapped_by_custom_field, "resolved_custom_field"
    stock_candidates = strategy_lot_candidates_by_stock.get(stock_id, [])
    if len(stock_candidates) == 1:
        return stock_candidates[0], "resolved_unique_stock_lot"
    if len(stock_candidates) > 1:
        return "", "ambiguous_unmapped_fill"
    return _strategy_lot_id(trade_date, stock_id), "resolved_default_trade_date"


def _affordable_order_qty(
    *,
    requested_qty: int,
    target_price: float,
    remaining_budget: float,
    order_lot: str = "intraday_odd_lot",
    fees=None,
    buffer_multiplier: float = 1.0,
) -> int:
    return affordable_buy_qty(
        requested_qty=requested_qty,
        target_price=target_price,
        remaining_budget=remaining_budget,
        order_lot=order_lot,
        fees=fees,
        buffer_multiplier=buffer_multiplier,
    )


def _normalize_fill_side(raw: object) -> str:
    return normalize_fill_side(raw)


def _selected_fill_rows(
    *,
    broker: ShioajiSinoPacBrokerAdapter,
    trade_date: date,
    target_stock_ids: set[str],
) -> list[dict[str, object]]:
    fills = broker.get_fills(since=datetime.combine(trade_date, time.min, tzinfo=TAIPEI))
    strategy_lot_by_order_id = _strategy_lot_lookup_by_order_id(trade_date)
    strategy_lot_by_custom_field = _strategy_lot_lookup_by_custom_field(trade_date)
    strategy_lot_candidates_by_stock = _strategy_lot_candidates_by_stock(trade_date)
    rows: list[dict[str, object]] = []
    for fill in fills:
        stock_id = str(fill.get("stock_id", "")).strip()
        if not stock_id or stock_id not in target_stock_ids:
            continue
        order_id = str(fill.get("order_id", "")).strip()
        broker_custom_field = str(fill.get("broker_custom_field", "")).strip()
        strategy_lot_id, fill_assignment_status = _resolve_strategy_lot_for_fill(
            trade_date=trade_date,
            stock_id=stock_id,
            fill=fill,
            strategy_lot_by_order_id=strategy_lot_by_order_id,
            strategy_lot_by_custom_field=strategy_lot_by_custom_field,
            strategy_lot_candidates_by_stock=strategy_lot_candidates_by_stock,
        )
        rows.append(
            {
                "run_id": _run_id(trade_date),
                "strategy_lot_id": strategy_lot_id,
                "basket_tag": basket_tag_from_strategy_lot_id(strategy_lot_id) if strategy_lot_id else "",
                "stock_id": stock_id,
                "side": _normalize_fill_side(fill.get("side", "")),
                "fill_price": _as_float(fill.get("fill_price")),
                "fill_qty": _as_int(fill.get("fill_qty")),
                "fee": 0.0,
                "tax": 0.0,
                "fill_time": fill.get("fill_time", ""),
                "broker_fill_id": order_id,
                "broker_custom_field": broker_custom_field,
                "fill_assignment_status": fill_assignment_status,
            }
        )
    return rows


def _strategy_lot_lookup_by_order_id(trade_date: date) -> dict[str, str]:
    return load_week_order_id_lot_lookup(trade_date)


def _strategy_lot_lookup_by_custom_field(trade_date: date) -> dict[str, str]:
    return load_week_custom_field_lot_lookup(trade_date)


def _ambiguous_fill_rows(*, trade_date: date) -> list[dict[str, str]]:
    run_dir = auto_trading_dir_for(trade_date)
    return [
        row
        for row in _read_csv_rows(run_dir / "fills.csv")
        if str(row.get("fill_assignment_status", "")).strip() == "ambiguous_unmapped_fill"
    ]


def _ambiguous_fill_stock_ids(fills_rows: list[dict[str, object]]) -> list[str]:
    return sorted(
        {
            str(row.get("stock_id", "")).strip()
            for row in fills_rows
            if str(row.get("fill_assignment_status", "")).strip() == "ambiguous_unmapped_fill"
            and str(row.get("stock_id", "")).strip()
        }
    )


def _build_ambiguous_fill_report_rows(*, trade_date: date) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for row in _ambiguous_fill_rows(trade_date=trade_date):
        rows.append(
            {
                "stock_id": row.get("stock_id", ""),
                "side": row.get("side", ""),
                "fill_qty": _as_int(row.get("fill_qty"), 0),
                "fill_price": _as_float(row.get("fill_price"), 0.0),
                "fill_time": row.get("fill_time", ""),
                "broker_fill_id": row.get("broker_fill_id", ""),
                "broker_custom_field": row.get("broker_custom_field", ""),
                "fill_assignment_status": row.get("fill_assignment_status", ""),
            }
        )
    return rows


def _build_broker_underheld_report_rows(*, trade_date: date) -> list[dict[str, object]]:
    run_dir = auto_trading_dir_for(trade_date)
    rows: list[dict[str, object]] = []
    for row in _read_csv_rows(run_dir / "broker_position_mismatches.csv"):
        rows.append(
            {
                "stock_id": row.get("stock_id", ""),
                "stock_name": row.get("stock_name", ""),
                "broker_qty": _as_int(row.get("broker_qty"), 0),
                "strategy_qty": _as_int(row.get("strategy_qty"), 0),
                "missing_qty": _as_int(row.get("missing_qty"), 0),
                "reason": row.get("reason", ""),
            }
        )
    return rows


def _sell_fill_stats_by_stock(
    *,
    fills_rows: list[dict[str, object]],
    positions: list[StrategyPosition],
    fees,
) -> dict[str, dict[str, object]]:
    return compute_sell_fill_stats(
        fills_rows=fills_rows,
        opening_positions=positions,
        selection_meta_by_stock={
            position.stock_id: {"stock_name": position.stock_name, "source": position.source, "basket_tag": position.basket_tag}
            for position in positions
        },
        selection_meta_by_strategy_lot={
            position.strategy_lot_id: {
                "stock_name": position.stock_name,
                "source": position.source,
                "basket_tag": position.basket_tag,
            }
            for position in positions
        },
        strategy_lot_id_for_stock=lambda stock_id: next((position.strategy_lot_id for position in positions if position.stock_id == stock_id), stock_id),
        fees=fees,
    )


@dataclass(slots=True)
class _ExistingBuyOrderState:
    order_id: str
    order_price: float
    status: str
    gate_reason: str
    filled_qty: int
    remaining_qty: int


def _buy_status_blocks_resubmit(status: object) -> bool:
    normalized = str(status or "").strip().lower()
    if not normalized:
        return False
    if any(keyword in normalized for keyword in ("cancel", "fail", "reject", "error")):
        return False
    if normalized in {
        "unknown",
        "ready_to_submit",
        "blocked_stale_quote",
        "blocked_insufficient_cash",
        "idle",
        "dry_run_keep",
        "secondary_add_trade_day_closed",
    }:
        return False
    return normalized == "active" or "submit" in normalized or "fill" in normalized


def _existing_buy_order_state(
    *,
    existing_row: dict[str, object] | None,
    broker,
    default_remaining_qty: int,
) -> _ExistingBuyOrderState | None:
    row = existing_row or {}
    order_id = str(row.get("order_id", "")).strip()
    if not order_id:
        return None
    snapshot = broker.get_managed_order(order_id) if broker is not None else None
    if snapshot is not None and snapshot.status in {"active", "filled"}:
        return _ExistingBuyOrderState(
            order_id=snapshot.order_id,
            order_price=snapshot.order_price,
            status=snapshot.status,
            gate_reason=f"existing_buy_order_{snapshot.status}",
            filled_qty=snapshot.filled_qty,
            remaining_qty=snapshot.remaining_qty,
        )
    if not _buy_status_blocks_resubmit(row.get("status", "")):
        return None
    return _ExistingBuyOrderState(
        order_id=order_id,
        order_price=_as_float(row.get("order_price")),
        status=str(row.get("status", "")).strip() or "existing_order_unverified",
        gate_reason="existing_buy_order_unverified",
        filled_qty=_as_int(row.get("filled_qty")),
        remaining_qty=_as_int(
            row.get("active_order_qty"),
            _as_int(row.get("remaining_qty"), default_remaining_qty),
        ),
    )


def _broker_custom_field_buy_order_state(
    *,
    broker,
    broker_custom_field: str,
    stock_id: str,
) -> _ExistingBuyOrderState | None:
    finder = getattr(broker, "get_managed_order_by_custom_field", None)
    if not callable(finder):
        return None
    try:
        snapshot = finder(broker_custom_field, side="Buy", stock_id=stock_id)
    except TypeError:
        snapshot = finder(broker_custom_field)
    if snapshot is None or snapshot.status not in {"active", "filled"}:
        return None
    return _ExistingBuyOrderState(
        order_id=snapshot.order_id,
        order_price=snapshot.order_price,
        status=snapshot.status,
        gate_reason=f"broker_custom_field_buy_order_{snapshot.status}",
        filled_qty=snapshot.filled_qty,
        remaining_qty=snapshot.remaining_qty,
    )


@dataclass(slots=True)
class _ExistingSellOrderState:
    order_id: str
    order_price: float
    status: str
    gate_reason: str
    filled_qty: int
    remaining_qty: int


def _sell_status_blocks_resubmit(status: object) -> bool:
    normalized = str(status or "").strip().lower()
    if not normalized:
        return False
    if normalized in {"cancelled", "failed", "rejected", "unknown", "ready_to_submit"}:
        return False
    return normalized in {
        "active",
        "filled",
        "filled_or_partially_filled",
        "submitted",
        "presubmitted",
        "pendingsubmit",
        "partfilled",
    }


def _existing_sell_order_state(
    *,
    existing_row: dict[str, object] | None,
    broker,
    default_remaining_qty: int,
) -> _ExistingSellOrderState | None:
    row = existing_row or {}
    order_id = str(row.get("sell_order_id", "")).strip()
    if not order_id:
        return None
    snapshot = broker.get_managed_order(order_id) if broker is not None else None
    if snapshot is not None and snapshot.status in {"active", "filled"}:
        return _ExistingSellOrderState(
            order_id=snapshot.order_id,
            order_price=snapshot.order_price,
            status=snapshot.status,
            gate_reason=f"existing_sell_order_{snapshot.status}",
            filled_qty=snapshot.filled_qty,
            remaining_qty=snapshot.remaining_qty,
        )
    if not _sell_status_blocks_resubmit(row.get("sell_order_status", "")):
        return None
    return _ExistingSellOrderState(
        order_id=order_id,
        order_price=_as_float(row.get("sell_order_price")),
        status=str(row.get("sell_order_status", "")).strip() or "existing_order_unverified",
        gate_reason="existing_sell_order_unverified",
        filled_qty=_as_int(row.get("sold_qty")),
        remaining_qty=_as_int(row.get("remaining_qty"), default_remaining_qty),
    )


def _positions_rows_from_fills(
    *,
    trade_date: date,
    fills_rows: list[dict[str, object]],
    selection_meta_by_stock: dict[str, dict[str, object]],
    quote_rows_by_stock: dict[str, QuoteState],
    selection_meta_by_strategy_lot: dict[str, dict[str, object]] | None = None,
    opening_positions: list[StrategyPosition] | None = None,
) -> list[dict[str, object]]:
    opening_strategy_lot_by_stock = {
        position.stock_id: position.strategy_lot_id
        for position in opening_positions or []
        if str(position.stock_id).strip()
    }
    return build_positions_rows_from_fills(
        run_id=_run_id(trade_date),
        trade_date=trade_date,
        fills_rows=fills_rows,
        selection_meta_by_stock=selection_meta_by_stock,
        selection_meta_by_strategy_lot=selection_meta_by_strategy_lot,
        quote_rows_by_stock=quote_rows_by_stock,
        opening_positions=opening_positions,
        strategy_lot_id_for_stock=lambda stock_id: opening_strategy_lot_by_stock.get(
            stock_id,
            _strategy_lot_id(trade_date, stock_id),
        ),
    )


def _quote_last_price_value(quote: object, default: float = 0.0) -> float:
    if isinstance(quote, QuoteState):
        return _as_float(quote.last_price, default)
    if isinstance(quote, dict):
        return _as_float(quote.get("last_price"), default)
    return _as_float(getattr(quote, "last_price", default), default)


def _positions_rows_from_local_orders(
    *,
    trade_date: date,
    order_rows: list[dict[str, object]],
    selection_meta_by_stock: dict[str, dict[str, object]],
    quote_rows_by_stock: dict[str, QuoteState],
    selection_meta_by_strategy_lot: dict[str, dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for row in order_rows:
        stock_id = str(row.get("stock_id", "")).strip()
        if not stock_id:
            continue
        holding_qty = _as_int(row.get("filled_qty"), 0)
        if holding_qty <= 0:
            continue
        strategy_lot_id = str(row.get("strategy_lot_id", "")).strip() or _strategy_lot_id(
            trade_date,
            stock_id,
            row.get("basket_tag"),
        )
        meta = (selection_meta_by_strategy_lot or {}).get(strategy_lot_id, {}) or selection_meta_by_stock.get(stock_id, {})
        buy_avg_price = _as_float(
            row.get("order_price"),
            _as_float(row.get("target_price"), _as_float(row.get("last_price"), 0.0)),
        )
        current_price = _quote_last_price_value(
            quote_rows_by_stock.get(stock_id),
            _as_float(row.get("last_price"), buy_avg_price),
        )
        rows.append(
            {
                "run_id": _run_id(trade_date),
                "strategy_lot_id": strategy_lot_id,
                "stock_id": stock_id,
                "stock_name": meta.get("stock_name", row.get("stock_name", stock_id)),
                "source": meta.get("source", row.get("source", "")),
                "basket_tag": normalize_basket_tag(meta.get("basket_tag") or row.get("basket_tag")),
                "holding_qty": holding_qty,
                "buy_avg_price": buy_avg_price,
                "buy_total_cost": buy_avg_price * holding_qty,
                "current_price": current_price,
                "status": "local_order_fill_fallback",
            }
        )
    return rows


def _positions_rows_from_strategy_positions(
    *,
    trade_date: date,
    positions: list[StrategyPosition],
    quote_rows_by_stock: dict[str, dict[str, str]] | dict[str, QuoteState],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for position in positions:
        stock_id = str(position.stock_id).strip()
        if not stock_id or int(position.holding_qty) <= 0:
            continue
        current_price = _quote_last_price_value(
            quote_rows_by_stock.get(stock_id),
            position.buy_avg_price,
        )
        rows.append(
            {
                "run_id": _run_id(trade_date),
                "strategy_lot_id": position.strategy_lot_id,
                "stock_id": stock_id,
                "stock_name": position.stock_name or stock_id,
                "source": position.source,
                "basket_tag": normalize_basket_tag(position.basket_tag),
                "holding_qty": int(position.holding_qty),
                "buy_avg_price": position.buy_avg_price,
                "buy_total_cost": position.buy_total_cost,
                "current_price": current_price,
                "status": "opening_positions_fallback",
            }
        )
    return rows


def _apply_local_sell_state_to_positions_rows(
    *,
    trade_date: date,
    positions_rows: list[dict[str, object]],
    opening_positions: list[StrategyPosition],
    sell_rows: list[dict[str, object]],
    quote_rows_by_stock: dict[str, QuoteState],
) -> tuple[list[dict[str, object]], list[str]]:
    opening_positions_by_lot = {
        str(position.strategy_lot_id).strip(): position
        for position in opening_positions
        if str(position.strategy_lot_id).strip()
    }
    sell_rows_by_lot = {
        str(row.get("strategy_lot_id", "")).strip(): row
        for row in sell_rows
        if str(row.get("strategy_lot_id", "")).strip()
    }
    adjusted_rows: list[dict[str, object]] = []
    adjusted_lot_ids: list[str] = []
    for row in positions_rows:
        strategy_lot_id = str(row.get("strategy_lot_id", "")).strip()
        opening_position = opening_positions_by_lot.get(strategy_lot_id)
        local_sell_row = sell_rows_by_lot.get(strategy_lot_id)
        if opening_position is None or local_sell_row is None:
            adjusted_rows.append(row)
            continue
        opening_qty = max(int(opening_position.holding_qty), 0)
        local_sold_qty = max(_as_int(local_sell_row.get("sold_qty"), 0), 0)
        local_remaining_qty = _as_int(local_sell_row.get("remaining_qty"), max(opening_qty - local_sold_qty, 0))
        local_remaining_qty = max(min(local_remaining_qty, opening_qty), 0)
        current_qty = max(_as_int(row.get("holding_qty"), 0), 0)
        if local_remaining_qty >= current_qty:
            adjusted_rows.append(row)
            continue
        adjusted_lot_ids.append(strategy_lot_id)
        if local_remaining_qty <= 0:
            continue
        current_price = _quote_last_price_value(
            quote_rows_by_stock.get(opening_position.stock_id),
            _as_float(row.get("current_price"), opening_position.buy_avg_price),
        )
        adjusted_rows.append(
            {
                "run_id": row.get("run_id", _run_id(trade_date)),
                "strategy_lot_id": strategy_lot_id,
                "stock_id": opening_position.stock_id,
                "stock_name": row.get("stock_name", opening_position.stock_name),
                "source": row.get("source", opening_position.source),
                "basket_tag": normalize_basket_tag(row.get("basket_tag") or opening_position.basket_tag),
                "holding_qty": local_remaining_qty,
                "buy_avg_price": opening_position.buy_avg_price,
                "buy_total_cost": opening_position.buy_avg_price * local_remaining_qty,
                "current_price": current_price,
                "status": "local_sell_fill_fallback",
            }
        )
    return adjusted_rows, adjusted_lot_ids


def _apply_local_sell_pnl_fallback(
    *,
    sell_rows: list[dict[str, object]],
    opening_positions: list[StrategyPosition],
    fees,
) -> tuple[list[dict[str, object]], list[str]]:
    opening_positions_by_lot = {
        str(position.strategy_lot_id).strip(): position
        for position in opening_positions
        if str(position.strategy_lot_id).strip()
    }
    updated_rows: list[dict[str, object]] = []
    fallback_lot_ids: list[str] = []
    for row in sell_rows:
        row_copy: dict[str, object] = dict(row)
        strategy_lot_id = str(row.get("strategy_lot_id", "")).strip()
        opening_position = opening_positions_by_lot.get(strategy_lot_id)
        sold_qty = max(_as_int(row.get("sold_qty"), 0), 0)
        if opening_position is None or sold_qty <= 0:
            updated_rows.append(row_copy)
            continue
        if (
            row.get("actual_fill_avg_price", "") not in ("", None)
            and row.get("allocated_buy_cost", "") not in ("", None)
            and row.get("realized_pnl", "") not in ("", None)
        ):
            updated_rows.append(row_copy)
            continue
        fill_avg_price = _as_float(
            row.get("actual_fill_avg_price"),
            _as_float(row.get("sell_order_price"), _as_float(row.get("conservative_sell_price"), 0.0)),
        )
        if fill_avg_price <= 0:
            updated_rows.append(row_copy)
            continue
        capped_sold_qty = min(sold_qty, max(int(opening_position.holding_qty), 0))
        if capped_sold_qty <= 0:
            updated_rows.append(row_copy)
            continue
        allocated_buy_cost = opening_position.buy_avg_price * capped_sold_qty
        gross = fill_avg_price * capped_sold_qty
        sell_fee = fees.estimate_sell_fee(gross)
        sell_tax = fees.estimate_sell_tax(gross)
        row_copy["actual_fill_avg_price"] = fill_avg_price
        row_copy["allocated_buy_cost"] = allocated_buy_cost
        row_copy["realized_pnl"] = gross - sell_fee - sell_tax - allocated_buy_cost
        row_copy["sell_pnl_source"] = "local_sell_order_fallback"
        fallback_lot_ids.append(strategy_lot_id)
        updated_rows.append(row_copy)
    return updated_rows, fallback_lot_ids


def _excluded_positions_rows(
    *,
    broker_positions: list,
    strategy_positions_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    return build_excluded_positions_rows(
        broker_positions=broker_positions,
        strategy_positions_rows=strategy_positions_rows,
    )


def _broker_qty_below_strategy_guard_rows(
    *,
    broker_positions: list,
    opening_positions: list[StrategyPosition],
) -> list[dict[str, object]]:
    strategy_qty_by_stock: dict[str, int] = {}
    strategy_name_by_stock: dict[str, str] = {}
    for position in opening_positions:
        stock_id = str(position.stock_id).strip()
        if not stock_id:
            continue
        strategy_qty_by_stock[stock_id] = strategy_qty_by_stock.get(stock_id, 0) + int(position.holding_qty)
        stock_name = str(position.stock_name).strip()
        if stock_name and stock_id not in strategy_name_by_stock:
            strategy_name_by_stock[stock_id] = stock_name

    broker_qty_by_stock: dict[str, int] = {}
    broker_name_by_stock: dict[str, str] = {}
    for position in broker_positions:
        stock_id = str(position.stock_id).strip()
        if not stock_id:
            continue
        broker_qty_by_stock[stock_id] = broker_qty_by_stock.get(stock_id, 0) + int(position.quantity)
        stock_name = str(getattr(position, "stock_name", "") or "").strip()
        if stock_name and stock_id not in broker_name_by_stock:
            broker_name_by_stock[stock_id] = stock_name

    rows: list[dict[str, object]] = []
    for stock_id, strategy_qty in sorted(strategy_qty_by_stock.items()):
        if strategy_qty <= 0:
            continue
        broker_qty = broker_qty_by_stock.get(stock_id, 0)
        if broker_qty >= strategy_qty:
            continue
        rows.append(
            {
                "stock_id": stock_id,
                "stock_name": strategy_name_by_stock.get(stock_id, broker_name_by_stock.get(stock_id, stock_id)),
                "broker_qty": broker_qty,
                "strategy_qty": strategy_qty,
                "missing_qty": strategy_qty - broker_qty,
                "reason": "broker_qty_below_strategy_qty",
            }
        )
    return rows


def _allocate_broker_qty_by_opening_lot(
    broker_qty: int,
    opening_positions: list[StrategyPosition],
) -> list[tuple[StrategyPosition, int]]:
    scoped_positions = [position for position in opening_positions if int(position.holding_qty) > 0]
    if broker_qty <= 0 or not scoped_positions:
        return []
    total_opening_qty = sum(int(position.holding_qty) for position in scoped_positions)
    if total_opening_qty <= 0:
        return []
    allocations: list[int] = []
    remainders: list[tuple[float, str, int]] = []
    assigned_qty = 0
    for index, position in enumerate(scoped_positions):
        raw_qty = broker_qty * int(position.holding_qty) / total_opening_qty
        allocated_qty = int(raw_qty)
        allocations.append(allocated_qty)
        remainders.append((raw_qty - allocated_qty, position.strategy_lot_id, index))
        assigned_qty += allocated_qty
    remaining_qty = broker_qty - assigned_qty
    for _, _, index in sorted(remainders, key=lambda item: (-item[0], item[1]))[:remaining_qty]:
        allocations[index] += 1
    return [
        (position, allocations[index])
        for index, position in enumerate(scoped_positions)
        if allocations[index] > 0
    ]


def _selected_positions_rows(
    *,
    trade_date: date,
    broker: ShioajiSinoPacBrokerAdapter,
    target_stock_ids: set[str],
    selection_meta_by_stock: dict[str, dict[str, object]],
    quote_rows_by_stock: dict[str, QuoteState],
    selection_meta_by_strategy_lot: dict[str, dict[str, object]] | None = None,
    opening_positions: list[StrategyPosition] | None = None,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for position in broker.get_positions():
        if position.stock_id not in target_stock_ids:
            continue
        quote = quote_rows_by_stock.get(position.stock_id)
        opening_lots = [
            lot
            for lot in (opening_positions or [])
            if str(lot.stock_id).strip() == position.stock_id and int(lot.holding_qty) > 0
        ]
        allocated_rows = _allocate_broker_qty_by_opening_lot(int(position.quantity), opening_lots)
        if allocated_rows:
            for opening_lot, allocated_qty in allocated_rows:
                meta = (selection_meta_by_strategy_lot or {}).get(opening_lot.strategy_lot_id, {})
                rows.append(
                    {
                        "run_id": _run_id(trade_date),
                        "strategy_lot_id": opening_lot.strategy_lot_id,
                        "stock_id": position.stock_id,
                        "stock_name": meta.get("stock_name", opening_lot.stock_name or position.stock_name),
                        "source": meta.get("source", opening_lot.source),
                        "basket_tag": normalize_basket_tag(meta.get("basket_tag") or opening_lot.basket_tag),
                        "holding_qty": allocated_qty,
                        "buy_avg_price": opening_lot.buy_avg_price,
                        "buy_total_cost": opening_lot.buy_avg_price * allocated_qty,
                        "current_price": quote.last_price if quote else position.avg_price,
                        "status": "broker_snapshot_opening_lot_scaled",
                    }
                )
            continue

        meta = selection_meta_by_stock.get(position.stock_id, {})
        basket_tag = normalize_basket_tag(meta.get("basket_tag"))
        strategy_lot_id = _strategy_lot_id(trade_date, position.stock_id, basket_tag)
        if selection_meta_by_strategy_lot and strategy_lot_id in selection_meta_by_strategy_lot:
            meta = selection_meta_by_strategy_lot[strategy_lot_id]
        rows.append(
            {
                "run_id": _run_id(trade_date),
                "strategy_lot_id": strategy_lot_id,
                "stock_id": position.stock_id,
                "stock_name": meta.get("stock_name", position.stock_name),
                "source": meta.get("source", ""),
                "basket_tag": normalize_basket_tag(meta.get("basket_tag")),
                "holding_qty": position.quantity,
                "buy_avg_price": position.avg_price,
                "buy_total_cost": position.avg_price * position.quantity,
                "current_price": quote.last_price if quote else position.avg_price,
                "status": "broker_snapshot_unscoped",
            }
        )
    return rows


def _stock_ids_from_local_rows(rows: list[dict[str, object]]) -> set[str]:
    return {
        str(row.get("stock_id", "")).strip()
        for row in rows
        if str(row.get("stock_id", "")).strip()
    }


def _reconcile_target_stock_ids(trade_date: date, explicit_stock_ids: list[str] | None = None) -> set[str]:
    run_dir = auto_trading_dir_for(trade_date)
    target_stock_ids = {
        str(stock_id).strip()
        for stock_id in (explicit_stock_ids or [])
        if str(stock_id).strip()
    }

    for filename in ("orders.csv", "sell_decisions.csv", "positions.csv", "sizing.csv", "quote_snapshots.csv"):
        target_stock_ids.update(_stock_ids_from_local_rows(_read_csv_rows(run_dir / filename)))

    for row in load_week_lot_ledger(trade_date):
        stock_id = str(row.get("stock_id", "")).strip()
        if stock_id:
            target_stock_ids.add(stock_id)

    for path in run_dir.glob("chase_*.json"):
        try:
            payload = _read_json(path)
        except (json.JSONDecodeError, OSError):
            continue
        stock_id = str(payload.get("stock_id", "")).strip()
        if stock_id:
            target_stock_ids.add(stock_id)

    if (run_dir / "allowed_live_order_2330_task.json").exists():
        target_stock_ids.add(ALLOWED_LIVE_ORDER_TARGET_STOCK_ID)

    return target_stock_ids


def _reconcile_selection_meta(
    trade_date: date,
    *,
    opening_positions: list[StrategyPosition],
) -> tuple[dict[str, dict[str, object]], dict[str, dict[str, object]]]:
    by_stock: dict[str, dict[str, object]] = {}
    by_lot: dict[str, dict[str, object]] = {}

    def add_meta(
        *,
        stock_id: object,
        strategy_lot_id: object = "",
        stock_name: object = "",
        source: object = "",
        basket_tag: object = "",
    ) -> None:
        resolved_stock_id = str(stock_id or "").strip()
        if not resolved_stock_id:
            return
        resolved_strategy_lot_id = str(strategy_lot_id or "").strip()
        meta = {
            "stock_name": str(stock_name or resolved_stock_id),
            "source": str(source or ""),
            "basket_tag": normalize_basket_tag(basket_tag or basket_tag_from_strategy_lot_id(resolved_strategy_lot_id)),
        }
        by_stock.setdefault(resolved_stock_id, meta)
        if resolved_strategy_lot_id:
            by_lot.setdefault(resolved_strategy_lot_id, meta)

    for position in opening_positions:
        add_meta(
            stock_id=position.stock_id,
            strategy_lot_id=position.strategy_lot_id,
            stock_name=position.stock_name,
            source=position.source,
            basket_tag=position.basket_tag,
        )

    for row in load_week_lot_ledger(trade_date):
        add_meta(
            stock_id=row.get("stock_id", ""),
            strategy_lot_id=row.get("strategy_lot_id", ""),
            stock_name=row.get("stock_name", ""),
            source=row.get("source", ""),
            basket_tag=row.get("basket_tag", ""),
        )

    run_dir = auto_trading_dir_for(trade_date)
    for filename in ("sizing.csv", "orders.csv", "sell_decisions.csv", "positions.csv"):
        for row in _read_csv_rows(run_dir / filename):
            add_meta(
                stock_id=row.get("stock_id", ""),
                strategy_lot_id=row.get("strategy_lot_id", ""),
                stock_name=row.get("stock_name", ""),
                source=row.get("source", ""),
                basket_tag=row.get("basket_tag", ""),
            )

    for path in run_dir.glob("chase_*.json"):
        try:
            payload = _read_json(path)
        except (json.JSONDecodeError, OSError):
            continue
        add_meta(
            stock_id=payload.get("stock_id", ""),
            stock_name=payload.get("stock_name", ""),
            source="guarded_live_order",
            basket_tag=DEFAULT_BASKET_TAG,
        )

    return by_stock, by_lot


@dataclass(slots=True)
class BrokerReconcileResult:
    trade_date: str
    target_stock_ids: list[str]
    fills_count: int
    positions_count: int
    excluded_positions_count: int
    ambiguous_fill_count: int
    pnl_snapshot: dict[str, object]


@dataclass(slots=True)
class PostGuardedOrderCheckResult:
    trade_date: str
    before_status: str
    after_status: str
    reconciled: bool
    fills_count: int
    positions_count: int
    sell_loop_readiness_recorded: bool
    reports_rendered: bool
    workflow_status_rendered: bool
    recommendation: str
    recommendation_note: str
    effective_recommendation: str
    effective_recommendation_note: str
    task_log_status: str = ""
    task_log_exit_code: str = ""
    task_log_message: str = ""
    task_log_path: str = ""
    schedule_task_name: str = ""
    schedule_status: str = ""
    schedule_state: str = ""
    schedule_next_run_time: str = ""
    schedule_last_run_time: str = ""
    schedule_last_task_result: str = ""
    schedule_description: str = ""
    schedule_message: str = ""
    next_run_guard_status: str = ""
    next_run_guard_message: str = ""
    next_run_guard_runner_script_path: str = ""
    next_run_guard_runner_sets_auto_trade_live: bool = False
    next_run_guard_allow_live_submit: bool = False
    next_run_guard_live_enabled: bool = False
    config_timing_status: str = ""
    config_timing_message: str = ""
    config_path: str = ""
    config_last_modified: str = ""
    task_recorded_at: str = ""
    config_fixed_after_task_recorded: bool = False
    config_fixed_after_scheduled_run: bool = False


@dataclass(slots=True)
class SellLoopReadinessResult:
    trade_date: str
    last_trade_day: str
    is_last_trade_day: bool
    positions_ready: bool
    positions_count: int
    positions_source_date: str
    post_guarded_status: str
    post_guarded_recommendation: str
    post_guarded_recommendation_note: str
    post_guarded_effective_recommendation: str
    post_guarded_effective_recommendation_note: str
    post_guarded_next_run_guard_status: str
    post_guarded_next_run_guard_message: str
    fills_count: int
    sell_decisions_count: int
    blocking_reason: str
    next_action: str
    next_action_note: str
    post_guarded_config_timing_status: str = ""
    post_guarded_config_timing_message: str = ""
    post_guarded_config_path: str = ""
    post_guarded_config_last_modified: str = ""
    post_guarded_task_recorded_at: str = ""


def _reconcile_broker_state(
    *,
    settings: Settings,
    trade_date: date,
    broker: ShioajiSinoPacBrokerAdapter,
    target_stock_ids: set[str],
) -> BrokerReconcileResult:
    run_dir = auto_trading_dir_for(trade_date)
    store = SQLiteStateStore(run_dir)
    store.initialize()

    broker_positions = broker.get_positions()
    target_stock_ids = set(target_stock_ids)
    if not target_stock_ids:
        raise RuntimeError("No target stock ids found for broker reconciliation. Pass --stock-id or create strategy artifacts first.")

    opening_positions, opening_source_date, _ = _load_strategy_positions_for_sell_loop(trade_date)
    ignored_same_day_positions = bool(opening_positions and opening_source_date == trade_date)
    opening_positions_for_reconcile = [] if ignored_same_day_positions else opening_positions
    opening_source_date_for_state = None if ignored_same_day_positions else opening_source_date
    selection_meta_by_stock, selection_meta_by_strategy_lot = _reconcile_selection_meta(
        trade_date,
        opening_positions=opening_positions,
    )
    for stock_id in target_stock_ids:
        selection_meta_by_stock.setdefault(
            stock_id,
            {
                "stock_name": stock_id,
                "source": "broker_reconcile",
                "basket_tag": DEFAULT_BASKET_TAG,
            },
        )

    quote_rows_by_stock: dict[str, QuoteState] = {}
    for stock_id in sorted(target_stock_ids):
        try:
            quote, _stock_name, _exchange_hint, _quote_timestamp = broker.get_quote_state(stock_id)
        except Exception:
            continue
        quote_rows_by_stock[stock_id] = quote

    fills_rows = _selected_fill_rows(
        broker=broker,
        trade_date=trade_date,
        target_stock_ids=target_stock_ids,
    )
    store.write_rows_csv("fills.csv", fills_rows)

    ambiguous_fill_rows = [
        row
        for row in fills_rows
        if str(row.get("fill_assignment_status", "")).strip() == "ambiguous_unmapped_fill"
    ]
    if ambiguous_fill_rows:
        store.append_event(
            run_id=_run_id(trade_date),
            timestamp=datetime.now(TAIPEI).isoformat(),
            level="WARNING",
            event_type="ambiguous_fill_mapping",
            message=f"Broker reconcile found {len(ambiguous_fill_rows)} fills without safe strategy lot mapping.",
            metadata={"count": len(ambiguous_fill_rows), "stocks": _ambiguous_fill_stock_ids(fills_rows)},
        )

    positions_rows = _positions_rows_from_fills(
        trade_date=trade_date,
        fills_rows=fills_rows,
        selection_meta_by_stock=selection_meta_by_stock,
        selection_meta_by_strategy_lot=selection_meta_by_strategy_lot,
        quote_rows_by_stock=quote_rows_by_stock,
        opening_positions=opening_positions_for_reconcile,
    )
    store.write_rows_csv("positions.csv", positions_rows)

    excluded_rows = _excluded_positions_rows(
        broker_positions=broker_positions,
        strategy_positions_rows=positions_rows,
    )
    store.write_rows_csv("excluded_positions.csv", excluded_rows)

    pnl_snapshot = _append_pnl_snapshot(
        store=store,
        trade_date=trade_date,
        positions_rows=positions_rows,
    )
    _update_run_state(
        store,
        trade_date,
        status="broker_reconciled",
        pnl_snapshot=pnl_snapshot,
        broker_reconcile={
            "target_stock_ids": sorted(target_stock_ids),
            "fills_count": len(fills_rows),
            "positions_count": len(positions_rows),
            "excluded_positions_count": len(excluded_rows),
            "ambiguous_fill_count": len(ambiguous_fill_rows),
            "opening_positions_source_date": opening_source_date_for_state.isoformat() if opening_source_date_for_state else "",
            "ignored_same_day_positions": ignored_same_day_positions,
        },
    )
    store.append_event(
        run_id=_run_id(trade_date),
        timestamp=datetime.now(TAIPEI).isoformat(),
        level="INFO",
        event_type="broker_reconcile",
        message=(
            "Reconciled broker fills/positions into local ledgers "
            f"for {len(target_stock_ids)} target stocks."
        ),
        metadata={
            "target_stock_ids": sorted(target_stock_ids),
            "fills_count": len(fills_rows),
            "positions_count": len(positions_rows),
            "excluded_positions_count": len(excluded_rows),
            "ambiguous_fill_count": len(ambiguous_fill_rows),
        },
    )

    return BrokerReconcileResult(
        trade_date=trade_date.isoformat(),
        target_stock_ids=sorted(target_stock_ids),
        fills_count=len(fills_rows),
        positions_count=len(positions_rows),
        excluded_positions_count=len(excluded_rows),
        ambiguous_fill_count=len(ambiguous_fill_rows),
        pnl_snapshot=pnl_snapshot,
    )


def _post_guarded_order_check(
    *,
    settings: Settings,
    trade_date: date,
    live: bool = False,
    reconcile: bool = False,
    sell_loop_readiness: bool = False,
    render_report: bool = False,
    workflow_status: bool = False,
    broker: ShioajiSinoPacBrokerAdapter | None = None,
) -> PostGuardedOrderCheckResult:
    if reconcile and not live:
        raise RuntimeError(
            "Post guarded order reconciliation is read-only, but --live is required to query the real broker account."
        )

    before_summary = _guarded_live_order_status_summary(trade_date)
    reconcile_result: BrokerReconcileResult | None = None
    if reconcile:
        resolved_broker = broker or ShioajiSinoPacBrokerAdapter(settings, simulation=False)
        summary = resolved_broker.get_account_summary()
        if not summary.signed:
            raise RuntimeError("Post guarded order reconciliation blocked because broker account is not signed.")
        target_stock_ids = _reconcile_target_stock_ids(
            trade_date,
            [ALLOWED_LIVE_ORDER_TARGET_STOCK_ID],
        )
        reconcile_result = _reconcile_broker_state(
            settings=settings,
            trade_date=trade_date,
            broker=resolved_broker,
            target_stock_ids=target_stock_ids,
        )

    after_summary = _guarded_live_order_status_summary(trade_date)
    evidence_summary = after_summary or before_summary
    next_run_guard = _allowed_live_next_run_guard_summary(
        settings,
        scheduled_task_evidence={
            "status": str(evidence_summary.get("schedule_status", "")),
            "task_name": str(evidence_summary.get("schedule_task_name", "")),
            "state": str(evidence_summary.get("schedule_state", "")),
            "next_run_time": str(evidence_summary.get("schedule_next_run_time", "")),
            "last_run_time": str(evidence_summary.get("schedule_last_run_time", "")),
            "last_task_result": str(evidence_summary.get("schedule_last_task_result", "")),
            "description": str(evidence_summary.get("schedule_description", "")),
            "message": str(evidence_summary.get("schedule_message", "")),
        },
    )
    config_timing = _guarded_config_timing_summary(
        settings=settings,
        trade_date=trade_date,
        guarded_status=str(after_summary.get("status", "")),
        schedule_next_run_time=str(evidence_summary.get("schedule_next_run_time", "")),
    )
    effective_recommendation = _effective_guarded_post_recommendation(
        {
            "after_status": str(after_summary.get("status", "")),
            "recommendation": str(after_summary.get("recommendation", "")),
            "next_run_guard_status": str(next_run_guard.get("status", "")),
        }
    )
    result = PostGuardedOrderCheckResult(
        trade_date=trade_date.isoformat(),
        before_status=str(before_summary.get("status", "")),
        after_status=str(after_summary.get("status", "")),
        reconciled=reconcile_result is not None,
        fills_count=int(after_summary.get("fills_count", 0) or 0),
        positions_count=int(after_summary.get("positions_count", 0) or 0),
        sell_loop_readiness_recorded=sell_loop_readiness,
        reports_rendered=render_report,
        workflow_status_rendered=workflow_status,
        recommendation=str(after_summary.get("recommendation", "")),
        recommendation_note=_describe_workflow_action(after_summary.get("recommendation", "")),
        effective_recommendation=effective_recommendation,
        effective_recommendation_note=_describe_workflow_action(effective_recommendation),
        task_log_status=str(evidence_summary.get("task_log_status", "")),
        task_log_exit_code=str(evidence_summary.get("task_log_exit_code", "")),
        task_log_message=_normalize_guarded_task_log_message(evidence_summary.get("task_log_message", "")),
        task_log_path=str(evidence_summary.get("task_log_path", "")),
        schedule_task_name=str(evidence_summary.get("schedule_task_name", "")),
        schedule_status=str(evidence_summary.get("schedule_status", "")),
        schedule_state=str(evidence_summary.get("schedule_state", "")),
        schedule_next_run_time=str(evidence_summary.get("schedule_next_run_time", "")),
        schedule_last_run_time=str(evidence_summary.get("schedule_last_run_time", "")),
        schedule_last_task_result=str(evidence_summary.get("schedule_last_task_result", "")),
        schedule_description=str(evidence_summary.get("schedule_description", "")),
        schedule_message=str(evidence_summary.get("schedule_message", "")),
        next_run_guard_status=str(next_run_guard.get("status", "")),
        next_run_guard_message=str(next_run_guard.get("message", "")),
        next_run_guard_runner_script_path=str(next_run_guard.get("runner_script_path", "")),
        next_run_guard_runner_sets_auto_trade_live=bool(next_run_guard.get("runner_sets_auto_trade_live", False)),
        next_run_guard_allow_live_submit=bool(next_run_guard.get("allow_live_submit", False)),
        next_run_guard_live_enabled=bool(next_run_guard.get("live_enabled", False)),
        config_timing_status=str(config_timing.get("status", "")),
        config_timing_message=_workflow_note_display_text(config_timing.get("message", "")),
        config_path=str(config_timing.get("config_path", "")),
        config_last_modified=str(config_timing.get("config_last_modified", "")),
        task_recorded_at=str(config_timing.get("task_recorded_at", "")),
        config_fixed_after_task_recorded=bool(config_timing.get("config_fixed_after_task_recorded", False)),
        config_fixed_after_scheduled_run=bool(config_timing.get("config_fixed_after_scheduled_run", False)),
    )

    run_dir = auto_trading_dir_for(trade_date)
    store = SQLiteStateStore(run_dir)
    store.initialize()
    (run_dir / "post_guarded_order_check.json").write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _update_run_state(
        store,
        trade_date,
        status="post_guarded_order_checked",
        post_guarded_order_check=asdict(result),
    )
    store.append_event(
        run_id=_run_id(trade_date),
        timestamp=datetime.now(TAIPEI).isoformat(),
        level="INFO",
        event_type="post_guarded_order_check",
        message=(
            f"已檢查受保護下單產物：after={result.after_status}，"
            f"current_step={result.effective_recommendation}"
            + ("；並已做 broker state reconciliation。" if reconcile_result is not None else "。")
        ),
        metadata=asdict(result),
    )
    if sell_loop_readiness:
        _write_sell_loop_readiness(trade_date)
    if render_report:
        command_render_report(argparse.Namespace(trade_date=trade_date.isoformat()))
    if workflow_status:
        command_workflow_status(argparse.Namespace(trade_date=trade_date.isoformat()))
    if reconcile_result is not None or sell_loop_readiness or render_report or workflow_status:
        _best_effort_obsidian_sync(
            settings,
            trade_date,
            include_live_status=live,
            event_summary=(
                "post_guarded_order_check completed: "
                f"before={result.before_status} after={result.after_status} "
                f"fills={result.fills_count} positions={result.positions_count}"
            ),
        )
    return result


def _sell_loop_readiness_summary(trade_date: date) -> SellLoopReadinessResult:
    plan = resolve_week_trade_plan(trade_date)
    positions, source_date, _ = _load_strategy_positions_for_sell_loop(trade_date)
    run_dir = auto_trading_dir_for(trade_date)
    post_guarded_check = _post_guarded_order_check_report_summary(trade_date)
    post_guarded_status = str(post_guarded_check.get("after_status", "")).strip()
    post_guarded_recommendation = str(post_guarded_check.get("recommendation", "")).strip()
    post_guarded_effective_recommendation = _effective_guarded_post_recommendation(post_guarded_check)
    post_guarded_next_run_guard_status = str(post_guarded_check.get("next_run_guard_status", "")).strip()
    post_guarded_next_run_guard_message = str(post_guarded_check.get("next_run_guard_message", "")).strip()
    post_guarded_config_timing_status = str(post_guarded_check.get("config_timing_status", "")).strip()
    post_guarded_config_timing_message = str(post_guarded_check.get("config_timing_message", "")).strip()
    post_guarded_config_path = str(post_guarded_check.get("config_path", "")).strip()
    post_guarded_config_last_modified = str(post_guarded_check.get("config_last_modified", "")).strip()
    post_guarded_task_recorded_at = str(post_guarded_check.get("task_recorded_at", "")).strip()
    fills_count = len(_read_csv_rows(run_dir / "fills.csv"))
    sell_decisions_count = len(_read_csv_rows(run_dir / "sell_decisions.csv"))
    is_last_trade_day = plan.last_trade_day == trade_date

    if not is_last_trade_day and positions:
        blocking_reason = "ready_to_prepare"
        next_action = "run_sell_loop_prepare_only_after_market_open"
    elif not is_last_trade_day:
        blocking_reason = "not_last_trade_day"
        next_action = f"wait_until_last_trade_day:{plan.last_trade_day.isoformat()}"
    elif not positions:
        blocking_reason = "no_strategy_positions"
        if post_guarded_status.startswith("skipped_"):
            if post_guarded_next_run_guard_status == "live_guard_ready":
                next_action = "today_guarded_run_missed_wait_for_next_guarded_schedule_no_backfill"
            else:
                next_action = (
                    post_guarded_effective_recommendation
                    or "fix_guarded_live_submit_guard_before_next_scheduled_run"
                )
        elif post_guarded_status in {
            "submitted_no_fills_yet",
            "schedule_query_failed",
            "task_failed",
            "scheduled_time_passed_without_artifacts",
        }:
            next_action = "resolve_guarded_order_status_then_reconcile_positions"
        else:
            next_action = "run_read_only_broker_reconcile_after_fills_exist"
    elif post_guarded_status == "task_failed":
        blocking_reason = "guarded_task_failed"
        next_action = "inspect_guarded_task_log_before_sell_loop"
    elif post_guarded_status == "submitted_no_fills_yet" and fills_count == 0:
        blocking_reason = "broker_reconcile_recommended"
        next_action = "run_post_guarded_order_check_with_live_reconcile_after_market_updates"
    else:
        blocking_reason = "ready_to_evaluate"
        next_action = "run_sell_loop_dry_run_or_live_with_guardrails_on_last_trade_day"

    return SellLoopReadinessResult(
        trade_date=trade_date.isoformat(),
        last_trade_day=plan.last_trade_day.isoformat(),
        is_last_trade_day=is_last_trade_day,
        positions_ready=bool(positions),
        positions_count=len(positions),
        positions_source_date=source_date.isoformat() if source_date else "",
        post_guarded_status=post_guarded_status,
        post_guarded_recommendation=post_guarded_recommendation,
        post_guarded_recommendation_note=_describe_workflow_action(post_guarded_recommendation),
        post_guarded_effective_recommendation=post_guarded_effective_recommendation,
        post_guarded_effective_recommendation_note=_describe_workflow_action(post_guarded_effective_recommendation),
        post_guarded_next_run_guard_status=post_guarded_next_run_guard_status,
        post_guarded_next_run_guard_message=post_guarded_next_run_guard_message,
        fills_count=fills_count,
        sell_decisions_count=sell_decisions_count,
        blocking_reason=blocking_reason,
        next_action=next_action,
        next_action_note=_describe_workflow_action(next_action),
        post_guarded_config_timing_status=post_guarded_config_timing_status,
        post_guarded_config_timing_message=post_guarded_config_timing_message,
        post_guarded_config_path=post_guarded_config_path,
        post_guarded_config_last_modified=post_guarded_config_last_modified,
        post_guarded_task_recorded_at=post_guarded_task_recorded_at,
    )


def _write_sell_loop_readiness(trade_date: date) -> SellLoopReadinessResult:
    result = _sell_loop_readiness_summary(trade_date)
    run_dir = auto_trading_dir_for(trade_date)
    store = SQLiteStateStore(run_dir)
    store.initialize()
    (run_dir / "sell_loop_readiness.json").write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _update_run_state(
        store,
        trade_date,
        status="sell_loop_readiness_checked",
        sell_loop_readiness=asdict(result),
    )
    store.append_event(
        run_id=_run_id(trade_date),
        timestamp=datetime.now(TAIPEI).isoformat(),
        level="INFO",
        event_type="sell_loop_readiness",
        message=(
            f"已檢查賣出就緒狀態：blocking={result.blocking_reason}，"
            f"next_action={result.next_action}。"
        ),
        metadata=asdict(result),
    )
    return result


def _append_pnl_snapshot(
    *,
    store: SQLiteStateStore,
    trade_date: date,
    positions_rows: list[dict[str, object]],
) -> dict[str, object]:
    sell_rows = _read_csv_rows(store.run_dir / "sell_decisions.csv")
    realized_pnl = sum(_as_float(row.get("realized_pnl"), 0.0) for row in sell_rows)
    realized_cost_basis = sum(_as_float(row.get("allocated_buy_cost"), 0.0) for row in sell_rows)
    snapshot = build_pnl_snapshot(
        run_id=_run_id(trade_date),
        trade_date=trade_date,
        positions_rows=positions_rows,
        realized_pnl=realized_pnl,
        realized_cost_basis=realized_cost_basis,
        snapshot_time=datetime.now(TAIPEI),
    )
    existing_rows = _read_csv_rows(store.run_dir / "pnl_snapshots.csv")
    existing_rows.append(snapshot)
    store.write_rows_csv("pnl_snapshots.csv", existing_rows)
    return snapshot


def _build_excluded_positions_rows(*, trade_date: date) -> list[dict[str, object]]:
    run_dir = auto_trading_dir_for(trade_date)
    rows: list[dict[str, object]] = []
    for row in _read_csv_rows(run_dir / "excluded_positions.csv"):
        stock_id = row.get("stock_id", "")
        excluded_qty = _as_int(row.get("excluded_qty"), 0)
        strategy_qty = _as_int(row.get("strategy_qty"), 0)
        broker_qty = _as_int(row.get("broker_qty"), 0)
        reason = row.get("reason", "")
        item = (
            f"{stock_id} {row.get('stock_name', '')}：broker {broker_qty} 股，"
            f"策略範圍 {strategy_qty} 股，排除 {excluded_qty} 股；原因：{reason}"
        )
        rows.append(
            {
                "stock_id": stock_id,
                "stock_name": row.get("stock_name", ""),
                "broker_qty": broker_qty,
                "strategy_qty": strategy_qty,
                "excluded_qty": excluded_qty,
                "reason": reason,
                "item": item,
            }
        )
    return rows


def _week_csv_rows(plan, filename: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for day in plan.week_trade_days or []:
        rows.extend(_read_csv_rows(auto_trading_dir_for(day) / filename))
    return rows


def _load_strategy_positions_for_sell_loop(
    trade_date: date,
) -> tuple[list[StrategyPosition], date | None, list[dict[str, str]]]:
    plan = resolve_week_trade_plan(trade_date)
    candidate_dates = [item for item in plan.week_trade_days if item <= trade_date]
    for source_date in reversed(candidate_dates):
        rows = _read_csv_rows(auto_trading_dir_for(source_date) / "positions.csv")
        positions: list[StrategyPosition] = []
        for row in rows:
            holding_qty = _as_int(row.get("holding_qty"), _as_int(row.get("quantity"), 0))
            if holding_qty <= 0:
                continue
            stock_id = row.get("stock_id", "")
            positions.append(
                StrategyPosition(
                    strategy_lot_id=row.get("strategy_lot_id", _strategy_lot_id(source_date, stock_id, row.get("basket_tag"))),
                    stock_id=stock_id,
                    stock_name=row.get("stock_name", stock_id),
                    holding_qty=holding_qty,
                    buy_avg_price=_as_float(row.get("buy_avg_price")),
                    buy_total_cost=_as_float(row.get("buy_total_cost"), _as_float(row.get("buy_avg_price")) * holding_qty),
                    source=row.get("source", "unknown"),
                    basket_tag=normalize_basket_tag(row.get("basket_tag") or basket_tag_from_strategy_lot_id(row.get("strategy_lot_id", ""))),
                )
            )
        if positions:
            return positions, source_date, rows
    return [], None, []


def _load_latest_quote_rows_for_stock_ids(
    trade_date: date,
    stock_ids: set[str],
) -> dict[str, dict[str, str]]:
    plan = resolve_week_trade_plan(trade_date)
    candidate_dates = [item for item in plan.week_trade_days if item <= trade_date]
    resolved: dict[str, dict[str, str]] = {}
    for source_date in reversed(candidate_dates):
        rows = _read_csv_rows(auto_trading_dir_for(source_date) / "quote_snapshots.csv")
        for row in rows:
            stock_id = row.get("stock_id", "")
            if not stock_id or stock_id not in stock_ids or stock_id in resolved:
                continue
            resolved[stock_id] = row
        if len(resolved) == len(stock_ids):
            break
    return resolved


def command_prepare_llm_selection(args: argparse.Namespace) -> int:
    settings = Settings.load()
    trade_date = _parse_trade_date(args.trade_date)
    provider = _provider_from_settings(settings)
    preselect = provider.load_preselect(trade_date)
    manual_final_list = provider.load_final_list(trade_date)

    written = write_llm_review_bundle(
        trade_date=trade_date,
        provider_name=provider.provider_name(),
        preselect_items=preselect,
        manual_final_list=manual_final_list,
    )

    run_dir = auto_trading_dir_for(trade_date)
    store = SQLiteStateStore(run_dir)
    store.initialize()
    _update_run_state(
        store,
        trade_date,
        status="llm_review_prepared",
        llm_review_bundle={
            "provider_name": provider.provider_name(),
            "candidate_count": len(preselect),
            "payload_path": str(written["payload_path"]),
            "template_path": str(written["template_path"]),
        },
    )
    store.append_event(
        run_id=_run_id(trade_date),
        timestamp=datetime.now(TAIPEI).isoformat(),
        level="INFO",
        event_type="prepare_llm_selection",
        message=f"Prepared LLM review bundle with {len(preselect)} candidates from {provider.provider_name()}.",
        metadata={
            "provider_name": provider.provider_name(),
            "payload_path": str(written["payload_path"]),
            "template_path": str(written["template_path"]),
        },
    )

    print(f"provider: {provider.provider_name()}")
    print(f"candidate_count: {len(preselect)}")
    print(f"payload_json: {written['payload_path']}")
    print(f"decisions_template: {written['template_path']}")
    seeded_decisions_path = written.get("decisions_path")
    if seeded_decisions_path:
        print(f"decisions_json_seeded: {seeded_decisions_path}")
    print(f"brief_markdown: {written['brief_path']}")
    return 0


def command_apply_llm_selection(args: argparse.Namespace) -> int:
    settings = Settings.load()
    trade_date = _parse_trade_date(args.trade_date)
    decisions_path = llm_selection_decisions_path(trade_date)
    items = load_llm_decision_items(decisions_path)
    final_list_path = input_dir_for(trade_date) / "auto_trade_final_list.csv"
    write_final_list_csv(final_list_path, items)

    run_dir = auto_trading_dir_for(trade_date)
    store = SQLiteStateStore(run_dir)
    store.initialize()
    _update_run_state(
        store,
        trade_date,
        status="llm_selection_applied",
        llm_selection={
            "selected_count": len(items),
            "decisions_path": str(decisions_path),
            "final_list_path": str(final_list_path),
        },
    )
    store.append_event(
        run_id=_run_id(trade_date),
        timestamp=datetime.now(TAIPEI).isoformat(),
        level="INFO",
        event_type="apply_llm_selection",
        message=f"Applied LLM selection decisions into final_list with {len(items)} selected names.",
        metadata={
            "decisions_path": str(decisions_path),
            "final_list_path": str(final_list_path),
        },
    )

    print(f"decisions_json: {decisions_path}")
    print(f"selected_count: {len(items)}")
    print(f"final_list_csv: {final_list_path}")
    return 0


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_event_message_for_display(event_type: object, message: object) -> str:
    event_name = str(event_type or "").strip()
    text = str(message or "").strip()
    if not text:
        return ""
    if event_name == "allowed_live_order_task":
        prefix = "Skipped allowed live order task: "
        config_disabled = "Live submit is blocked because auto_trading.live_enabled is false in config."
        if text == f"{prefix}{config_disabled}":
            return "已略過受保護下單任務：因為設定中的 auto_trading.live_enabled=false，真實下單被擋下。"
        if text.startswith(prefix):
            rest = text[len(prefix) :].strip()
            rest = rest.replace("Live submit", "真實下單")
            if rest.endswith("."):
                rest = rest[:-1] + "。"
            return f"已略過受保護下單任務：{rest}"
        executed_prefix = "Allowed live order task executed: "
        if text.startswith(executed_prefix):
            rest = text[len(executed_prefix) :].strip()
            if rest.endswith("."):
                rest = rest[:-1] + "。"
            return f"已執行受保護下單任務：{rest}"
        failed_prefix = "Allowed live order task failed: "
        if text.startswith(failed_prefix):
            rest = text[len(failed_prefix) :].strip()
            if rest.endswith("."):
                rest = rest[:-1] + "。"
            return f"受保護下單任務執行失敗：{rest}"
    if event_name == "prepare_week":
        prefix = "Loaded "
        middle = " preselect items from "
        if text.startswith(prefix) and middle in text:
            count, provider = text[len(prefix) :].split(middle, 1)
            return f"已載入 {count.strip()} 筆預選名單，來源提供者={provider.strip()}。"
    if event_name == "finalize":
        prefix = "Finalized "
        suffix = " symbols."
        if text.startswith(prefix) and text.endswith(suffix):
            count = text[len(prefix) : -len(suffix)].strip()
            return f"已完成 finalize，產出 {count} 檔訂版股票。"
    if event_name == "workflow_status":
        prefix = "Rendered workflow status with "
        suffix = " checklist rows."
        if text.startswith(prefix) and text.endswith(suffix):
            count = text[len(prefix) : -len(suffix)].strip()
            return f"已輸出工作流狀態，包含 {count} 筆清單列。"
    if event_name == "refresh_dashboard":
        prefix = "Dashboard refresh completed with "
        suffix = " steps."
        if text.startswith(prefix) and text.endswith(suffix):
            count = text[len(prefix) : -len(suffix)].strip()
            return f"已完成儀表板刷新，共 {count} 個步驟。"
    if event_name == "post_guarded_order_check":
        if text == "Checked guarded live order artifacts.":
            return "已檢查受保護下單產物。"
        prefix = "Checked guarded live order artifacts: after="
        if text.startswith(prefix):
            rest = text[len(prefix) :].strip()
            rest = rest.replace("; reconciled broker state.", "；並已做 broker state reconciliation。")
            if rest.endswith("."):
                rest = rest[:-1] + "。"
            return f"已檢查受保護下單產物：after={rest}"
    if event_name == "sell_loop_readiness":
        simple_prefix = "Checked sell-loop readiness: "
        if text.startswith(simple_prefix) and text.endswith(".") and "blocking=" not in text:
            rest = text[len(simple_prefix) : -1].strip()
            return f"已檢查賣出就緒狀態：{rest}。"
        prefix = "Checked sell-loop readiness: blocking="
        if text.startswith(prefix):
            rest = text[len(prefix) :].strip()
            if rest.endswith("."):
                rest = rest[:-1] + "。"
            return f"已檢查賣出就緒狀態：blocking={rest}"
    if event_name == "sell_loop" and text == "No strategy positions were available for sell-loop evaluation.":
        return "目前沒有可用的策略部位可進行賣出迴圈評估。"
    return text


def _read_event_rows(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        metadata = raw.get("metadata", {}) if isinstance(raw.get("metadata", {}), dict) else {}
        display_message = _normalize_event_message_for_display(raw.get("event_type", ""), raw.get("message", ""))
        rows.append(
            {
                "time": raw.get("timestamp", ""),
                "event_type": raw.get("event_type", ""),
                "stock_id": raw.get("stock_id", ""),
                "action": metadata.get("action", raw.get("event_type", "")),
                "price": metadata.get("price", ""),
                "qty": metadata.get("qty", metadata.get("quantity", "")),
                "result": display_message,
                "warning_or_error": "" if raw.get("level") == "INFO" else display_message,
            }
        )
    return rows


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


def _load_selection_snapshot_rows(
    *,
    trade_date: date,
    provider_name: str,
) -> list[dict[str, object]]:
    run_dir = auto_trading_dir_for(trade_date)
    input_dir = input_dir_for(trade_date)
    preselect_rows = _read_csv_rows(run_dir / "preselect.csv")
    final_rows = _read_csv_rows(input_dir / "auto_trade_final_list.csv")
    sizing_rows = _read_csv_rows(run_dir / "sizing.csv")

    merged: dict[str, dict[str, object]] = {}
    for row in preselect_rows:
        stock_id = row.get("stock_id", "")
        if not stock_id:
            continue
        basket_tag = normalize_basket_tag(row.get("basket_tag"))
        merged[_selection_snapshot_key(stock_id, basket_tag)] = {
            "stock_id": stock_id,
            "stock_name": row.get("stock_name", ""),
            "source": row.get("source", "unknown"),
            "basket_tag": basket_tag,
            "source_weight": _as_float(row.get("source_weight"), 1.0),
            "preselect_flag": True,
            "final_flag": False,
            "role_level": row.get("role_level", ""),
            "theme": row.get("theme", ""),
            "model_score": row.get("model_score", ""),
            "finalizer_score": "",
            "include_reason": "",
            "exclude_reason": "",
            "provider_name": row.get("provider_name", provider_name),
        }

    for row in final_rows:
        stock_id = row.get("stock_id", "")
        if not stock_id:
            continue
        basket_tag = normalize_basket_tag(row.get("basket_tag"))
        target = merged.setdefault(
            _selection_snapshot_key(stock_id, basket_tag),
            {
                "stock_id": stock_id,
                "stock_name": row.get("stock_name", ""),
                "source": row.get("source", "unknown"),
                "basket_tag": basket_tag,
                "source_weight": _as_float(row.get("source_weight"), 1.0),
                "preselect_flag": False,
                "final_flag": False,
                "role_level": row.get("role_level", ""),
                "theme": row.get("theme", ""),
                "model_score": row.get("model_score", ""),
                "finalizer_score": "",
                "include_reason": "",
                "exclude_reason": "",
                "provider_name": provider_name,
            },
        )
        target["stock_name"] = row.get("stock_name", target["stock_name"])
        target["source"] = row.get("source", target["source"])
        target["basket_tag"] = basket_tag
        target["source_weight"] = _as_float(row.get("source_weight"), _as_float(target.get("source_weight"), 1.0))
        target["final_flag"] = True
        include_reason = str(row.get("include_reason", "")).strip()
        target["include_reason"] = include_reason or _provider_final_list_origin(provider_name)
        target["provider_name"] = provider_name

    for row in sizing_rows:
        stock_id = row.get("stock_id", "")
        basket_tag = normalize_basket_tag(row.get("basket_tag"))
        key = _selection_snapshot_key(stock_id, basket_tag)
        if not stock_id or key not in merged:
            continue
        merged[key]["target_qty"] = _as_int(row.get("target_qty"))
        merged[key]["estimated_buy_price"] = _as_float(row.get("estimated_buy_price"))
        merged[key]["projected_cost"] = _as_float(row.get("projected_cost"))

    return sorted(
        merged.values(),
        key=lambda item: (not bool(item.get("final_flag")), str(item.get("stock_id", "")), str(item.get("basket_tag", ""))),
    )


def _selection_snapshot_counts(selection_rows: list[dict[str, object]]) -> tuple[int, int]:
    preselect_count = sum(1 for row in selection_rows if bool(row.get("preselect_flag")))
    final_list_count = sum(1 for row in selection_rows if bool(row.get("final_flag")))
    return preselect_count, final_list_count


def _build_buy_execution_rows(
    *,
    trade_date: date,
    selection_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    run_dir = auto_trading_dir_for(trade_date)
    order_rows = _read_csv_rows(run_dir / "orders.csv")
    quote_rows = {row.get("stock_id", ""): row for row in _read_csv_rows(run_dir / "quote_snapshots.csv")}
    latest_orders: dict[str, dict[str, str]] = {}
    for row in order_rows:
        strategy_lot_id = str(row.get("strategy_lot_id", "")).strip()
        stock_id = row.get("stock_id", "")
        basket_tag = normalize_basket_tag(row.get("basket_tag"))
        resolved_lot_id = strategy_lot_id or _strategy_lot_id(trade_date, stock_id, basket_tag)
        if stock_id:
            latest_orders[resolved_lot_id] = row

    next_check = (datetime.now(TAIPEI).replace(second=0, microsecond=0)).isoformat(timespec="minutes")
    rows: list[dict[str, object]] = []
    for item in selection_rows:
        if not item.get("final_flag"):
            continue
        stock_id = str(item.get("stock_id", ""))
        basket_tag = normalize_basket_tag(item.get("basket_tag"))
        strategy_lot_id = _strategy_lot_id(trade_date, stock_id, basket_tag)
        target_qty = _as_int(item.get("target_qty"), 0)
        order = latest_orders.get(strategy_lot_id, {})
        quote = quote_rows.get(stock_id, {})
        last_price = _as_float(quote.get("last_price"), _as_float(order.get("target_price"), _as_float(item.get("estimated_buy_price"), 0.0)))
        order_price = _as_float(order.get("target_price"), last_price)
        bought_qty = _as_int(order.get("filled_qty"), 0)
        remaining_qty = _as_int(order.get("remaining_qty"), max(target_qty - bought_qty, 0))
        tick_gap = ""
        if last_price > 0 and order_price > 0:
            tick_gap = tick_distance(last_price, order_price)
        rows.append(
            {
                "strategy_lot_id": strategy_lot_id,
                "stock_id": stock_id,
                "stock_name": item.get("stock_name", ""),
                "basket_tag": basket_tag,
                "target_qty": target_qty,
                "bought_qty": bought_qty,
                "remaining_qty": remaining_qty,
                "active_order_id": order.get("order_id", ""),
                "active_order_price": order_price or "",
                "active_order_qty": _as_int(order.get("active_order_qty"), _as_int(order.get("target_qty"), target_qty)),
                "order_age": order.get("order_age", ""),
                "current_mode": order.get("current_mode", current_buy_mode(datetime.now(TAIPEI)).value),
                "last_price": last_price or "",
                "bid1": quote.get("bid1", ""),
                "ask1": quote.get("ask1", ""),
                "quote_timestamp": quote.get("timestamp", order.get("quote_timestamp", "")),
                "buy_submission_gate": order.get("buy_submission_gate", ""),
                "tick_distance_to_target": tick_gap,
                "next_check_time": next_check,
                "order_status_summary": order.get("status", "not_submitted"),
            }
        )
    return rows


def _report_mode(state_data: dict[str, object]) -> str:
    buy_loop = state_data.get("buy_loop", {})
    if isinstance(buy_loop, dict):
        mode = str(buy_loop.get("mode", "")).strip()
        if mode:
            return mode
    post_guarded_order_check = state_data.get("post_guarded_order_check", {})
    if isinstance(post_guarded_order_check, dict):
        guarded_status = str(post_guarded_order_check.get("after_status", "")).strip() or str(
            post_guarded_order_check.get("before_status", "")
        ).strip()
        if guarded_status:
            return "live_guarded"
    guarded_status = str(state_data.get("guarded_post_check_status", "")).strip() or str(
        state_data.get("guarded_post_check_recommendation", "")
    ).strip()
    if guarded_status:
        return "live_guarded"
    workflow_type = str(state_data.get("workflow_type", "")).strip()
    if workflow_type:
        return workflow_type
    return "dry_run"


def _resolve_basket_summary(
    *,
    state_data: dict[str, object],
    sell_rows: list[dict[str, object]],
    settings: Settings,
    current_equity: float,
    unrealized: float,
    strategy_return: float,
) -> dict[str, object]:
    sell_loop = state_data.get("sell_loop", {})
    basket_reco = ""
    basket_threshold = settings.auto_trading.basket_profit_buffer_min_twd
    loser_loss_ratio = 0.0
    basket_scope = "combined"
    basket_tags = ""
    if isinstance(sell_loop, dict):
        basket_summaries = sell_loop.get("basket_summaries", {})
        if isinstance(basket_summaries, dict) and basket_summaries:
            resolved = [value for value in basket_summaries.values() if isinstance(value, dict)]
            if resolved:
                recommendations = {str(item.get("recommendation", "")).strip() for item in resolved if str(item.get("recommendation", "")).strip()}
                basket_reco = next(iter(recommendations)) if len(recommendations) == 1 else "mixed"
                basket_threshold = sum(_as_float(item.get("threshold"), 0.0) for item in resolved)
                loser_loss_ratio = max((_as_float(item.get("loser_loss_ratio"), 0.0) for item in resolved), default=0.0)
                basket_scope = "multi_basket" if len(resolved) > 1 else "single_basket"
                basket_tags = ",".join(sorted(str(tag).strip() for tag in basket_summaries if str(tag).strip()))
        if not basket_reco:
            basket_reco = str(sell_loop.get("basket_recommendation", "")).strip()
            basket_threshold = _as_float(
                sell_loop.get("basket_threshold"),
                settings.auto_trading.basket_profit_buffer_min_twd,
            )
            loser_loss_ratio = _as_float(sell_loop.get("loser_loss_ratio"), 0.0)

    if not basket_reco:
        row_basket_tags = sorted(
            {
                normalize_basket_tag(row.get("basket_tag"))
                for row in sell_rows
                if str(row.get("basket_tag", "")).strip()
            }
        )
        row_recommendations = [
            str(row.get("basket_recommendation", "")).strip()
            for row in sell_rows
            if str(row.get("basket_recommendation", "")).strip()
        ]
        if row_recommendations:
            basket_reco = row_recommendations[-1] if len(set(row_recommendations)) == 1 else "mixed"
        else:
            basket_reco = "recommend_exit" if any(row.get("sell_decision") == "sell" for row in sell_rows) else "hold"
        row_threshold = next(
            (
                row.get("basket_threshold")
                for row in sell_rows
                if row.get("basket_threshold", "") not in ("", None)
            ),
            None,
        )
        if row_threshold is not None:
            basket_threshold = _as_float(row_threshold, basket_threshold)
        row_loser_ratio = next(
            (
                row.get("basket_loser_loss_ratio")
                for row in sell_rows
                if row.get("basket_loser_loss_ratio", "") not in ("", None)
            ),
            None,
        )
        if row_loser_ratio is not None:
            loser_loss_ratio = _as_float(row_loser_ratio, 0.0)
        if row_basket_tags:
            basket_scope = "multi_basket" if len(row_basket_tags) > 1 else "single_basket"
            basket_tags = ",".join(row_basket_tags)

    return {
        "basket_market_value": current_equity,
        "basket_unrealized_pnl": unrealized,
        "basket_unrealized_pnl_pct": strategy_return,
        "basket_conservative_profit": sum(_as_float(row.get("conservative_profit"), 0.0) for row in sell_rows),
        "basket_threshold": basket_threshold,
        "basket_recommendation": basket_reco,
        "loser_loss_ratio": loser_loss_ratio,
        "basket_scope": basket_scope,
        "basket_tags": basket_tags,
    }


def _build_positions_rows(
    *,
    trade_date: date,
    fees,
    auto,
    buy_execution_rows: list[dict[str, object]],
    sell_rows: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    run_dir = auto_trading_dir_for(trade_date)
    csv_rows = _read_csv_rows(run_dir / "positions.csv")
    rows: list[dict[str, object]] = []
    if not csv_rows:
        for item in buy_execution_rows:
            if _as_int(item.get("bought_qty"), 0) <= 0:
                continue
            current_price = _as_float(item.get("last_price"), _as_float(item.get("active_order_price"), 0.0))
            buy_avg_price = _as_float(item.get("active_order_price"), current_price)
            buy_total_cost = buy_avg_price * _as_int(item.get("bought_qty"), 0)
            estimated_exit = current_price * _as_int(item.get("bought_qty"), 0) - fees.estimate_sell_fee(current_price) - fees.estimate_sell_tax(current_price)
            rows.append(
                {
                    "strategy_lot_id": item.get("strategy_lot_id", ""),
                    "stock_id": item.get("stock_id", ""),
                    "stock_name": item.get("stock_name", ""),
                    "basket_tag": item.get("basket_tag", DEFAULT_BASKET_TAG),
                    "holding_qty": item.get("bought_qty", 0),
                    "buy_avg_price": buy_avg_price,
                    "buy_total_cost": buy_total_cost,
                    "current_price": current_price,
                    "market_value": current_price * _as_int(item.get("bought_qty"), 0),
                    "unrealized_pnl": 0.0,
                    "unrealized_pnl_pct": 0.0,
                    "estimated_exit_value_after_fee_tax": estimated_exit,
                    "breakeven_sell_price": buy_avg_price,
                }
            )
        opening_positions, _, _ = _load_strategy_positions_for_sell_loop(trade_date)
        if opening_positions:
            existing_lot_ids = {
                str(row.get("strategy_lot_id", "")).strip()
                for row in rows
                if str(row.get("strategy_lot_id", "")).strip()
            }
            latest_quote_rows = _load_latest_quote_rows_for_stock_ids(
                trade_date,
                {
                    str(position.stock_id).strip()
                    for position in opening_positions
                    if str(position.stock_id).strip()
                },
            )
            for row in _positions_rows_from_strategy_positions(
                trade_date=trade_date,
                positions=opening_positions,
                quote_rows_by_stock=latest_quote_rows,
            ):
                strategy_lot_id = str(row.get("strategy_lot_id", "")).strip()
                if strategy_lot_id and strategy_lot_id in existing_lot_ids:
                    continue
                rows.append(row)
        if rows and sell_rows:
            adjustment_positions = [
                StrategyPosition(
                    strategy_lot_id=str(row.get("strategy_lot_id", "")).strip(),
                    stock_id=str(row.get("stock_id", "")).strip(),
                    stock_name=str(row.get("stock_name", "")).strip(),
                    holding_qty=_as_int(row.get("holding_qty"), 0),
                    buy_avg_price=_as_float(row.get("buy_avg_price"), 0.0),
                    buy_total_cost=_as_float(row.get("buy_total_cost"), 0.0),
                    source=str(row.get("source", "")).strip(),
                    basket_tag=normalize_basket_tag(row.get("basket_tag")),
                )
                for row in rows
                if str(row.get("strategy_lot_id", "")).strip() and _as_int(row.get("holding_qty"), 0) > 0
            ]
            rows, _ = _apply_local_sell_state_to_positions_rows(
                trade_date=trade_date,
                positions_rows=rows,
                opening_positions=adjustment_positions,
                sell_rows=sell_rows,
                quote_rows_by_stock={},
            )
        return rows

    for row in csv_rows:
        holding_qty = _as_int(row.get("holding_qty"), _as_int(row.get("quantity"), 0))
        buy_total_cost = _as_float(row.get("buy_total_cost"), _as_float(row.get("buy_avg_price"), 0.0) * holding_qty)
        current_price = _as_float(row.get("current_price"), _as_float(row.get("buy_avg_price"), 0.0))
        market_value = current_price * holding_qty
        unrealized = market_value - buy_total_cost
        unrealized_pct = 0.0 if buy_total_cost == 0 else unrealized / buy_total_cost
        estimated_exit = market_value - fees.estimate_sell_fee(market_value) - fees.estimate_sell_tax(market_value)
        rows.append(
            {
                "strategy_lot_id": row.get("strategy_lot_id", ""),
                "stock_id": row.get("stock_id", ""),
                "stock_name": row.get("stock_name", ""),
                "basket_tag": normalize_basket_tag(row.get("basket_tag")),
                "holding_qty": holding_qty,
                "buy_avg_price": _as_float(row.get("buy_avg_price")),
                "buy_total_cost": buy_total_cost,
                "current_price": current_price,
                "market_value": market_value,
                "unrealized_pnl": unrealized,
                "unrealized_pnl_pct": unrealized_pct,
                "estimated_exit_value_after_fee_tax": estimated_exit,
                "breakeven_sell_price": 0.0 if holding_qty == 0 else buy_total_cost / holding_qty,
            }
        )
    return rows


def _build_sell_rows(*, trade_date: date) -> list[dict[str, object]]:
    run_dir = auto_trading_dir_for(trade_date)
    rows = []
    for row in _read_csv_rows(run_dir / "sell_decisions.csv"):
        rows.append(
            {
                "strategy_lot_id": row.get("strategy_lot_id", ""),
                "stock_id": row.get("stock_id", ""),
                "basket_tag": normalize_basket_tag(row.get("basket_tag")),
                "can_sell_flag": row.get("can_sell_flag", row.get("sell_decision", "") == "sell"),
                "conservative_sell_price": _as_float(row.get("conservative_sell_price")),
                "conservative_profit": _as_float(row.get("conservative_profit")),
                "sell_decision": row.get("sell_decision", ""),
                "sell_decision_reason": row.get("sell_decision_reason", ""),
                "sell_order_price": _as_float(row.get("sell_order_price")) or "",
                "sell_order_status": row.get("sell_order_status", ""),
                "sell_submission_gate": row.get("sell_submission_gate", ""),
                "quote_timestamp": row.get("quote_timestamp", ""),
                "actual_fill_avg_price": _as_float(row.get("actual_fill_avg_price")) if row.get("actual_fill_avg_price", "") not in ("", None) else "",
                "sold_qty": _as_int(row.get("sold_qty"), 0),
                "remaining_qty": _as_int(row.get("remaining_qty"), 0) if row.get("remaining_qty", "") not in ("", None) else "",
                "allocated_buy_cost": _as_float(row.get("allocated_buy_cost")) if row.get("allocated_buy_cost", "") not in ("", None) else "",
                "realized_pnl": _as_float(row.get("realized_pnl")) if row.get("realized_pnl", "") not in ("", None) else "",
                "sell_pnl_source": row.get("sell_pnl_source", ""),
                "basket_recommendation": row.get("basket_recommendation", ""),
                "basket_threshold": _as_float(row.get("basket_threshold")) if row.get("basket_threshold", "") not in ("", None) else "",
                "basket_loser_loss_ratio": _as_float(row.get("basket_loser_loss_ratio")) if row.get("basket_loser_loss_ratio", "") not in ("", None) else "",
            }
        )
    return rows


def _build_chart_data(*, trade_date: date, overview: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    run_dir = auto_trading_dir_for(trade_date)
    pnl_rows = _read_csv_rows(run_dir / "pnl_snapshots.csv")
    if pnl_rows:
        x_labels = [row.get("snapshot_time", "")[-5:] or row.get("snapshot_time", "") for row in pnl_rows]
        comparison = {
            "x_labels": x_labels,
            "kind": "returns",
            "value_format": "percent",
            "caption": "累積報酬率比較。這不是單股看盤 K 線，而是策略績效與市場基準的相對走勢。",
            "series": [
                {"label": "策略", "color": "#244c5a", "values": [_as_float(row.get("strategy_return")) for row in pnl_rows]},
                {"label": "加權", "color": "#b85c38", "values": [_as_float(row.get("twii_return")) for row in pnl_rows]},
                {"label": "2330", "color": "#5a7d4d", "values": [_as_float(row.get("tsmc_return")) for row in pnl_rows]},
            ],
        }
        capital = {
            "x_labels": x_labels,
            "kind": "allocation",
            "value_format": "money",
            "caption": "資金配置圖：看現金與已投入資金的占比變化，不是價格走勢圖。",
            "series": [
                {"label": "現金", "color": "#244c5a", "values": [max(_as_float(overview.get("hard_budget")) - _as_float(row.get("cash_used")), 0.0) for row in pnl_rows]},
                {"label": "已投入", "color": "#b85c38", "values": [_as_float(row.get("cash_used")) for row in pnl_rows]},
            ],
        }
        return comparison, capital

    comparison = {
        "x_labels": [trade_date.isoformat()],
        "kind": "returns",
        "value_format": "percent",
        "caption": "累積報酬率比較。資料只有一個時間點時，改以基準比較條呈現。",
        "series": [
            {"label": "策略", "color": "#244c5a", "values": [_as_float(overview.get("strategy_return"))]},
            {"label": "加權", "color": "#b85c38", "values": [0.0]},
            {"label": "2330", "color": "#5a7d4d", "values": [0.0]},
        ],
    }
    capital = {
        "x_labels": [trade_date.isoformat()],
        "kind": "allocation",
        "value_format": "money",
        "caption": "資金配置圖：看目前現金與已投入資金的占比，不是價格走勢圖。",
        "series": [
            {"label": "現金", "color": "#244c5a", "values": [_as_float(overview.get("remaining_cash"))]},
            {"label": "已投入", "color": "#b85c38", "values": [_as_float(overview.get("used_cash"))]},
        ],
    }
    return comparison, capital


def _build_daily_report(settings: Settings, trade_date: date) -> dict[str, object]:
    plan = resolve_week_trade_plan(trade_date)
    run_dir = auto_trading_dir_for(trade_date)
    state_data = _read_json(run_dir / "state.json")
    workflow_status_summary = (
        state_data.get("workflow_status", {})
        if isinstance(state_data.get("workflow_status", {}), dict)
        else {}
    )
    buy_loop_state = state_data.get("buy_loop", {}) if isinstance(state_data.get("buy_loop", {}), dict) else {}
    sell_loop_state = state_data.get("sell_loop", {}) if isinstance(state_data.get("sell_loop", {}), dict) else {}
    events = _read_event_rows(run_dir / "event_log.jsonl")
    selection_rows = _load_selection_snapshot_rows(trade_date=trade_date, provider_name=str(state_data.get("provider_name", settings.providers.active)))
    preselect_count, final_list_count = _selection_snapshot_counts(selection_rows)
    buy_execution_rows = _build_buy_execution_rows(trade_date=trade_date, selection_rows=selection_rows)
    sell_rows = _build_sell_rows(trade_date=trade_date)
    positions_rows = _build_positions_rows(
        trade_date=trade_date,
        fees=settings.fees,
        auto=settings.auto_trading,
        buy_execution_rows=buy_execution_rows,
        sell_rows=sell_rows,
    )
    excluded_positions_rows = _build_excluded_positions_rows(trade_date=trade_date)
    broker_underheld_rows = _build_broker_underheld_report_rows(trade_date=trade_date)
    ambiguous_fill_report_rows = _build_ambiguous_fill_report_rows(trade_date=trade_date)
    fallback_position_rows = [
        row
        for row in positions_rows
        if "fallback" in str(row.get("status", "")).strip().lower()
    ]
    fallback_statuses = sorted(
        {
            str(row.get("status", "")).strip()
            for row in fallback_position_rows
            if str(row.get("status", "")).strip()
        }
    )
    positions_source_date = ""
    current_positions_csv_rows = _read_csv_rows(run_dir / "positions.csv")
    if not current_positions_csv_rows and positions_rows:
        positions_source_date = str(sell_loop_state.get("positions_source_date", "")).strip()
        if not positions_source_date:
            _, source_date, _ = _load_strategy_positions_for_sell_loop(trade_date)
            positions_source_date = source_date.isoformat() if source_date else ""
    buy_ambiguous_fill_guard_count = _as_int(buy_loop_state.get("ambiguous_fill_guard_count"), 0)
    sell_ambiguous_fill_guard_count = _as_int(sell_loop_state.get("ambiguous_fill_guard_count"), 0)
    ambiguous_fill_guard_count = buy_ambiguous_fill_guard_count + sell_ambiguous_fill_guard_count
    excluded_position_guard_count = _as_int(sell_loop_state.get("excluded_position_guard_count"), 0)
    buy_broker_underheld_guard_count = _as_int(buy_loop_state.get("broker_underheld_guard_count"), 0)
    sell_broker_underheld_guard_count = _as_int(sell_loop_state.get("broker_underheld_guard_count"), 0)
    broker_underheld_guard_count = buy_broker_underheld_guard_count + sell_broker_underheld_guard_count
    raw_buy_broker_underheld_guard_stocks = buy_loop_state.get("broker_underheld_guard_stocks", [])
    raw_broker_underheld_guard_stocks = sell_loop_state.get("broker_underheld_guard_stocks", [])
    if isinstance(raw_buy_broker_underheld_guard_stocks, (list, tuple, set)):
        buy_broker_underheld_guard_stocks = [
            str(stock_id).strip()
            for stock_id in raw_buy_broker_underheld_guard_stocks
            if str(stock_id).strip()
        ]
    elif raw_buy_broker_underheld_guard_stocks:
        buy_broker_underheld_guard_stocks = [str(raw_buy_broker_underheld_guard_stocks).strip()]
    else:
        buy_broker_underheld_guard_stocks = []
    if isinstance(raw_broker_underheld_guard_stocks, (list, tuple, set)):
        sell_broker_underheld_guard_stocks = [
            str(stock_id).strip()
            for stock_id in raw_broker_underheld_guard_stocks
            if str(stock_id).strip()
        ]
    elif raw_broker_underheld_guard_stocks:
        sell_broker_underheld_guard_stocks = [str(raw_broker_underheld_guard_stocks).strip()]
    else:
        sell_broker_underheld_guard_stocks = []
    realized_pnl = sum(_as_float(row.get("realized_pnl"), 0.0) for row in sell_rows)
    realized_cost_basis = sum(_as_float(row.get("allocated_buy_cost"), 0.0) for row in sell_rows)
    pnl_snapshot = build_pnl_snapshot(
        run_id=_run_id(trade_date),
        trade_date=trade_date,
        positions_rows=positions_rows,
        realized_pnl=realized_pnl,
        realized_cost_basis=realized_cost_basis,
        snapshot_time=datetime.now(TAIPEI),
    )
    used_cash = _as_float(pnl_snapshot.get("cash_used"), 0.0)
    current_equity = _as_float(pnl_snapshot.get("strategy_equity"), 0.0)
    unrealized = _as_float(pnl_snapshot.get("unrealized_pnl"), 0.0)
    strategy_pnl_after_fee_tax = _as_float(pnl_snapshot.get("total_pnl_after_fee_tax"), 0.0)
    strategy_return = _as_float(pnl_snapshot.get("strategy_return"), 0.0)
    basket_summary = _resolve_basket_summary(
        state_data=state_data,
        sell_rows=sell_rows,
        settings=settings,
        current_equity=current_equity,
        unrealized=unrealized,
        strategy_return=strategy_return,
    )
    effective_buy_cutoff_day = _effective_buy_cutoff_day(settings, plan)
    overview = {
        "last_update_time": datetime.now(TAIPEI).isoformat(timespec="seconds"),
        "weekly_budget": settings.auto_trading.weekly_budget,
        "hard_budget": settings.auto_trading.hard_budget,
        "buy_chase_after_first_trade_day_enabled": _allow_buy_after_first_trade_day(settings),
        "effective_buy_cutoff_day": effective_buy_cutoff_day.isoformat() if effective_buy_cutoff_day else "",
        "used_cash": used_cash,
        "remaining_cash": max(settings.auto_trading.hard_budget - used_cash, 0.0),
        "current_equity": current_equity,
        "strategy_pnl_after_fee_tax": strategy_pnl_after_fee_tax,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized,
        "strategy_return": strategy_return,
        "position_data_quality": "fallback" if fallback_position_rows else "direct",
        "positions_source_date": positions_source_date,
        "fallback_position_lot_count": len(fallback_position_rows),
        "ambiguous_fill_guard_count": ambiguous_fill_guard_count,
        "excluded_position_guard_count": excluded_position_guard_count,
        "buy_ambiguous_fill_guard_count": buy_ambiguous_fill_guard_count,
        "sell_ambiguous_fill_guard_count": sell_ambiguous_fill_guard_count,
        "broker_underheld_guard_count": broker_underheld_guard_count,
        "buy_broker_underheld_guard_count": buy_broker_underheld_guard_count,
        "sell_broker_underheld_guard_count": sell_broker_underheld_guard_count,
    }
    if workflow_status_summary:
        overview.update(
            {
                "workflow_completed_steps": _as_int(workflow_status_summary.get("completed_steps"), 0),
                "workflow_pending_steps": _as_int(workflow_status_summary.get("pending_steps"), 0),
                "workflow_closed_steps": _as_int(workflow_status_summary.get("closed_steps"), 0),
            }
        )
    selection_source = _selection_source_summary(
        trade_date=trade_date,
        provider_name=str(state_data.get("provider_name", settings.providers.active)).strip(),
        preselect_count=preselect_count,
        final_list_count=final_list_count,
        settings=settings,
    )
    if selection_source:
        overview.update(selection_source)
    dashboard_refresh_payload = _current_dashboard_refresh_payload(trade_date, state_data)
    dashboard_refresh_summary = _dashboard_refresh_summary({"dashboard_refresh": dashboard_refresh_payload}) if dashboard_refresh_payload else _dashboard_refresh_summary(state_data)
    if not dashboard_refresh_summary:
        dashboard_refresh_summary = _refresh_dashboard_event_summary(trade_date)
    if dashboard_refresh_summary:
        overview.update(dashboard_refresh_summary)
    dashboard_last_materializing_payload = _resolve_last_materializing_refresh_payload(trade_date, state_data)
    dashboard_last_materializing_summary = _dashboard_last_materializing_summary({**state_data, **dashboard_refresh_summary})
    if dashboard_last_materializing_summary:
        overview.update(dashboard_last_materializing_summary)
    selection_materialization = _selection_materialization_summary(
        trade_date=trade_date,
        selection_source_status=overview.get("selection_source_status", ""),
        buy_cutoff_day=effective_buy_cutoff_day,
        last_trade_day=plan.last_trade_day,
    )
    if selection_materialization:
        overview.update(selection_materialization)
    weekly_settlement = _weekly_settlement_summary(
        trade_date=trade_date,
        state_data=state_data,
    )
    if weekly_settlement:
        overview.update(weekly_settlement)
    comparison_chart, capital_chart = _build_chart_data(trade_date=trade_date, overview=overview)
    warnings: list[str] = []
    guarded_live_task_evidence = _allowed_live_task_log_evidence(trade_date)
    post_guarded_check = _post_guarded_order_check_report_summary(trade_date)
    sell_loop_readiness = _sell_loop_readiness_report_summary(trade_date)
    if post_guarded_check:
        guarded_post_effective_recommendation = _effective_guarded_post_recommendation(post_guarded_check)
        overview.update(
            {
                "guarded_post_check_status": str(post_guarded_check.get("after_status", "")).strip(),
                "guarded_post_check_recommendation": str(post_guarded_check.get("recommendation", "")).strip(),
                "guarded_post_check_effective_recommendation": guarded_post_effective_recommendation,
                "guarded_post_check_effective_recommendation_note": _describe_workflow_action(
                    guarded_post_effective_recommendation
                ),
                "guarded_post_check_reconciled": bool(post_guarded_check.get("reconciled", False)),
                "guarded_post_check_fills_count": _as_int(post_guarded_check.get("fills_count"), 0),
                "guarded_post_check_positions_count": _as_int(post_guarded_check.get("positions_count"), 0),
                "guarded_post_check_next_run_guard_status": str(post_guarded_check.get("next_run_guard_status", "")).strip(),
                "guarded_post_check_next_run_guard_message": str(post_guarded_check.get("next_run_guard_message", "")).strip(),
                "guarded_post_check_config_timing_status": str(post_guarded_check.get("config_timing_status", "")).strip(),
                "guarded_post_check_config_timing_message": str(post_guarded_check.get("config_timing_message", "")).strip(),
                "guarded_post_check_config_path": str(post_guarded_check.get("config_path", "")).strip(),
                "guarded_post_check_config_last_modified": str(post_guarded_check.get("config_last_modified", "")).strip(),
                "guarded_post_check_task_recorded_at": str(post_guarded_check.get("task_recorded_at", "")).strip(),
            }
        )
    if sell_loop_readiness:
        overview.update(
            {
                "sell_loop_readiness_blocking_reason": str(sell_loop_readiness.get("blocking_reason", "")).strip(),
                "sell_loop_readiness_next_action": str(sell_loop_readiness.get("next_action", "")).strip(),
                "sell_loop_readiness_next_action_note": _describe_workflow_action(
                    sell_loop_readiness.get("next_action", "")
                ),
                "sell_loop_readiness_positions_ready": bool(sell_loop_readiness.get("positions_ready", False)),
                "sell_loop_readiness_positions_count": _as_int(sell_loop_readiness.get("positions_count"), 0),
                "sell_loop_readiness_positions_source_date": str(sell_loop_readiness.get("positions_source_date", "")).strip(),
                "sell_loop_readiness_post_guarded_effective_recommendation": str(
                    sell_loop_readiness.get("post_guarded_effective_recommendation", "")
                ).strip(),
                "sell_loop_readiness_post_guarded_effective_recommendation_note": _describe_workflow_action(
                    sell_loop_readiness.get("post_guarded_effective_recommendation", "")
                ),
                "sell_loop_readiness_post_guarded_next_run_guard_status": str(
                    sell_loop_readiness.get("post_guarded_next_run_guard_status", "")
                ).strip(),
                "sell_loop_readiness_post_guarded_config_timing_status": str(
                    sell_loop_readiness.get("post_guarded_config_timing_status", "")
                ).strip(),
                "sell_loop_readiness_post_guarded_config_timing_message": str(
                    sell_loop_readiness.get("post_guarded_config_timing_message", "")
                ).strip(),
                "sell_loop_readiness_post_guarded_config_path": str(
                    sell_loop_readiness.get("post_guarded_config_path", "")
                ).strip(),
                "sell_loop_readiness_post_guarded_config_last_modified": str(
                    sell_loop_readiness.get("post_guarded_config_last_modified", "")
                ).strip(),
                "sell_loop_readiness_post_guarded_task_recorded_at": str(
                    sell_loop_readiness.get("post_guarded_task_recorded_at", "")
                ).strip(),
            }
        )
    ordering_summary = _today_ordering_summary(
        guarded_effective_recommendation=overview.get("guarded_post_check_effective_recommendation", ""),
        selection_source_status=overview.get("selection_source_status", ""),
        trade_date=trade_date,
        buy_cutoff_day=effective_buy_cutoff_day,
        last_trade_day=plan.last_trade_day,
    )
    if ordering_summary:
        overview.update(ordering_summary)
    ordering_conflict = _today_ordering_conflict_summary(
        selection_source_status=overview.get("selection_source_status", ""),
        trade_date=trade_date,
        buy_cutoff_day=effective_buy_cutoff_day,
        last_trade_day=plan.last_trade_day,
    )
    if ordering_conflict:
        overview.update(ordering_conflict)
    ordering_conflict_resolution = _today_ordering_conflict_resolution_summary(
        today_ordering_conflict_status=overview.get("today_ordering_conflict_status", ""),
        trade_date=trade_date,
        next_trade_day=_next_trade_day_after(trade_date),
    )
    if ordering_conflict_resolution:
        overview.update(ordering_conflict_resolution)
    new_order_submission = _today_new_order_submission_summary(
        today_ordering_status=overview.get("today_ordering_status", ""),
    )
    if new_order_submission:
        overview.update(new_order_submission)
    overview.update(
        _today_status_summary(
            trade_date=trade_date,
            last_trade_day=plan.last_trade_day,
            buy_execution_rows=buy_execution_rows,
            sell_rows=sell_rows,
            positions_rows=positions_rows,
            today_new_order_submission_status=overview.get("today_new_order_submission_status", ""),
        )
    )
    selection_source_carry_forward = _selection_source_carry_forward_summary(
        selection_source_status=overview.get("selection_source_status", ""),
        trade_date=trade_date,
    )
    if selection_source_carry_forward:
        overview.update(selection_source_carry_forward)
    guarded_live_warning = _guarded_live_task_warning(trade_date, guarded_live_task_evidence)
    if guarded_live_warning:
        warnings.append(guarded_live_warning)
    if str(overview.get("selection_source_status", "")).strip() == "same_day_a_preselect_missing_pass":
        warnings.append(str(overview.get("selection_source_note", "")).strip())
    if str(overview.get("today_ordering_conflict_note", "")).strip():
        warnings.append(str(overview.get("today_ordering_conflict_note", "")).strip())
    if str(overview.get("today_ordering_conflict_resolution_note", "")).strip():
        warnings.append(str(overview.get("today_ordering_conflict_resolution_note", "")).strip())
    if str(overview.get("selection_materialization_note", "")).strip():
        warnings.append(str(overview.get("selection_materialization_note", "")).strip())
    guarded_post_status = str(post_guarded_check.get("after_status", "")).strip() if post_guarded_check else ""
    guarded_post_recommendation = str(post_guarded_check.get("recommendation", "")).strip() if post_guarded_check else ""
    guarded_post_effective_recommendation = _effective_guarded_post_recommendation(post_guarded_check) if post_guarded_check else ""
    guarded_post_effective_recommendation_note = (
        str(overview.get("guarded_post_check_effective_recommendation_note", "")).strip()
        if post_guarded_check
        else ""
    )
    guarded_post_next_run_guard_status = str(post_guarded_check.get("next_run_guard_status", "")).strip() if post_guarded_check else ""
    guarded_post_next_run_guard_message = str(post_guarded_check.get("next_run_guard_message", "")).strip() if post_guarded_check else ""
    guarded_post_config_timing_message = str(post_guarded_check.get("config_timing_message", "")).strip() if post_guarded_check else ""
    if guarded_post_status == "schedule_query_failed":
        warnings.append(
            "受保護下單後檢查無法從目前執行環境核對 Windows 排程；這是排程查詢受限，不代表下單失敗。"
        )
    elif guarded_post_status.startswith("skipped_"):
        if guarded_post_next_run_guard_status == "live_guard_ready":
            warnings.append(
                "受保護下單後檢查顯示：今天的受保護下單任務曾被真實下單保護條件擋下，但下一次排程現在已就緒；若同日 09:10-13:20 視窗仍開著，排程應補跑。"
            )
        elif guarded_post_next_run_guard_status == "scheduled_task_time_passed":
            warnings.append(
                "受保護下單後檢查顯示：真實下單保護條件問題現在已修好，但今天的受保護下單補跑視窗已關閉；今天不再送這筆單。"
            )
        else:
            warnings.append(
                f"受保護下單後檢查顯示：受保護下單任務被真實下單保護條件擋下；下一步：{guarded_post_effective_recommendation_note or guarded_post_effective_recommendation or guarded_post_recommendation}。"
            )
        if guarded_post_config_timing_message:
            warnings.append(guarded_post_config_timing_message)
        if guarded_post_next_run_guard_message:
            warnings.append(f"下一次受保護下單排程就緒狀態：{guarded_post_next_run_guard_message}")
    elif guarded_post_status == "scheduled_time_passed_without_artifacts":
        warnings.append(
            "受保護下單後檢查顯示：排程時間已過，但還沒找到 order/fill 產物；請先檢查最新任務日誌，再做唯讀 reconciliation。"
        )
    elif guarded_post_status == "task_failed":
        warnings.append("受保護下單後檢查找到任務失敗證據；重新送單前請先檢查任務日誌。")
    elif guarded_post_status == "submitted_no_fills_yet":
        warnings.append("受保護下單後檢查找到送單證據，但還沒有成交；請等市場更新後再做唯讀 broker reconciliation。")
    elif guarded_post_recommendation == "fills_found_review_positions_and_sell_loop":
        warnings.append("受保護下單後檢查找到成交；請檢查部位與賣出就緒狀態。")
    sell_readiness_reason = str(sell_loop_readiness.get("blocking_reason", "")).strip() if sell_loop_readiness else ""
    sell_readiness_next_action = str(sell_loop_readiness.get("next_action", "")).strip() if sell_loop_readiness else ""
    sell_readiness_next_action_note = str(overview.get("sell_loop_readiness_next_action_note", "")).strip()
    if sell_readiness_reason == "not_last_trade_day":
        warnings.append(f"sell-loop readiness 顯示目前先等待：{sell_readiness_next_action_note or sell_readiness_next_action}。")
    elif sell_readiness_reason == "ready_to_prepare":
        warnings.append("賣出就緒狀態顯示已有策略部位；開盤後可先跑 prepare-only 賣出準備，真正送單仍等你的 live 命令。")
    elif sell_readiness_reason == "no_strategy_positions":
        if sell_readiness_next_action == "today_guarded_run_missed_wait_for_next_guarded_schedule_no_backfill":
            warnings.append(
                "賣出就緒狀態被擋住，因為今天的受保護下單還沒有建立策略部位；若同日 09:10-13:20 視窗仍開著，受保護下單排程應先補跑。"
            )
        elif sell_readiness_next_action == "historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill":
            warnings.append(
                "賣出就緒狀態被擋住，因為今天的受保護下單補跑視窗已關閉，且沒有建立任何策略部位；今天不再送這筆單。"
            )
        else:
            warnings.append("賣出就緒狀態被擋住，因為目前還沒有可用的策略部位。")
    elif sell_readiness_reason == "broker_reconcile_recommended":
        warnings.append("賣出就緒狀態建議在進入 sell-loop 評估前，先做唯讀 broker reconciliation。")
    elif sell_readiness_reason == "guarded_task_failed":
        warnings.append("賣出就緒狀態被受保護下單任務失敗證據擋住。")
    elif sell_readiness_reason == "ready_to_evaluate":
        warnings.append("賣出就緒狀態顯示目前已有部位，可進入 sell-loop 評估。")
    if bool(state_data.get("calendar_missing_warning", False)):
        warnings.append("交易日曆缺失，暫時改用平日規則當 fallback。")
    if not selection_rows:
        warnings.append("這個 trade date 尚未載入任何 selection rows。")
    if not buy_execution_rows and selection_rows:
        warnings.append("已有 selection rows，但尚未產生任何 buy execution rows。")
    if excluded_positions_rows:
        warnings.append(f"發現 {len(excluded_positions_rows)} 筆 excluded broker position rows，這些部位不屬於目前策略 lot 範圍。")
    if broker_underheld_rows:
        warnings.append(
            f"發現 {len(broker_underheld_rows)} 筆 broker-underheld rows，策略部位數量高於券商持股。"
        )
    if ambiguous_fill_report_rows:
        ambiguous_stocks = ",".join(
            sorted({str(row.get("stock_id", "")).strip() for row in ambiguous_fill_report_rows if str(row.get("stock_id", "")).strip()})
        )
        warnings.append(
            f"發現 {len(ambiguous_fill_report_rows)} 筆 ambiguous live fill rows，暫時無法安全對 lot；positions / PnL 會先排除它們（{ambiguous_stocks}）。"
        )
    if buy_ambiguous_fill_guard_count > 0:
        warnings.append(
            f"buy loop 因 ambiguous fills 尚待人工 reconciliation，已阻擋 {buy_ambiguous_fill_guard_count} 筆 strategy lot 的新 live submit。"
        )
    if sell_ambiguous_fill_guard_count > 0:
        warnings.append(
            f"sell loop 因 ambiguous fills 尚待人工 reconciliation，已阻擋 {sell_ambiguous_fill_guard_count} 筆 strategy lot 的新 live submit。"
        )
    if excluded_position_guard_count > 0:
        warnings.append(
            f"sell loop 已阻擋 {excluded_position_guard_count} 筆 strategy lot 的新 live submit，因為券商持股包含同檔股票的 excluded 非策略部位。"
        )
    if buy_broker_underheld_guard_count > 0:
        stocks_note = f" ({', '.join(sorted(set(buy_broker_underheld_guard_stocks)))})" if buy_broker_underheld_guard_stocks else ""
        warnings.append(
            f"buy loop 已阻擋 {buy_broker_underheld_guard_count} 筆 strategy lot 的新 live submit，因為券商持股低於策略部位{stocks_note}。"
        )
    if sell_broker_underheld_guard_count > 0:
        stocks_note = f" ({', '.join(sorted(set(sell_broker_underheld_guard_stocks)))})" if sell_broker_underheld_guard_stocks else ""
        warnings.append(
            f"sell loop 已阻擋 {sell_broker_underheld_guard_count} 筆 strategy lot 的新 live submit，因為券商持股低於策略部位{stocks_note}。"
        )
    sell_pnl_fallback_rows = [
        row
        for row in sell_rows
        if str(row.get("sell_pnl_source", "")).strip() == "local_sell_order_fallback"
    ]
    if sell_pnl_fallback_rows:
        warnings.append(
            f"已實現賣出 PnL 有 {len(sell_pnl_fallback_rows)} 筆 strategy lot 先使用 local order fallback，因為 live fill 明細仍不完整。"
        )
    if fallback_position_rows:
        warnings.append(
            f"Positions/PnL use fallback reconstruction for {len(fallback_position_rows)} strategy lots ({', '.join(fallback_statuses)})."
        )
    if positions_source_date and positions_source_date != trade_date.isoformat():
        warnings.append(
            f"因今日 positions.csv 不可用，部位 baseline 改從 {positions_source_date} 載入。"
        )
    settlement_next_action_note = str(overview.get("weekly_settlement_next_action_note", "")).strip()
    weekly_settlement_action = (
        settlement_next_action_note
        or "收盤後重新執行 render_report 與 settle_week，補齊日 / 週 snapshots。"
    )
    next_actions = [
        "Run render_report every 5 minutes during market hours to refresh the current dashboard.",
        weekly_settlement_action,
    ]
    if str(overview.get("today_new_order_submission_status", "")).strip() == "no_auto_new_buy_paths_remaining_today":
        carry_forward_note = str(overview.get("selection_source_carry_forward_note", "")).strip()
        materialization_action_note = str(overview.get("selection_materialization_next_action_note", "")).strip()
        next_actions = [
            "今天不會再有新的自動買單；永豐自動交易目前只追蹤既有持倉、市值與風控。",
            "同日 A 來源 / AB 每日預選是獨立專案輸出；非買進日的預選只作網頁呈現與觀察，不代表自動交易要重新選股或補買。",
            materialization_action_note or "維持目前持倉追蹤；下次自動交易選股 / 買進依下一個週一買進流程處理。",
            carry_forward_note or "永豐自動交易下次選股 / 買進依下一個週一買進流程處理。",
            weekly_settlement_action,
        ]
    return {
        "trade_date": trade_date.isoformat(),
        "week_id": _week_id(plan),
        "run_id": _run_id(trade_date),
        "status": str(state_data.get("status", "")).strip(),
        "mode": _report_mode(state_data),
        "provider_name": str(state_data.get("provider_name", settings.providers.active)),
        "buy_cutoff_day": plan.buy_cutoff_day.isoformat() if plan.buy_cutoff_day else "",
        "last_trade_day": plan.last_trade_day.isoformat() if plan.last_trade_day else "",
        "preselect_count": preselect_count,
        "final_list_count": final_list_count,
        "workflow_status": workflow_status_summary,
        "workflow_completed_steps": _as_int(workflow_status_summary.get("completed_steps"), 0),
        "workflow_pending_steps": _as_int(workflow_status_summary.get("pending_steps"), 0),
        "workflow_closed_steps": _as_int(workflow_status_summary.get("closed_steps"), 0),
        "overview": overview,
        "dashboard_refresh": dashboard_refresh_payload,
        "dashboard_refresh_last_materializing": dashboard_last_materializing_payload,
        "selection_rows": selection_rows,
        "buy_execution_rows": buy_execution_rows,
        "positions_rows": positions_rows,
        "excluded_positions_rows": excluded_positions_rows,
        "broker_underheld_rows": broker_underheld_rows,
        "ambiguous_fill_rows": ambiguous_fill_report_rows,
        "sell_rows": sell_rows,
        "guarded_live_task_evidence": guarded_live_task_evidence or {},
        "post_guarded_order_check": post_guarded_check,
        "sell_loop_readiness": sell_loop_readiness,
        "basket_summary": basket_summary,
        "comparison_chart": comparison_chart,
        "capital_chart": capital_chart,
        "events": events,
        "warnings": warnings,
        "next_actions": next_actions,
    }


def _build_weekly_summary(settings: Settings, trade_date: date) -> dict[str, object]:
    plan = resolve_week_trade_plan(trade_date)
    start_date = plan.week_trade_days[0] if plan.week_trade_days else trade_date
    secondary_add_date = plan.week_trade_days[1] if len(plan.week_trade_days) >= 2 else None
    end_date = plan.week_trade_days[-1] if plan.week_trade_days else trade_date
    daily_rows: list[dict[str, object]] = []
    last_daily_report: dict[str, object] | None = None
    expired_unfilled: list[dict[str, object]] = []
    excluded_positions: dict[str, dict[str, object]] = {}
    broker_underheld_rows: list[dict[str, object]] = []
    ambiguous_fills: list[dict[str, object]] = []
    fallback_day_count = 0
    fallback_position_lot_count = 0
    ambiguous_fill_guard_day_count = 0
    ambiguous_fill_guard_lot_count = 0
    excluded_position_guard_day_count = 0
    excluded_position_guard_lot_count = 0
    broker_underheld_guard_day_count = 0
    broker_underheld_guard_lot_count = 0
    cumulative_realized_pnl = 0.0
    cumulative_realized_cost_basis = 0.0

    for day in plan.week_trade_days or [trade_date]:
        daily_report = _build_daily_report(settings, day)
        last_daily_report = daily_report
        selection_rows = daily_report.get("selection_rows", [])
        daily_sell_rows = daily_report.get("sell_rows", [])
        cumulative_realized_pnl += sum(_as_float(row.get("realized_pnl"), 0.0) for row in daily_sell_rows)
        cumulative_realized_cost_basis += sum(_as_float(row.get("allocated_buy_cost"), 0.0) for row in daily_sell_rows)
        cumulative_pnl_snapshot = build_pnl_snapshot(
            run_id=_run_id(day),
            trade_date=day,
            positions_rows=list(daily_report.get("positions_rows", [])),
            realized_pnl=cumulative_realized_pnl,
            realized_cost_basis=cumulative_realized_cost_basis,
            snapshot_time=datetime.now(TAIPEI),
        )
        daily_overview = daily_report.get("overview", {})
        position_data_quality = str(daily_overview.get("position_data_quality", "")).strip() or "direct"
        fallback_lot_count = _as_int(daily_overview.get("fallback_position_lot_count"), 0)
        ambiguous_fill_guard_count = _as_int(daily_overview.get("ambiguous_fill_guard_count"), 0)
        excluded_position_guard_count = _as_int(daily_overview.get("excluded_position_guard_count"), 0)
        broker_underheld_guard_count = _as_int(daily_overview.get("broker_underheld_guard_count"), 0)
        if position_data_quality == "fallback":
            fallback_day_count += 1
            fallback_position_lot_count += fallback_lot_count
        if ambiguous_fill_guard_count > 0:
            ambiguous_fill_guard_day_count += 1
            ambiguous_fill_guard_lot_count += ambiguous_fill_guard_count
        if excluded_position_guard_count > 0:
            excluded_position_guard_day_count += 1
            excluded_position_guard_lot_count += excluded_position_guard_count
        if broker_underheld_guard_count > 0:
            broker_underheld_guard_day_count += 1
            broker_underheld_guard_lot_count += broker_underheld_guard_count
        twii_return = _as_float(daily_report.get("comparison_chart", {}).get("series", [{}, {"values": [0.0]}])[1].get("values", [0.0])[-1])
        tsmc_return = _as_float(daily_report.get("comparison_chart", {}).get("series", [{}, {}, {"values": [0.0]}])[2].get("values", [0.0])[-1])
        final_count = sum(1 for row in selection_rows if row.get("final_flag"))
        preselect_count = sum(1 for row in selection_rows if row.get("preselect_flag"))
        secondary_add_qty = sum(
            _as_int(row.get("target_qty"), 0)
            for row in selection_rows
            if row.get("final_flag") and normalize_basket_tag(row.get("basket_tag")) == "secondary_add"
        )
        daily_rows.append(
            {
                "date": day.isoformat(),
                "twii": f"{twii_return:.2%}",
                "tsmc": f"{tsmc_return:.2%}",
                "preselect": preselect_count,
                "a_preselect": sum(1 for row in selection_rows if str(row.get("source", "")).upper() == "A"),
                "b_preselect": sum(1 for row in selection_rows if str(row.get("source", "")).upper() == "B"),
                "final_list": final_count,
                "a_final": sum(1 for row in selection_rows if row.get("final_flag") and str(row.get("source", "")).upper() == "A"),
                "b_final": sum(1 for row in selection_rows if row.get("final_flag") and str(row.get("source", "")).upper() == "B"),
                "equal_weight_version": final_count,
                "weighted_version": sum(_as_int(row.get("target_qty"), 0) for row in selection_rows if row.get("final_flag")),
                "monday_plan": final_count if day == start_date else "N/A",
                "secondary_add": "N/A"
                if (
                    not settings.auto_trading.enable_secondary_add
                    or not _allow_buy_after_first_trade_day(settings)
                    or day != secondary_add_date
                )
                else secondary_add_qty,
                "actual_combined": sum(_as_int(row.get("holding_qty"), 0) for row in daily_report.get("positions_rows", [])),
                "position_data_quality": position_data_quality,
                "fallback_lot_count": fallback_lot_count,
                "ambiguous_fill_guard_count": ambiguous_fill_guard_count,
                "excluded_position_guard_count": excluded_position_guard_count,
                "broker_underheld_guard_count": broker_underheld_guard_count,
                "positions_source_date": str(daily_overview.get("positions_source_date", "")).strip(),
                "strategy_return_value": _as_float(cumulative_pnl_snapshot.get("strategy_return"), 0.0),
                "twii_return_value": twii_return,
                "tsmc_return_value": tsmc_return,
            }
        )
        for event in daily_report.get("events", []):
            if "expired" in str(event.get("result", "")).lower():
                expired_unfilled.append(
                    {
                        "stock_id": event.get("stock_id", ""),
                        "stop_day": day.isoformat(),
                        "reason": event.get("result", ""),
                    }
                )
        for row in daily_report.get("excluded_positions_rows", []):
            item = str(row.get("item", "")).strip()
            if not item:
                continue
            excluded_positions[item] = {"item": item}
        for row in daily_report.get("broker_underheld_rows", []):
            broker_underheld_rows.append(
                {
                    "date": day.isoformat(),
                    "stock_id": row.get("stock_id", ""),
                    "stock_name": row.get("stock_name", ""),
                    "broker_qty": _as_int(row.get("broker_qty"), 0),
                    "strategy_qty": _as_int(row.get("strategy_qty"), 0),
                    "missing_qty": _as_int(row.get("missing_qty"), 0),
                    "reason": row.get("reason", ""),
                }
            )
        for row in daily_report.get("ambiguous_fill_rows", []):
            ambiguous_fills.append(
                {
                    "date": day.isoformat(),
                    "stock_id": row.get("stock_id", ""),
                    "side": row.get("side", ""),
                    "fill_qty": _as_int(row.get("fill_qty"), 0),
                    "fill_price": _as_float(row.get("fill_price"), 0.0),
                    "fill_time": row.get("fill_time", ""),
                    "broker_fill_id": row.get("broker_fill_id", ""),
                    "broker_custom_field": row.get("broker_custom_field", ""),
                    "fill_assignment_status": row.get("fill_assignment_status", ""),
                }
            )

    latest = last_daily_report or _build_daily_report(settings, trade_date)
    weekly_fill_rows = _week_csv_rows(plan, "fills.csv")
    weekly_sell_decisions = _week_csv_rows(plan, "sell_decisions.csv")
    lot_ledger_rows = load_week_lot_ledger(trade_date)
    weekly_realized_pnl = sum(_as_float(row.get("realized_pnl"), 0.0) for row in weekly_sell_decisions)
    weekly_realized_cost_basis = sum(_as_float(row.get("allocated_buy_cost"), 0.0) for row in weekly_sell_decisions)
    weekly_pnl_snapshot = build_pnl_snapshot(
        run_id=_run_id(end_date),
        trade_date=end_date,
        positions_rows=list(latest.get("positions_rows", [])),
        realized_pnl=weekly_realized_pnl,
        realized_cost_basis=weekly_realized_cost_basis,
        snapshot_time=datetime.now(TAIPEI),
    )
    weekly_totals = {
        "weekly_budget": settings.auto_trading.weekly_budget,
        "hard_budget": settings.auto_trading.hard_budget,
        "total_buy_cost": _as_float(weekly_pnl_snapshot.get("cash_used")),
        "final_market_value": _as_float(weekly_pnl_snapshot.get("strategy_equity")),
        "total_profit": _as_float(weekly_pnl_snapshot.get("total_pnl_after_fee_tax")),
        "strategy_return": _as_float(weekly_pnl_snapshot.get("strategy_return")),
    }
    benchmark_summary = {
        "twii_return": _as_float(latest.get("comparison_chart", {}).get("series", [{}, {"values": [0.0]}])[1].get("values", [0.0])[-1]),
        "tsmc_return": _as_float(latest.get("comparison_chart", {}).get("series", [{}, {}, {"values": [0.0]}])[2].get("values", [0.0])[-1]),
        "strategy_excess_vs_twii": weekly_totals["strategy_return"] - _as_float(latest.get("comparison_chart", {}).get("series", [{}, {"values": [0.0]}])[1].get("values", [0.0])[-1]),
        "strategy_excess_vs_tsmc": weekly_totals["strategy_return"] - _as_float(latest.get("comparison_chart", {}).get("series", [{}, {}, {"values": [0.0]}])[2].get("values", [0.0])[-1]),
    }
    buy_fill_qty = sum(_as_int(row.get("fill_qty"), 0) for row in weekly_fill_rows if _normalize_fill_side(row.get("side", "")) == "Buy")
    sell_fill_qty = sum(_as_int(row.get("fill_qty"), 0) for row in weekly_fill_rows if _normalize_fill_side(row.get("side", "")) == "Sell")
    excluded_position_count = len(excluded_positions)
    strategy_lot_count = len(lot_ledger_rows)
    open_lot_count = sum(1 for row in lot_ledger_rows if _as_int(row.get("closing_qty"), 0) > 0)
    settled_lot_count = sum(1 for row in lot_ledger_rows if str(row.get("lot_status", "")) in {"settled", "closed"})
    ambiguous_fill_count = len(ambiguous_fills)
    trade_results = [
        {"label": "weekly_budget", "value": settings.auto_trading.weekly_budget},
        {"label": "hard_budget", "value": settings.auto_trading.hard_budget},
        {"label": "used_cash", "value": _as_float(weekly_pnl_snapshot.get("cash_used"))},
        {"label": "current_equity", "value": _as_float(weekly_pnl_snapshot.get("strategy_equity"))},
        {"label": "strategy_pnl_after_fee_tax", "value": _as_float(weekly_pnl_snapshot.get("total_pnl_after_fee_tax"))},
        {"label": "strategy_lot_count", "value": strategy_lot_count},
        {"label": "open_lot_count", "value": open_lot_count},
        {"label": "settled_lot_count", "value": settled_lot_count},
        {"label": "buy_fill_qty", "value": buy_fill_qty},
        {"label": "sell_fill_qty", "value": sell_fill_qty},
        {"label": "realized_pnl", "value": _as_float(weekly_pnl_snapshot.get("realized_pnl"))},
        {"label": "unrealized_pnl", "value": _as_float(weekly_pnl_snapshot.get("unrealized_pnl"))},
        {"label": "excluded_positions_count", "value": excluded_position_count},
        {"label": "ambiguous_fill_count", "value": ambiguous_fill_count},
        {"label": "ambiguous_fill_guard_day_count", "value": ambiguous_fill_guard_day_count},
        {"label": "ambiguous_fill_guard_lot_count", "value": ambiguous_fill_guard_lot_count},
        {"label": "excluded_position_guard_day_count", "value": excluded_position_guard_day_count},
        {"label": "excluded_position_guard_lot_count", "value": excluded_position_guard_lot_count},
        {"label": "broker_underheld_guard_day_count", "value": broker_underheld_guard_day_count},
        {"label": "broker_underheld_guard_lot_count", "value": broker_underheld_guard_lot_count},
        {"label": "fallback_day_count", "value": fallback_day_count},
        {"label": "fallback_position_lot_count", "value": fallback_position_lot_count},
    ]
    comparison_chart = {
        "x_labels": [row["date"] for row in daily_rows],
        "kind": "returns",
        "value_format": "percent",
        "caption": "週內累積報酬率比較，用來看策略相對加權與 2330 的表現。",
        "series": [
            {"label": "策略", "color": "#244c5a", "values": [_as_float(row.get("strategy_return_value"), 0.0) for row in daily_rows]},
            {"label": "加權", "color": "#b85c38", "values": [_as_float(row.get("twii_return_value"), 0.0) for row in daily_rows]},
            {"label": "2330", "color": "#5a7d4d", "values": [_as_float(row.get("tsmc_return_value"), 0.0) for row in daily_rows]},
        ],
    }
    return {
        "week_id": f"{start_date.isoformat()}_{end_date.isoformat()}",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "mode": str(latest.get("mode", "dry_run")),
        "provider_name": str(latest.get("provider_name", settings.providers.active)),
        "last_update_time": datetime.now(TAIPEI).isoformat(timespec="seconds"),
        "weekly_totals": weekly_totals,
        "benchmark_summary": benchmark_summary,
        "daily_rows": daily_rows,
        "excluded_positions": list(excluded_positions.values()),
        "broker_underheld_rows": broker_underheld_rows,
        "trade_results": trade_results,
        "lot_ledger_rows": lot_ledger_rows,
        "ambiguous_fill_rows": ambiguous_fills,
        "expired_unfilled": expired_unfilled,
        "tuning_suggestions": (
            [
                f"Review overrun_tolerance currently set to {settings.auto_trading.overrun_tolerance:,.0f} TWD.",
                "Validate conservative sell thresholds against real fills once live sell loop is wired.",
                "Keep 2330 and TAIEX as fixed benchmark lines on all dashboard views.",
            ]
            + (
                [f"Review fallback-heavy days first: {fallback_day_count} trading days used reconstructed positions this week."]
                if fallback_day_count > 0
                else []
            )
            + (
                [f"Resolve ambiguous-fill guards before relaxing live execution: {ambiguous_fill_guard_day_count} trading days had guarded strategy lots this week."]
                if ambiguous_fill_guard_day_count > 0
                else []
            )
            + (
                [f"Resolve excluded-position guards before live selling the same stock: {excluded_position_guard_day_count} trading days had non-strategy broker scope mixed with strategy lots this week."]
                if excluded_position_guard_day_count > 0
                else []
            )
            + (
                [f"Reconcile broker-underheld guards before trusting live sell capacity: {broker_underheld_guard_day_count} trading days had broker holdings below strategy positions this week."]
                if broker_underheld_guard_day_count > 0
                else []
            )
        ),
        "comparison_chart": comparison_chart,
    }


def command_prepare_week(args: argparse.Namespace) -> int:
    settings = Settings.load()
    ensure_runtime_directories()
    trade_date = _parse_trade_date(args.trade_date)
    confirmation_status = _a_preselect_confirmation_time_status(settings, trade_date)
    if confirmation_status.get("required") and not confirmation_status.get("ready"):
        print(f"prepare_week_skipped: {confirmation_status.get('reason')}")
        print(f"a_preselect_confirmation_start_at: {confirmation_status.get('start_at')}")
        print(f"note: {A_PRESELECT_CONFIRMATION_REMINDER}")
        return 0
    provider = _provider_from_settings(settings)
    preselect = provider.load_preselect(trade_date)
    plan = resolve_week_trade_plan(trade_date)
    run_dir = auto_trading_dir_for(trade_date)
    store = SQLiteStateStore(run_dir)
    store.initialize()
    run_id = _run_id(trade_date)
    week_id = _week_id(plan)
    _update_run_state(
        store,
        trade_date,
        status="prepared",
        week_id=week_id,
        provider_name=provider.provider_name(),
        week_trade_days=[item.isoformat() for item in plan.week_trade_days],
        buy_cutoff_day=plan.buy_cutoff_day.isoformat() if plan.buy_cutoff_day else None,
        last_trade_day=plan.last_trade_day.isoformat() if plan.last_trade_day else None,
        calendar_missing_warning=plan.calendar_missing_warning,
        preselect_count=len(preselect),
        a_preselect_confirmation_policy=A_PRESELECT_CONFIRMATION_POLICY if confirmation_status.get("required") else "",
        a_preselect_confirmation_start_at=confirmation_status.get("start_at", ""),
        a_preselect_confirmation_ready=bool(confirmation_status.get("ready", True)),
        **_selection_source_summary(
            trade_date=trade_date,
            provider_name=provider.provider_name(),
            preselect_count=len(preselect),
            final_list_count=0,
            settings=settings,
        ),
    )
    _write_selection_csv(_manual_trade_dir(trade_date) / "auto_trade_preselect.csv", preselect)
    store.write_rows_csv("preselect.csv", _selection_rows(preselect, provider.provider_name()))
    store.append_event(
        run_id=run_id,
        timestamp=datetime.now(TAIPEI).isoformat(),
        level="INFO",
        event_type="prepare_week",
        message=f"Loaded {len(preselect)} preselect items from {provider.provider_name()}",
        metadata={"week_id": week_id},
    )
    print(f"trade_date: {trade_date}")
    print(f"provider: {provider.provider_name()}")
    print(f"preselect_count: {len(preselect)}")
    print(f"buy_cutoff_day: {plan.buy_cutoff_day}")
    if confirmation_status.get("required"):
        print(f"note: {A_PRESELECT_CONFIRMATION_REMINDER}")
    return 0


def command_track_until_final(args: argparse.Namespace) -> int:
    settings = Settings.load()
    trade_date = _parse_trade_date(args.trade_date)
    confirmation_status = _a_preselect_confirmation_time_status(settings, trade_date)
    if confirmation_status.get("required") and not confirmation_status.get("ready"):
        print(f"track_until_final_skipped: {confirmation_status.get('reason')}")
        print(f"a_preselect_confirmation_start_at: {confirmation_status.get('start_at')}")
        print(f"note: {A_PRESELECT_CONFIRMATION_REMINDER}")
        return 0
    provider = _provider_from_settings(settings)
    preselect = provider.load_preselect(trade_date)
    run_dir = auto_trading_dir_for(trade_date)
    store = SQLiteStateStore(run_dir)
    store.initialize()
    quote_provider = _load_fake_quote_provider(settings)
    rows: list[dict[str, object]] = []
    for item in preselect:
        snapshot = quote_provider.get_snapshot(item.stock_id) if quote_provider else None
        rows.append(
            {
                "stock_id": item.stock_id,
                "stock_name": item.stock_name,
                "timestamp": snapshot.timestamp.isoformat() if snapshot else "",
                "last_price": snapshot.last_price if snapshot else "",
                "bid1": snapshot.bid1 if snapshot else "",
                "ask1": snapshot.ask1 if snapshot else "",
            }
        )
    store.write_rows_csv("quote_snapshots.csv", rows)
    _update_run_state(
        store,
        trade_date,
        status="tracked_until_final",
        tracked_snapshot_count=len(rows),
        used_fake_quotes=bool(quote_provider),
    )
    store.append_event(
        run_id=_run_id(trade_date),
        timestamp=datetime.now(TAIPEI).isoformat(),
        level="INFO",
        event_type="track_until_final",
        message="Tracked preselect quotes using example snapshots." if quote_provider else "No quote provider configured.",
        metadata={"snapshot_count": len(rows)},
    )
    print(f"tracked_symbols: {len(rows)}")
    print(f"used_fake_quotes: {bool(quote_provider)}")
    return 0


def command_finalize(args: argparse.Namespace) -> int:
    settings = Settings.load()
    trade_date = _parse_trade_date(args.trade_date)
    confirmation_status = _a_preselect_confirmation_time_status(settings, trade_date)
    if confirmation_status.get("required") and not confirmation_status.get("ready"):
        print(f"finalize_skipped: {confirmation_status.get('reason')}")
        print(f"a_preselect_confirmation_start_at: {confirmation_status.get('start_at')}")
        print(f"note: {A_PRESELECT_CONFIRMATION_REMINDER}")
        return 0
    plan = resolve_week_trade_plan(trade_date)
    provider = _provider_from_settings(settings)
    preselect = provider.load_preselect(trade_date)
    manual_final_list = provider.load_final_list(trade_date)
    quote_provider = _load_fake_quote_provider(settings)
    result = finalize_selection(
        trade_date,
        preselect,
        provider.provider_name(),
        manual_final_list=manual_final_list,
        final_list_origin=_provider_final_list_origin(provider.provider_name()) if manual_final_list else None,
        quote_provider=quote_provider,
        max_names=args.max_names,
    )

    direct_a_preselect_provider = provider.provider_name() == "ab_llm_preselect_json"
    estimated_prices, unresolved_stock_ids = _estimated_prices_for_finalize(
        result.final_items,
        quote_provider,
        prefer_reference_price=direct_a_preselect_provider,
    )
    if direct_a_preselect_provider and unresolved_stock_ids:
        joined = ", ".join(unresolved_stock_ids)
        raise RuntimeError(
            "Missing usable reference prices for direct A-preselect names: "
            f"{joined}. Refusing to size them with the old 10.0 fallback."
        )

    sizing_result = size_selection(
        result.final_items,
        estimated_prices,
        auto=settings.auto_trading,
        fees=settings.fees,
        trade_date=trade_date,
        week_trade_days=plan.week_trade_days,
    )

    input_dir = _manual_trade_dir(trade_date)
    _write_selection_csv(input_dir / "auto_trade_final_list.csv", result.final_items)

    run_dir = auto_trading_dir_for(trade_date)
    store = SQLiteStateStore(run_dir)
    store.initialize()
    store.write_rows_csv(
        "sizing.csv",
        [
            {
                "stock_id": row.item.stock_id,
                "stock_name": row.item.stock_name,
                "source": row.item.source,
                "basket_tag": row.item.normalized_basket_tag(),
                "source_weight": row.source_weight,
                "target_qty": row.target_qty,
                "estimated_buy_price": row.estimated_buy_price,
                "projected_cost": row.projected_cost,
            }
            for row in sizing_result.rows
        ],
    )
    _update_run_state(
        store,
        trade_date,
        status="finalized",
        preselect_count=len(preselect),
        final_list_count=len(result.final_items),
        projected_total_cost=sizing_result.projected_total_cost,
        sizing_weekly_budget=settings.auto_trading.weekly_budget,
        sizing_hard_budget=settings.auto_trading.hard_budget,
        sizing_weekly_execution_week_id=weekly_execution_week_id_for(trade_date),
        sizing_weekly_execution_enabled=settings.auto_trading.weekly_execution_enabled,
        a_preselect_confirmation_policy=A_PRESELECT_CONFIRMATION_POLICY if confirmation_status.get("required") else "",
        a_preselect_confirmation_start_at=confirmation_status.get("start_at", ""),
        a_preselect_confirmed=bool(confirmation_status.get("required")),
        a_preselect_confirmed_at=confirmation_status.get("now", "") if confirmation_status.get("required") else "",
        used_manual_final_list=result.used_manual_final_list,
        used_provider_final_list=result.used_provider_final_list,
        final_list_origin=result.final_list_origin,
        secondary_add_trade_day=plan.week_trade_days[1].isoformat() if len(plan.week_trade_days) >= 2 else "",
        **_selection_source_summary(
            trade_date=trade_date,
            provider_name=provider.provider_name(),
            preselect_count=len(preselect),
            final_list_count=len(result.final_items),
            settings=settings,
        ),
    )
    store.append_event(
        run_id=_run_id(trade_date),
        timestamp=datetime.now(TAIPEI).isoformat(),
        level="INFO",
        event_type="finalize",
        message=f"Finalized {len(result.final_items)} symbols.",
        metadata={
            "used_manual_final_list": result.used_manual_final_list,
            "used_provider_final_list": result.used_provider_final_list,
            "final_list_origin": result.final_list_origin,
        },
    )
    print(f"final_count: {len(result.final_items)}")
    print(f"used_manual_final_list: {result.used_manual_final_list}")
    print(f"used_provider_final_list: {result.used_provider_final_list}")
    print(f"final_list_origin: {result.final_list_origin}")
    print(f"projected_total_cost: {sizing_result.projected_total_cost:.2f}")
    if confirmation_status.get("required"):
        print(f"note: {A_PRESELECT_CONFIRMATION_REMINDER}")
    return 0


def command_buy_loop(args: argparse.Namespace) -> int:
    settings = Settings.load()
    trade_date = _parse_trade_date(args.trade_date)
    plan = resolve_week_trade_plan(trade_date)
    skip_reason = _buy_loop_skip_reason(settings, trade_date, plan)
    if skip_reason:
        print(f"buy_loop_skipped: {skip_reason}")
        return 0

    quote_provider = _load_fake_quote_provider(settings)
    buy_source_trade_date = _buy_loop_source_trade_date(settings, trade_date, plan)
    run_dir = auto_trading_dir_for(trade_date)
    source_run_dir = auto_trading_dir_for(buy_source_trade_date)
    store = SQLiteStateStore(run_dir)
    store.initialize()
    source_store = SQLiteStateStore(source_run_dir)
    source_state = source_store.read_state_json()
    confirmation_status = _a_preselect_sizing_confirmation_status(
        settings,
        buy_source_trade_date,
        source_state,
    )
    if confirmation_status.get("required") and not confirmation_status.get("ready"):
        print(f"buy_loop_skipped: {confirmation_status.get('reason')}")
        print(f"buy_source_trade_date: {buy_source_trade_date.isoformat()}")
        print(f"a_preselect_confirmation_start_at: {confirmation_status.get('start_at')}")
        print(f"a_preselect_confirmed_at: {confirmation_status.get('confirmed_at', '')}")
        print(f"note: {A_PRESELECT_CONFIRMATION_REMINDER}")
        return 0
    sizing_path = source_run_dir / "sizing.csv"
    if not sizing_path.exists():
        raise RuntimeError(f"Missing sizing.csv for buy source date {buy_source_trade_date.isoformat()}. Run finalize first.")

    with sizing_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    can_go_live, live_guard = _buy_loop_can_go_live(
        settings,
        live=args.live,
        confirm_live=args.confirm_live,
        trade_date=trade_date,
    )
    if can_go_live:
        budget_guard_allowed, budget_guard_reason = _buy_loop_sizing_budget_guard(
            settings,
            source_state,
            trade_date=trade_date,
        )
        if not budget_guard_allowed:
            can_go_live = False
            live_guard = budget_guard_reason
    broker: FakeBrokerAdapter | ShioajiSinoPacBrokerAdapter
    live_cash_budget = settings.auto_trading.hard_budget
    if can_go_live:
        broker = ShioajiSinoPacBrokerAdapter(settings, simulation=False)
        summary = broker.get_account_summary()
        if not summary.signed:
            raise RuntimeError("Live buy_loop blocked because broker account is not signed.")
        if not broker.is_market_open():
            raise RuntimeError("Live buy_loop blocked because the Taiwan stock market is closed.")
        if not broker.supports_order_lot("intraday_odd_lot"):
            raise RuntimeError("Live buy_loop blocked because intraday odd lot orders are not supported.")
        live_cash_budget = min(settings.auto_trading.hard_budget, broker.get_cash_available())
    else:
        broker = FakeBrokerAdapter(cash_available=settings.auto_trading.hard_budget)

    secondary_add_allowed = _secondary_add_allowed_on_trade_date(settings, trade_date, plan)
    existing_order_rows = _buy_loop_existing_order_rows(plan, trade_date, buy_source_trade_date)
    existing_orders_by_lot = {
        str(row.get("strategy_lot_id", "")).strip() or _strategy_lot_id(buy_source_trade_date, row.get("stock_id", ""), row.get("basket_tag")): row
        for row in existing_order_rows
        if row.get("stock_id")
    }
    order_rows: list[dict[str, object]] = []
    quote_rows: list[dict[str, object]] = []
    target_stock_ids: set[str] = set()
    selection_meta_by_stock: dict[str, dict[str, object]] = {}
    selection_meta_by_strategy_lot: dict[str, dict[str, object]] = {}
    quote_rows_by_stock: dict[str, QuoteState] = {}
    ambiguous_fill_rows: list[dict[str, object]] = []
    planned_target_stock_ids = {
        str(row.get("stock_id", "")).strip()
        for row in rows
        if _as_int(row.get("target_qty"), 0) > 0 and str(row.get("stock_id", "")).strip()
    }
    opening_positions, _, _ = _load_strategy_positions_for_sell_loop(trade_date)
    opening_qty_by_lot = {
        position.strategy_lot_id: int(position.holding_qty)
        for position in opening_positions
        if str(position.strategy_lot_id).strip()
    }
    already_bought_qty_by_lot = _already_bought_qty_by_lot(trade_date)
    pre_submit_fill_rows: list[dict[str, object]] = []
    ambiguous_fill_guard_stocks: set[str] = set()
    broker_underheld_guard_rows: list[dict[str, object]] = []
    broker_underheld_guard_stocks: set[str] = set()
    if can_go_live and broker is not None and planned_target_stock_ids:
        pre_submit_fill_rows = _selected_fill_rows(
            broker=broker,
            trade_date=trade_date,
            target_stock_ids=planned_target_stock_ids,
        )
        ambiguous_fill_guard_stocks = set(_ambiguous_fill_stock_ids(pre_submit_fill_rows))
        broker_underheld_guard_rows = _broker_qty_below_strategy_guard_rows(
            broker_positions=broker.get_positions(),
            opening_positions=opening_positions,
        )
        broker_underheld_guard_stocks = {
            str(row.get("stock_id", "")).strip()
            for row in broker_underheld_guard_rows
            if str(row.get("stock_id", "")).strip()
        }
    store.write_rows_csv("broker_position_mismatches.csv", broker_underheld_guard_rows)
    live_actions = 0
    live_budget_remaining = live_cash_budget
    ambiguous_fill_guard_lot_ids: list[str] = []
    broker_underheld_guard_lot_ids: list[str] = []
    for row in rows:
        stock_id = row["stock_id"]
        basket_tag = normalize_basket_tag(row.get("basket_tag"))
        strategy_lot_id = str(row.get("strategy_lot_id", "")).strip() or _strategy_lot_id(buy_source_trade_date, stock_id, basket_tag)
        broker_custom_field = str(row.get("broker_custom_field", "")).strip() or _broker_custom_field(strategy_lot_id, "BL")
        qty = int(row["target_qty"])
        if qty <= 0:
            continue
        if basket_tag == "secondary_add" and not secondary_add_allowed:
            order_rows.append(
                {
                    "strategy_lot_id": strategy_lot_id,
                    "stock_id": stock_id,
                    "stock_name": row.get("stock_name", stock_id),
                    "basket_tag": basket_tag,
                    "target_price": _as_float(row.get("estimated_buy_price")),
                    "target_qty": qty,
                    "active_order_qty": 0,
                    "filled_qty": 0,
                    "remaining_qty": qty,
                    "action": "skip",
                    "status": "secondary_add_waiting_trade_day",
                    "order_id": "",
                    "order_price": "",
                    "broker_custom_field": broker_custom_field,
                    "current_mode": current_buy_mode(datetime.now(TAIPEI)).value,
                    "last_price": "",
                    "bid1": "",
                    "ask1": "",
                    "quote_timestamp": "",
                    "buy_submission_gate": "secondary_add_trade_day_closed",
                    "note": "secondary_add_only_on_second_trade_day",
                }
            )
            continue
        target_stock_ids.add(stock_id)
        selection_meta_by_stock[stock_id] = {
            "stock_name": row.get("stock_name", stock_id),
            "source": row.get("source", ""),
            "basket_tag": basket_tag,
        }
        selection_meta_by_strategy_lot[strategy_lot_id] = {
            "stock_name": row.get("stock_name", stock_id),
            "source": row.get("source", ""),
            "basket_tag": basket_tag,
        }

        quote, resolved_name, exchange_hint, quote_timestamp = _buy_loop_quote(
            stock_id=stock_id,
            estimated_buy_price=float(row["estimated_buy_price"]),
            quote_provider=quote_provider,
            broker=broker,
            can_go_live=can_go_live,
        )
        quote_is_fresh = not quote_is_stale(
            parse_quote_timestamp(quote_timestamp),
            now=datetime.now(TAIPEI),
            stale_seconds=settings.auto_trading.quote_stale_seconds,
        )
        quote_rows_by_stock[stock_id] = quote
        quote_rows.append(
            {
                "stock_id": stock_id,
                "stock_name": row.get("stock_name", resolved_name),
                "timestamp": quote_timestamp,
                "last_price": quote.last_price,
                "bid1": quote.bid1 or "",
                "ask1": quote.ask1 or "",
            }
        )
        current_mode = current_buy_mode(datetime.now(TAIPEI))
        target_price = current_mode_target_price(quote, current_mode)

        existing_order_row = existing_orders_by_lot.get(strategy_lot_id, {})
        broker_custom_field = str(existing_order_row.get("broker_custom_field", "")).strip() or broker_custom_field
        existing_order_id = str(existing_order_row.get("order_id", "")).strip()
        default_filled_qty = max(
            _as_int(existing_order_row.get("filled_qty"), 0),
            opening_qty_by_lot.get(strategy_lot_id, 0),
            already_bought_qty_by_lot.get(strategy_lot_id, 0),
        )
        existing_buy_state = None
        if can_go_live and existing_order_id and isinstance(broker, ShioajiSinoPacBrokerAdapter):
            existing_buy_state = _existing_buy_order_state(
                existing_row=existing_order_row,
                broker=broker,
                default_remaining_qty=max(qty - default_filled_qty, 0),
            )
        broker_custom_field_buy_state = None
        if can_go_live and broker_custom_field:
            broker_custom_field_buy_state = _broker_custom_field_buy_order_state(
                broker=broker,
                broker_custom_field=broker_custom_field,
                stock_id=stock_id,
            )
        if broker_custom_field_buy_state and (
            existing_buy_state is None or existing_buy_state.gate_reason == "existing_buy_order_unverified"
        ):
            existing_buy_state = broker_custom_field_buy_state
        filled_qty = existing_buy_state.filled_qty if existing_buy_state else default_filled_qty
        remaining_qty = max(qty - filled_qty, 0)

        managed_existing = None
        if existing_buy_state and existing_buy_state.status == "active":
            managed_existing = ManagedOrder(
                strategy_lot_id=strategy_lot_id,
                stock_id=stock_id,
                order_id=existing_buy_state.order_id,
                order_price=existing_buy_state.order_price,
                order_qty=existing_buy_state.filled_qty + existing_buy_state.remaining_qty,
                filled_qty=existing_buy_state.filled_qty,
                remaining_qty=existing_buy_state.remaining_qty,
                active=True,
            )

        action = plan_order_action(
            managed_existing if managed_existing and managed_existing.active else None,
            target_price=target_price,
            remaining_qty=remaining_qty,
            reprice_threshold_ticks=args.reprice_threshold_ticks,
        )
        has_active_live_order = bool(existing_buy_state and existing_buy_state.status == "active")
        buy_gate = live_buy_quote_gate(
            requested_action=action.action,
            quote_is_fresh=quote_is_fresh,
            existing_order_active=has_active_live_order,
        )
        buy_gate_reason = buy_gate.reason
        ambiguous_fill_guard_active = (
            can_go_live
            and stock_id in ambiguous_fill_guard_stocks
            and action.action in {"place", "cancel_replace"}
        )
        broker_underheld_guard_active = (
            can_go_live
            and stock_id in broker_underheld_guard_stocks
            and action.action in {"place", "cancel_replace"}
        )

        result = None
        status = existing_buy_state.status if existing_buy_state else str(existing_order_row.get("status", "pending"))
        order_id = existing_order_id
        order_price = existing_buy_state.order_price if existing_buy_state else _as_float(existing_order_row.get("target_price"), target_price)
        note = action.reason
        action_name = action.action
        active_order_qty = remaining_qty

        if can_go_live:
            metadata = {
                "note": "buy_loop_live",
                "stock_name": row.get("stock_name", resolved_name),
                "exchange_hint": exchange_hint,
                "custom_prefix": "BL",
                "strategy_lot_id": strategy_lot_id,
                "basket_tag": basket_tag,
                "broker_custom_field": broker_custom_field,
            }
            if existing_buy_state and existing_buy_state.gate_reason.startswith("broker_custom_field_buy_order_"):
                status = existing_buy_state.status
                order_id = existing_buy_state.order_id
                order_price = existing_buy_state.order_price or target_price
                active_order_qty = existing_buy_state.remaining_qty
                note = existing_buy_state.gate_reason
                action_name = "done" if remaining_qty <= 0 or existing_buy_state.status == "filled" else "keep"
                buy_gate_reason = existing_buy_state.gate_reason
                if existing_buy_state.remaining_qty > 0:
                    live_budget_remaining = max(
                        live_budget_remaining
                        - estimate_buy_order_cost(
                            order_price,
                            existing_buy_state.remaining_qty,
                            fees=settings.fees,
                            buffer_multiplier=settings.auto_trading.cost_buffer_multiplier,
                        ),
                        0.0,
                    )
            elif existing_buy_state and existing_buy_state.gate_reason == "existing_buy_order_unverified":
                status = existing_buy_state.status
                order_id = existing_buy_state.order_id
                order_price = existing_buy_state.order_price or target_price
                active_order_qty = existing_buy_state.remaining_qty
                note = existing_buy_state.gate_reason
                action_name = "done" if remaining_qty <= 0 else "keep"
                buy_gate_reason = existing_buy_state.gate_reason
                if existing_buy_state.remaining_qty > 0:
                    live_budget_remaining = max(
                        live_budget_remaining
                        - estimate_buy_order_cost(
                            order_price,
                            existing_buy_state.remaining_qty,
                            fees=settings.fees,
                            buffer_multiplier=settings.auto_trading.cost_buffer_multiplier,
                        ),
                        0.0,
                    )
            elif not buy_gate.allowed and buy_gate.status == "keep_existing_due_stale_quote" and managed_existing:
                status = existing_buy_state.status if existing_buy_state else status
                order_id = managed_existing.order_id
                order_price = managed_existing.order_price
                active_order_qty = managed_existing.remaining_qty
                note = buy_gate.reason
                action_name = "keep"
                live_budget_remaining = max(
                    live_budget_remaining
                    - estimate_buy_order_cost(
                        managed_existing.order_price,
                        managed_existing.remaining_qty,
                        fees=settings.fees,
                        buffer_multiplier=settings.auto_trading.cost_buffer_multiplier,
                    ),
                    0.0,
                )
            elif ambiguous_fill_guard_active:
                buy_gate_reason = "ambiguous_fill_guard"
                note = "ambiguous_fill_guard"
                if strategy_lot_id not in ambiguous_fill_guard_lot_ids:
                    ambiguous_fill_guard_lot_ids.append(strategy_lot_id)
                if managed_existing and managed_existing.active:
                    status = existing_buy_state.status if existing_buy_state else status
                    order_id = managed_existing.order_id
                    order_price = managed_existing.order_price
                    active_order_qty = managed_existing.remaining_qty
                    action_name = "keep"
                    live_budget_remaining = max(
                        live_budget_remaining
                        - estimate_buy_order_cost(
                            managed_existing.order_price,
                            managed_existing.remaining_qty,
                            fees=settings.fees,
                            buffer_multiplier=settings.auto_trading.cost_buffer_multiplier,
                        ),
                        0.0,
                    )
                else:
                    status = "blocked_ambiguous_fill"
                    order_id = ""
                    order_price = target_price
                    active_order_qty = 0
                    action_name = "skip"
            elif broker_underheld_guard_active:
                buy_gate_reason = "broker_qty_below_strategy_guard"
                note = "broker_qty_below_strategy_guard"
                if strategy_lot_id not in broker_underheld_guard_lot_ids:
                    broker_underheld_guard_lot_ids.append(strategy_lot_id)
                if managed_existing and managed_existing.active:
                    status = existing_buy_state.status if existing_buy_state else status
                    order_id = managed_existing.order_id
                    order_price = managed_existing.order_price
                    active_order_qty = managed_existing.remaining_qty
                    action_name = "keep"
                    live_budget_remaining = max(
                        live_budget_remaining
                        - estimate_buy_order_cost(
                            managed_existing.order_price,
                            managed_existing.remaining_qty,
                            fees=settings.fees,
                            buffer_multiplier=settings.auto_trading.cost_buffer_multiplier,
                        ),
                        0.0,
                    )
                else:
                    status = "blocked_broker_qty_mismatch"
                    order_id = ""
                    order_price = target_price
                    active_order_qty = 0
                    action_name = "skip"
            elif not buy_gate.allowed and buy_gate.status == "blocked_stale_quote":
                status = buy_gate.status
                order_id = ""
                order_price = target_price
                active_order_qty = 0
                note = buy_gate.reason
                action_name = "skip"
            elif action.action == "keep" and managed_existing and managed_existing.active:
                reserved_cost = estimate_buy_order_cost(
                    managed_existing.order_price,
                    managed_existing.remaining_qty,
                    fees=settings.fees,
                    buffer_multiplier=settings.auto_trading.cost_buffer_multiplier,
                )
                live_budget_remaining = max(live_budget_remaining - reserved_cost, 0.0)
                active_order_qty = managed_existing.remaining_qty
            elif action.action == "cancel_replace" and managed_existing and managed_existing.order_id:
                replacement_qty = _affordable_order_qty(
                    requested_qty=remaining_qty,
                    target_price=target_price,
                    remaining_budget=live_budget_remaining,
                    fees=settings.fees,
                    buffer_multiplier=settings.auto_trading.cost_buffer_multiplier,
                )
                if replacement_qty < remaining_qty:
                    status = existing_buy_state.status if existing_buy_state else status
                    order_id = managed_existing.order_id
                    order_price = managed_existing.order_price
                    active_order_qty = managed_existing.remaining_qty
                    note = "kept_existing_due_cash_limit"
                    action_name = "keep"
                    live_budget_remaining = max(
                        live_budget_remaining
                        - estimate_buy_order_cost(
                            managed_existing.order_price,
                            managed_existing.remaining_qty,
                            fees=settings.fees,
                            buffer_multiplier=settings.auto_trading.cost_buffer_multiplier,
                        ),
                        0.0,
                    )
                else:
                    broker.cancel_order(managed_existing.order_id)
                    resolved_after_cancel = _wait_for_cancel_resolution(broker, managed_existing.order_id)
                    if resolved_after_cancel and resolved_after_cancel.status == "filled":
                        status = "filled"
                        order_id = resolved_after_cancel.order_id
                        order_price = resolved_after_cancel.order_price
                        filled_qty = resolved_after_cancel.filled_qty
                        remaining_qty = resolved_after_cancel.remaining_qty
                        active_order_qty = resolved_after_cancel.remaining_qty
                        note = "filled_during_cancel"
                        action_name = "done"
                    else:
                        result = broker.place_buy_order(stock_id, target_price, replacement_qty, "intraday_odd_lot", metadata)
                        live_actions += 1
                        live_budget_remaining = max(
                            live_budget_remaining
                            - estimate_buy_order_cost(
                                target_price,
                                replacement_qty,
                                fees=settings.fees,
                                buffer_multiplier=settings.auto_trading.cost_buffer_multiplier,
                            ),
                            0.0,
                        )
                        active_order_qty = replacement_qty
            elif action.action == "place":
                affordable_qty = _affordable_order_qty(
                    requested_qty=remaining_qty,
                    target_price=target_price,
                    remaining_budget=live_budget_remaining,
                    fees=settings.fees,
                    buffer_multiplier=settings.auto_trading.cost_buffer_multiplier,
                )
                if affordable_qty <= 0:
                    status = "blocked_insufficient_cash"
                    order_id = ""
                    order_price = target_price
                    active_order_qty = 0
                    note = "blocked_insufficient_cash"
                    action_name = "skip"
                else:
                    if affordable_qty < remaining_qty:
                        note = f"cash_capped_qty:{affordable_qty}"
                    result = broker.place_buy_order(stock_id, target_price, affordable_qty, "intraday_odd_lot", metadata)
                    live_actions += 1
                    live_budget_remaining = max(
                        live_budget_remaining
                        - estimate_buy_order_cost(
                            target_price,
                            affordable_qty,
                            fees=settings.fees,
                            buffer_multiplier=settings.auto_trading.cost_buffer_multiplier,
                        ),
                        0.0,
                    )
                    active_order_qty = affordable_qty
            elif existing_buy_state:
                status = existing_buy_state.status
                order_id = existing_buy_state.order_id
                order_price = existing_buy_state.order_price
                active_order_qty = existing_buy_state.remaining_qty
            else:
                status = "idle"
                active_order_qty = 0
        else:
            if action.action in {"place", "cancel_replace"}:
                result = broker.place_buy_order(
                    stock_id,
                    target_price,
                    remaining_qty,
                    "intraday_odd_lot",
                    {"note": "buy_loop_dry_run"},
                )
                status = result.status
                order_id = result.order_id
                order_price = result.order_price
                active_order_qty = remaining_qty
            elif existing_order_row:
                status = str(existing_order_row.get("status", "kept"))
                active_order_qty = _as_int(existing_order_row.get("active_order_qty"), remaining_qty)
            else:
                status = "dry_run_keep"
                active_order_qty = 0

        if result is not None:
            status = result.status
            order_id = result.order_id
            order_price = result.order_price

        order_rows.append(
            {
                "strategy_lot_id": strategy_lot_id,
                "stock_id": stock_id,
                "stock_name": row.get("stock_name", resolved_name),
                "basket_tag": basket_tag,
                "target_price": target_price,
                "target_qty": qty,
                "active_order_qty": active_order_qty,
                "filled_qty": filled_qty,
                "remaining_qty": max(qty - filled_qty, 0) if action_name == "done" else remaining_qty,
                "action": action_name,
                "status": status,
                "order_id": order_id,
                "order_price": order_price,
                "broker_custom_field": broker_custom_field,
                "current_mode": current_mode.value,
                "last_price": quote.last_price,
                "bid1": quote.bid1 or "",
                "ask1": quote.ask1 or "",
                "quote_timestamp": quote_timestamp,
                "buy_submission_gate": buy_gate_reason if can_go_live else "",
                "note": note,
            }
        )
        store.append_event(
            run_id=_run_id(trade_date),
            timestamp=datetime.now(TAIPEI).isoformat(),
            level="INFO",
            event_type="buy_loop_stock",
            stock_id=stock_id,
            message=f"buy_loop {action_name} for {stock_id} in {'live' if can_go_live else 'dry_run'} mode.",
            metadata={
                "stock_name": row.get("stock_name", resolved_name),
                "basket_tag": basket_tag,
                "action": action_name,
                "status": status,
                "price": order_price or target_price,
                "qty": active_order_qty,
                "target_price": target_price,
                "order_price": order_price,
                "target_qty": qty,
                "filled_qty": filled_qty,
                "remaining_qty": remaining_qty,
                "current_mode": current_mode.value,
                "live": can_go_live,
                "quote_timestamp": quote_timestamp,
                "quote_is_fresh": quote_is_fresh,
                "buy_submission_gate": buy_gate_reason if can_go_live else "",
                "live_budget_remaining": live_budget_remaining,
            },
        )

    store.write_rows_csv("quote_snapshots.csv", quote_rows)
    store.write_rows_csv("orders.csv", order_rows)
    if ambiguous_fill_guard_lot_ids:
        store.append_event(
            run_id=_run_id(trade_date),
            timestamp=datetime.now(TAIPEI).isoformat(),
            level="WARNING",
            event_type="ambiguous_fill_guard",
            message=f"Blocked new live buy submissions for {len(ambiguous_fill_guard_lot_ids)} strategy lots because ambiguous fills require manual reconciliation first.",
            metadata={
                "strategy_lot_ids": ambiguous_fill_guard_lot_ids,
                "stocks": sorted(ambiguous_fill_guard_stocks),
            },
        )
    if broker_underheld_guard_lot_ids:
        store.append_event(
            run_id=_run_id(trade_date),
            timestamp=datetime.now(TAIPEI).isoformat(),
            level="WARNING",
            event_type="broker_qty_below_strategy_guard",
            message=f"Blocked new live buy submissions for {len(broker_underheld_guard_lot_ids)} strategy lots because broker holdings were lower than strategy positions.",
            metadata={
                "strategy_lot_ids": broker_underheld_guard_lot_ids,
                "stocks": sorted(broker_underheld_guard_stocks),
                "rows": broker_underheld_guard_rows,
            },
        )
    if can_go_live and isinstance(broker, ShioajiSinoPacBrokerAdapter):
        broker_positions = broker.get_positions()
        fills_rows = _selected_fill_rows(
            broker=broker,
            trade_date=trade_date,
            target_stock_ids=target_stock_ids,
        )
        store.write_rows_csv("fills.csv", fills_rows)
        ambiguous_fill_rows = [row for row in fills_rows if row.get("fill_assignment_status") == "ambiguous_unmapped_fill"]
        if ambiguous_fill_rows:
            ambiguous_stocks = _ambiguous_fill_stock_ids(fills_rows)
            store.append_event(
                run_id=_run_id(trade_date),
                timestamp=datetime.now(TAIPEI).isoformat(),
                level="WARNING",
                event_type="ambiguous_fill_mapping",
                message=f"Skipped {len(ambiguous_fill_rows)} live fills because no safe strategy lot mapping was available.",
                metadata={"count": len(ambiguous_fill_rows), "stocks": ambiguous_stocks},
            )
        positions_rows = _positions_rows_from_fills(
            trade_date=trade_date,
            fills_rows=fills_rows,
            selection_meta_by_stock=selection_meta_by_stock,
            selection_meta_by_strategy_lot=selection_meta_by_strategy_lot,
            quote_rows_by_stock=quote_rows_by_stock,
        )
        if not positions_rows:
            positions_rows = _positions_rows_from_local_orders(
                trade_date=trade_date,
                order_rows=order_rows,
                selection_meta_by_stock=selection_meta_by_stock,
                selection_meta_by_strategy_lot=selection_meta_by_strategy_lot,
                quote_rows_by_stock=quote_rows_by_stock,
            )
        if not positions_rows:
            positions_rows = _selected_positions_rows(
                trade_date=trade_date,
                broker=broker,
                target_stock_ids=target_stock_ids,
                selection_meta_by_stock=selection_meta_by_stock,
                selection_meta_by_strategy_lot=selection_meta_by_strategy_lot,
                quote_rows_by_stock=quote_rows_by_stock,
                opening_positions=opening_positions,
            )
        store.write_rows_csv("positions.csv", positions_rows)
        excluded_rows = _excluded_positions_rows(
            broker_positions=broker_positions,
            strategy_positions_rows=positions_rows,
        )
        store.write_rows_csv("excluded_positions.csv", excluded_rows)
        pnl_snapshot = _append_pnl_snapshot(
            store=store,
            trade_date=trade_date,
            positions_rows=positions_rows,
        )
    else:
        store.write_rows_csv("excluded_positions.csv", [])
        pnl_snapshot = {}
    repair_required_reason = _live_buy_repair_required_reason(
        requested_live=bool(args.live),
        can_go_live=can_go_live,
        live_guard=live_guard,
        order_rows=order_rows,
    )
    buy_loop_state: dict[str, object] = {
        "order_count": len(order_rows),
        "live_guard": live_guard,
        "live_actions": live_actions,
        "mode": "live" if can_go_live else "dry_run",
        "buy_source_trade_date": buy_source_trade_date.isoformat(),
        "buy_chase_mode": buy_source_trade_date != trade_date,
        "secondary_add_allowed": secondary_add_allowed,
        "cash_budget": live_cash_budget,
        "cash_budget_remaining": live_budget_remaining,
        "excluded_positions_count": len(excluded_rows) if can_go_live else 0,
        "ambiguous_fill_count": len(ambiguous_fill_rows) if can_go_live else 0,
        "ambiguous_fill_guard_count": len(ambiguous_fill_guard_lot_ids),
        "broker_underheld_guard_count": len(broker_underheld_guard_lot_ids),
        "broker_underheld_guard_stocks": sorted(broker_underheld_guard_stocks),
    }
    state_fields: dict[str, object] = {
        "buy_loop": buy_loop_state,
        "pnl_snapshot": pnl_snapshot,
    }
    if args.live:
        state_fields["repair_confirmation_required"] = _repair_confirmation_required_payload(
            required=bool(repair_required_reason),
            reason=repair_required_reason,
        )
    _update_run_state(
        store,
        trade_date,
        status="buy_loop_live" if can_go_live else "buy_loop_dry_run",
        **state_fields,
    )
    if repair_required_reason:
        store.append_event(
            run_id=_run_id(trade_date),
            timestamp=datetime.now(TAIPEI).isoformat(),
            level="WARNING",
            event_type="repair_confirmation_required",
            message=(
                "Live buy was requested but was not safely complete; repair confirmation "
                "email reply is required before any continuation."
            ),
            metadata=_repair_confirmation_required_payload(required=True, reason=repair_required_reason),
        )
    store.append_event(
        run_id=_run_id(trade_date),
        timestamp=datetime.now(TAIPEI).isoformat(),
        level="INFO",
        event_type="buy_loop",
        message=(
            f"Processed {len(order_rows)} buy-loop rows in "
            f"{'live' if can_go_live else 'dry_run'} mode."
        ),
        metadata={
            "live_guard": live_guard,
            "live_actions": live_actions,
            "buy_source_trade_date": buy_source_trade_date.isoformat(),
            "buy_chase_mode": buy_source_trade_date != trade_date,
        },
    )
    print(f"buy_orders: {len(order_rows)}")
    print(f"mode: {'live' if can_go_live else 'dry_run'}")
    print(f"live_guard: {live_guard}")
    print(f"live_actions: {live_actions}")
    return 0


def command_sell_loop(args: argparse.Namespace) -> int:
    settings = Settings.load()
    trade_date = _parse_trade_date(args.trade_date)
    plan = resolve_week_trade_plan(trade_date)
    prepare_only = bool(getattr(args, "prepare_only", False))
    requested_live = bool(getattr(args, "live", False))
    confirm_live = bool(getattr(args, "confirm_live", False))
    if plan.last_trade_day != trade_date and not prepare_only:
        print("sell_loop_skipped: not_last_trade_day")
        return 0

    run_dir = auto_trading_dir_for(trade_date)
    store = SQLiteStateStore(run_dir)
    store.initialize()
    can_go_live, live_guard = _buy_loop_can_go_live(
        settings,
        live=requested_live and not prepare_only,
        confirm_live=confirm_live,
        trade_date=trade_date,
    )
    read_only_live = prepare_only and requested_live and confirm_live
    if prepare_only:
        if read_only_live:
            live_guard = "sell_prepare_only_read_only_live"
        elif requested_live:
            live_guard = "sell_prepare_only_confirm_live_missing"
        else:
            live_guard = "sell_prepare_only_dry_run"
    broker: ShioajiSinoPacBrokerAdapter | None = None
    now_taipei = datetime.now(TAIPEI)
    live_sell_window = now_taipei.time() >= time(13, 0)
    after_1320 = now_taipei.time() >= time(13, 20)
    live_actions = 0
    broker_checks_enabled = can_go_live or read_only_live
    if broker_checks_enabled:
        broker = ShioajiSinoPacBrokerAdapter(settings, simulation=False)
        summary = broker.get_account_summary()
        if not summary.signed:
            raise RuntimeError("Live sell_loop blocked because broker account is not signed.")
        if not broker.is_market_open():
            raise RuntimeError("Live sell_loop blocked because the Taiwan stock market is closed.")

    positions, source_date, source_rows = _load_strategy_positions_for_sell_loop(trade_date)
    if not positions:
        store.write_rows_csv("sell_decisions.csv", [])
        store.write_rows_csv("excluded_positions.csv", [])
        _update_run_state(
            store,
            trade_date,
            status="sell_loop_no_positions",
            sell_loop={
                "sell_candidates": 0,
                "basket_recommendation": "hold",
                "live_guard": live_guard,
                "live_actions": 0,
                "prepare_only": prepare_only,
                "read_only_live": read_only_live,
                "live_sell_window": live_sell_window,
            },
        )
        store.append_event(
            run_id=_run_id(trade_date),
            timestamp=datetime.now(TAIPEI).isoformat(),
            level="INFO",
            event_type="sell_loop",
            message="目前沒有可用的策略部位可進行賣出迴圈評估。",
            metadata={"trade_date": trade_date.isoformat()},
        )
        print("sell_candidates: 0")
        print("basket_recommendation: hold")
        return 0

    if source_rows:
        store.write_rows_csv("positions.csv", [{key: value for key, value in row.items()} for row in source_rows])
    if source_date is not None:
        source_excluded_rows = _read_csv_rows(auto_trading_dir_for(source_date) / "excluded_positions.csv")
        store.write_rows_csv("excluded_positions.csv", source_excluded_rows)

    existing_sell_rows = _read_csv_rows(run_dir / "sell_decisions.csv")
    existing_sell_rows_by_lot = {
        str(row.get("strategy_lot_id", "")).strip(): row
        for row in existing_sell_rows
        if str(row.get("strategy_lot_id", "")).strip()
    }
    quote_rows = _load_latest_quote_rows_for_stock_ids(trade_date, {position.stock_id for position in positions})
    live_quote_states: dict[str, QuoteState] = {}
    live_quote_timestamps: dict[str, str] = {}
    if broker_checks_enabled and broker is not None:
        for position in positions:
            quote, _, _, quote_timestamp = broker.get_quote_state(position.stock_id)
            live_quote_states[position.stock_id] = quote
            live_quote_timestamps[position.stock_id] = quote_timestamp
            quote_rows[position.stock_id] = {
                "stock_id": position.stock_id,
                "timestamp": quote_timestamp,
                "last_price": quote.last_price,
                "bid1": quote.bid1 or "",
                "ask1": quote.ask1 or "",
            }

    decisions = []
    ambiguous_fill_rows: list[dict[str, object]] = []
    for position in positions:
        live_quote = live_quote_states.get(position.stock_id)
        quote = SellQuote(
            last_price=live_quote.last_price if live_quote else _as_float(quote_rows.get(position.stock_id, {}).get("last_price"), position.buy_avg_price),
            bid1=live_quote.bid1 if live_quote else (_as_float(quote_rows.get(position.stock_id, {}).get("bid1"), 0.0) or None),
            ask1=live_quote.ask1 if live_quote else (_as_float(quote_rows.get(position.stock_id, {}).get("ask1"), 0.0) or None),
        )
        decisions.append(
            evaluate_sell_decision(
                position,
                quote,
                fees=settings.fees,
                auto=settings.auto_trading,
                after_1320=after_1320,
            )
        )
    basket = basket_recommendation(decisions, positions, settings.auto_trading)
    basket_by_tag = basket_recommendations_by_tag(decisions, positions, settings.auto_trading)
    target_stock_ids = {position.stock_id for position in positions}
    pre_submit_fill_rows: list[dict[str, object]] = []
    ambiguous_fill_guard_stocks: set[str] = set()
    excluded_position_guard_rows: list[dict[str, object]] = []
    excluded_position_guard_stocks: set[str] = set()
    broker_underheld_guard_rows: list[dict[str, object]] = []
    broker_underheld_guard_stocks: set[str] = set()
    if broker_checks_enabled and broker is not None:
        pre_submit_fill_rows = _selected_fill_rows(
            broker=broker,
            trade_date=trade_date,
            target_stock_ids=target_stock_ids,
        )
        ambiguous_fill_guard_stocks = set(_ambiguous_fill_stock_ids(pre_submit_fill_rows))
        excluded_position_guard_rows = _excluded_positions_rows(
            broker_positions=broker.get_positions(),
            strategy_positions_rows=[
                {
                    "strategy_lot_id": position.strategy_lot_id,
                    "stock_id": position.stock_id,
                    "stock_name": position.stock_name,
                    "holding_qty": position.holding_qty,
                    "basket_tag": position.basket_tag,
                }
                for position in positions
            ],
        )
        excluded_position_guard_stocks = {
            str(row.get("stock_id", "")).strip()
            for row in excluded_position_guard_rows
            if str(row.get("stock_id", "")).strip()
        }
        broker_underheld_guard_rows = _broker_qty_below_strategy_guard_rows(
            broker_positions=broker.get_positions(),
            opening_positions=positions,
        )
        broker_underheld_guard_stocks = {
            str(row.get("stock_id", "")).strip()
            for row in broker_underheld_guard_rows
            if str(row.get("stock_id", "")).strip()
        }
    store.write_rows_csv("broker_position_mismatches.csv", broker_underheld_guard_rows)
    sell_rows_to_write: list[dict[str, object]] = []
    ambiguous_fill_guard_lot_ids: list[str] = []
    excluded_position_guard_lot_ids: list[str] = []
    broker_underheld_guard_lot_ids: list[str] = []
    for position, decision in zip(positions, decisions):
        position_basket = basket
        effective_sell_decision, effective_sell_reason = effective_basket_sell_signal(decision, position_basket)
        existing_sell_row = existing_sell_rows_by_lot.get(position.strategy_lot_id, {})
        broker_custom_field = str(existing_sell_row.get("broker_custom_field", "")).strip() or _broker_custom_field(position.strategy_lot_id, "SL")
        quote_timestamp = str(
            live_quote_timestamps.get(position.stock_id)
            or quote_rows.get(position.stock_id, {}).get("timestamp", "")
        ).strip()
        quote_is_fresh = not quote_is_stale(
            parse_quote_timestamp(quote_timestamp),
            now=datetime.now(TAIPEI),
            stale_seconds=settings.auto_trading.quote_stale_seconds,
        )
        gate = live_sell_submission_gate(
            decision,
            position_basket,
            auto=settings.auto_trading,
            live_sell_window=True if prepare_only else live_sell_window,
            quote_is_fresh=quote_is_fresh,
        )
        existing_sell_state = (
            _existing_sell_order_state(
                existing_row=existing_sell_row,
                broker=broker,
                default_remaining_qty=position.holding_qty,
            )
            if broker_checks_enabled and broker is not None
            else None
        )
        sell_submission_gate = existing_sell_state.gate_reason if existing_sell_state else gate.reason
        if existing_sell_state:
            sell_order_status = existing_sell_state.status
        elif prepare_only:
            sell_order_status = gate.status
        elif can_go_live:
            sell_order_status = gate.status
        else:
            sell_order_status = "not_submitted"
        if (
            broker_checks_enabled
            and broker is not None
            and not existing_sell_state
            and position.stock_id in ambiguous_fill_guard_stocks
            and sell_order_status == "ready_to_submit"
        ):
            sell_submission_gate = "ambiguous_fill_guard"
            sell_order_status = "blocked_ambiguous_fill"
            ambiguous_fill_guard_lot_ids.append(position.strategy_lot_id)
        elif (
            broker_checks_enabled
            and broker is not None
            and not existing_sell_state
            and position.stock_id in excluded_position_guard_stocks
            and sell_order_status == "ready_to_submit"
        ):
            sell_submission_gate = "excluded_position_guard"
            sell_order_status = "blocked_excluded_position_scope"
            excluded_position_guard_lot_ids.append(position.strategy_lot_id)
        elif (
            broker_checks_enabled
            and broker is not None
            and not existing_sell_state
            and position.stock_id in broker_underheld_guard_stocks
            and sell_order_status == "ready_to_submit"
        ):
            sell_submission_gate = "broker_qty_below_strategy_guard"
            sell_order_status = "blocked_broker_qty_mismatch"
            broker_underheld_guard_lot_ids.append(position.strategy_lot_id)
        sell_rows_to_write.append(
            {
                "strategy_lot_id": position.strategy_lot_id,
                "stock_id": decision.stock_id,
                "stock_name": decision.stock_name,
                "basket_tag": position.basket_tag,
                "holding_qty": position.holding_qty,
                "buy_avg_price": position.buy_avg_price,
                "buy_total_cost": position.buy_total_cost,
                "source": position.source,
                "last_price": _as_float(quote_rows.get(position.stock_id, {}).get("last_price"), position.buy_avg_price),
                "bid1": _as_float(quote_rows.get(position.stock_id, {}).get("bid1")),
                "ask1": _as_float(quote_rows.get(position.stock_id, {}).get("ask1")),
                "can_sell_flag": decision.can_sell_flag,
                "sell_decision": effective_sell_decision,
                "sell_decision_reason": effective_sell_reason,
                "conservative_sell_price": decision.conservative_sell_price,
                "conservative_profit": decision.conservative_profit,
                "estimated_sell_net_proceeds": decision.estimated_sell_net_proceeds,
                "basket_recommendation": position_basket.recommendation,
                "basket_threshold": position_basket.threshold,
                "basket_loser_loss_ratio": position_basket.loser_loss_ratio,
                "quote_timestamp": quote_timestamp,
                "sell_submission_gate": sell_submission_gate,
                "sell_order_price": existing_sell_state.order_price if existing_sell_state else "",
                "sell_order_id": existing_sell_state.order_id if existing_sell_state else "",
                "broker_custom_field": broker_custom_field,
                "sell_order_status": sell_order_status,
                "sold_qty": existing_sell_state.filled_qty if existing_sell_state else 0,
                "remaining_qty": existing_sell_state.remaining_qty if existing_sell_state else position.holding_qty,
                "allocated_buy_cost": "",
                "realized_pnl": "",
                "sell_pnl_source": "",
            }
        )
    store.write_rows_csv(
        "sell_decisions.csv",
        sell_rows_to_write,
    )
    if ambiguous_fill_guard_lot_ids:
        store.append_event(
            run_id=_run_id(trade_date),
            timestamp=datetime.now(TAIPEI).isoformat(),
            level="WARNING",
            event_type="ambiguous_fill_guard",
            message=f"Blocked new live sell submissions for {len(ambiguous_fill_guard_lot_ids)} strategy lots because ambiguous fills require manual reconciliation first.",
            metadata={
                "strategy_lot_ids": ambiguous_fill_guard_lot_ids,
                "stocks": sorted(ambiguous_fill_guard_stocks),
            },
        )
    if excluded_position_guard_lot_ids:
        store.append_event(
            run_id=_run_id(trade_date),
            timestamp=datetime.now(TAIPEI).isoformat(),
            level="WARNING",
            event_type="excluded_position_guard",
            message=f"Blocked new live sell submissions for {len(excluded_position_guard_lot_ids)} strategy lots because broker holdings included excluded non-strategy scope for the same stock.",
            metadata={
                "strategy_lot_ids": excluded_position_guard_lot_ids,
                "stocks": sorted(excluded_position_guard_stocks),
                "rows": excluded_position_guard_rows,
            },
        )
    if broker_underheld_guard_lot_ids:
        store.append_event(
            run_id=_run_id(trade_date),
            timestamp=datetime.now(TAIPEI).isoformat(),
            level="WARNING",
            event_type="broker_qty_below_strategy_guard",
            message=f"Blocked new live sell submissions for {len(broker_underheld_guard_lot_ids)} strategy lots because broker holdings were lower than strategy positions.",
            metadata={
                "strategy_lot_ids": broker_underheld_guard_lot_ids,
                "stocks": sorted(broker_underheld_guard_stocks),
                "rows": broker_underheld_guard_rows,
            },
        )
    if can_go_live and broker is not None and live_sell_window:
        sell_rows = _read_csv_rows(run_dir / "sell_decisions.csv")
        updated_rows: list[dict[str, object]] = []
        for row in sell_rows:
            row_copy: dict[str, object] = dict(row)
            stock_id = str(row.get("stock_id", ""))
            qty = _as_int(row.get("holding_qty"))
            if str(row.get("sell_decision", "")) == "sell" and qty > 0 and str(row.get("sell_order_status", "")) == "ready_to_submit":
                order_lot = _sell_order_lot_for_qty(qty)
                if not broker.supports_order_lot(order_lot):
                    row_copy["sell_order_status"] = "blocked_unsupported_order_lot"
                    row_copy["sell_submission_gate"] = "unsupported_order_lot"
                    row_copy["sell_decision_reason"] = f"{row.get('sell_decision_reason', '')}; unsupported order lot"
                else:
                    result = broker.place_sell_order(
                        stock_id,
                        _as_float(row.get("conservative_sell_price")),
                        qty,
                        order_lot,
                        {
                            "note": "sell_loop_live",
                            "stock_name": row.get("stock_name", stock_id),
                            "custom_prefix": "SL",
                            "strategy_lot_id": row.get("strategy_lot_id", _strategy_lot_id(trade_date, stock_id)),
                            "basket_tag": row.get("basket_tag", DEFAULT_BASKET_TAG),
                            "broker_custom_field": row.get("broker_custom_field", ""),
                        },
                    )
                    live_actions += 1
                    row_copy["sell_order_price"] = result.order_price
                    row_copy["sell_order_id"] = result.order_id
                    row_copy["sell_order_status"] = result.status
                    row_copy["sell_submission_gate"] = "submitted_live"
                    store.append_event(
                        run_id=_run_id(trade_date),
                        timestamp=datetime.now(TAIPEI).isoformat(),
                        level="INFO",
                        event_type="sell_loop_stock",
                        stock_id=stock_id,
                        message=f"sell_loop submitted {order_lot} sell order for {stock_id}.",
                        metadata={
                            "action": "sell_place",
                            "status": result.status,
                            "price": result.order_price,
                            "qty": qty,
                            "order_id": result.order_id,
                            "order_lot": order_lot,
                            "live": True,
                        },
                    )
            updated_rows.append(row_copy)
        store.write_rows_csv("sell_decisions.csv", updated_rows)
    if broker_checks_enabled and broker is not None:
        fills_rows = _selected_fill_rows(
            broker=broker,
            trade_date=trade_date,
            target_stock_ids=target_stock_ids,
        )
        store.write_rows_csv("fills.csv", fills_rows)
        ambiguous_fill_rows = [row for row in fills_rows if row.get("fill_assignment_status") == "ambiguous_unmapped_fill"]
        if ambiguous_fill_rows:
            ambiguous_stocks = _ambiguous_fill_stock_ids(fills_rows)
            store.append_event(
                run_id=_run_id(trade_date),
                timestamp=datetime.now(TAIPEI).isoformat(),
                level="WARNING",
                event_type="ambiguous_fill_mapping",
                message=f"Skipped {len(ambiguous_fill_rows)} live fills because no safe strategy lot mapping was available.",
                metadata={"count": len(ambiguous_fill_rows), "stocks": ambiguous_stocks},
            )
        sell_fill_stats = _sell_fill_stats_by_stock(
            fills_rows=fills_rows,
            positions=positions,
            fees=settings.fees,
        )
        sell_rows = _read_csv_rows(run_dir / "sell_decisions.csv")
        updated_rows: list[dict[str, object]] = []
        for row in sell_rows:
            row_copy: dict[str, object] = dict(row)
            stock_id = str(row.get("stock_id", ""))
            strategy_lot_id = str(row.get("strategy_lot_id", "")).strip()
            stats = sell_fill_stats.get(strategy_lot_id)
            if stats:
                row_copy["actual_fill_avg_price"] = stats["fill_avg_price"]
                row_copy["sold_qty"] = stats["sold_qty"]
                row_copy["remaining_qty"] = stats["remaining_qty"]
                row_copy["allocated_buy_cost"] = stats["allocated_buy_cost"]
                row_copy["realized_pnl"] = stats["realized_pnl"]
                row_copy["sell_pnl_source"] = "live_fill_reconciled"
                if _as_int(stats["sold_qty"], 0) > 0:
                    if _as_int(stats["remaining_qty"], 0) <= 0:
                        row_copy["sell_order_status"] = "filled"
                    elif row_copy.get("sell_order_status", "") in {
                        "evaluated",
                        "not_submitted",
                        "waiting_1300",
                        "active",
                        "Submitted",
                        "PreSubmitted",
                        "existing_order_unverified",
                        "",
                    }:
                        row_copy["sell_order_status"] = "filled_or_partially_filled"
            updated_rows.append(row_copy)
        updated_rows, local_sell_pnl_fallback_lots = _apply_local_sell_pnl_fallback(
            sell_rows=updated_rows,
            opening_positions=positions,
            fees=settings.fees,
        )
        if local_sell_pnl_fallback_lots:
            store.append_event(
                run_id=_run_id(trade_date),
                timestamp=datetime.now(TAIPEI).isoformat(),
                level="WARNING",
                event_type="local_sell_pnl_fallback",
                message=f"Used local sell PnL fallback for {len(local_sell_pnl_fallback_lots)} strategy lots because live fill details were incomplete.",
                metadata={"strategy_lot_ids": local_sell_pnl_fallback_lots},
            )
        store.write_rows_csv("sell_decisions.csv", updated_rows)
        quote_rows_by_stock: dict[str, QuoteState] = {}
        for stock_id in target_stock_ids:
            live_quote = live_quote_states.get(stock_id)
            if live_quote:
                quote_rows_by_stock[stock_id] = live_quote
        positions_rows = _positions_rows_from_fills(
            trade_date=trade_date,
            fills_rows=fills_rows,
            selection_meta_by_stock={
                position.stock_id: {"stock_name": position.stock_name, "source": position.source, "basket_tag": position.basket_tag}
                for position in positions
            },
            selection_meta_by_strategy_lot={
                position.strategy_lot_id: {
                    "stock_name": position.stock_name,
                    "source": position.source,
                    "basket_tag": position.basket_tag,
                }
                for position in positions
            },
            quote_rows_by_stock=quote_rows_by_stock,
            opening_positions=positions,
        )
        positions_rows, local_sell_fallback_lots = _apply_local_sell_state_to_positions_rows(
            trade_date=trade_date,
            positions_rows=positions_rows,
            opening_positions=positions,
            sell_rows=updated_rows,
            quote_rows_by_stock=quote_rows_by_stock,
        )
        if local_sell_fallback_lots:
            store.append_event(
                run_id=_run_id(trade_date),
                timestamp=datetime.now(TAIPEI).isoformat(),
                level="WARNING",
                event_type="local_sell_fill_fallback",
                message=f"Used local sell state fallback for {len(local_sell_fallback_lots)} strategy lots because live fills were incomplete.",
                metadata={"strategy_lot_ids": local_sell_fallback_lots},
            )
        store.write_rows_csv("positions.csv", positions_rows)
        broker_positions = broker.get_positions()
        store.write_rows_csv(
            "excluded_positions.csv",
            _excluded_positions_rows(
                broker_positions=broker_positions,
                strategy_positions_rows=positions_rows,
            ),
        )
        _append_pnl_snapshot(
            store=store,
            trade_date=trade_date,
            positions_rows=positions_rows,
        )
    _update_run_state(
        store,
        trade_date,
        status="sell_loop_evaluated",
        sell_loop={
            "sell_candidates": len(decisions),
            "basket_recommendation": basket.recommendation,
            "basket_threshold": basket.threshold,
            "loser_loss_ratio": basket.loser_loss_ratio,
            "basket_summaries": {
                basket_tag: {
                    "recommendation": grouped.recommendation,
                    "threshold": grouped.threshold,
                    "loser_loss_ratio": grouped.loser_loss_ratio,
                    "basket_conservative_profit": grouped.basket_conservative_profit,
                }
                for basket_tag, grouped in basket_by_tag.items()
            },
            "positions_source_date": source_date.isoformat() if source_date else "",
            "live_guard": live_guard,
            "live_actions": live_actions,
            "prepare_only": prepare_only,
            "read_only_live": read_only_live,
            "live_sell_window": live_sell_window,
            "ambiguous_fill_guard_count": len(ambiguous_fill_guard_lot_ids),
            "excluded_position_guard_count": len(excluded_position_guard_lot_ids),
            "broker_underheld_guard_count": len(broker_underheld_guard_lot_ids),
            "broker_underheld_guard_stocks": sorted(broker_underheld_guard_stocks),
            "ambiguous_fill_count": len(ambiguous_fill_rows) if broker_checks_enabled and broker is not None else 0,
        },
    )
    store.append_event(
        run_id=_run_id(trade_date),
        timestamp=datetime.now(TAIPEI).isoformat(),
        level="INFO",
        event_type="sell_loop",
        message="Evaluated conservative sell policy using strategy positions.",
        metadata={
            "basket_recommendation": basket.recommendation,
            "basket_recommendation_by_tag": {basket_tag: grouped.recommendation for basket_tag, grouped in basket_by_tag.items()},
            "positions_source_date": source_date.isoformat() if source_date else "",
            "position_count": len(positions),
            "live_guard": live_guard,
            "live_actions": live_actions,
            "prepare_only": prepare_only,
            "read_only_live": read_only_live,
        },
    )
    print(f"sell_candidates: {len(decisions)}")
    print(f"basket_recommendation: {basket.recommendation}")
    print(f"live_guard: {live_guard}")
    print(f"live_actions: {live_actions}")
    return 0


def command_reconcile_broker_state(args: argparse.Namespace) -> int:
    settings = Settings.load()
    trade_date = _parse_trade_date(args.trade_date)
    if not args.live:
        raise RuntimeError("Broker reconciliation is read-only, but --live is required to query the real broker account.")

    broker = ShioajiSinoPacBrokerAdapter(settings, simulation=False)
    summary = broker.get_account_summary()
    if not summary.signed:
        raise RuntimeError("Broker reconciliation blocked because broker account is not signed.")

    explicit_stock_ids = list(args.stock_id or [])
    target_stock_ids = _reconcile_target_stock_ids(trade_date, explicit_stock_ids)
    result = _reconcile_broker_state(
        settings=settings,
        trade_date=trade_date,
        broker=broker,
        target_stock_ids=target_stock_ids,
    )
    _best_effort_obsidian_sync(
        settings,
        trade_date,
        include_live_status=True,
        event_summary=(
            "broker_reconcile completed: "
            f"fills={result.fills_count} positions={result.positions_count} "
            f"ambiguous={result.ambiguous_fill_count}"
        ),
    )
    print(f"trade_date: {result.trade_date}")
    print(f"target_stock_ids: {', '.join(result.target_stock_ids)}")
    print(f"fills_count: {result.fills_count}")
    print(f"positions_count: {result.positions_count}")
    print(f"excluded_positions_count: {result.excluded_positions_count}")
    print(f"ambiguous_fill_count: {result.ambiguous_fill_count}")
    print(f"pnl_snapshot_total_pnl: {result.pnl_snapshot.get('total_pnl_after_fee_tax', 0)}")
    print(f"run_dir: {auto_trading_dir_for(trade_date)}")
    return 0


def command_repair_confirmation(args: argparse.Namespace) -> int:
    settings = Settings.load()
    trade_date = _parse_trade_date(args.trade_date)
    plan = resolve_week_trade_plan(trade_date)
    buy_source_trade_date = _buy_loop_source_trade_date(settings, trade_date, plan)
    run_dir = auto_trading_dir_for(trade_date)
    source_run_dir = auto_trading_dir_for(buy_source_trade_date)
    source_state = SQLiteStateStore(source_run_dir).read_state_json()
    confirmation_status = _a_preselect_sizing_confirmation_status(
        settings,
        buy_source_trade_date,
        source_state,
    )
    if confirmation_status.get("required") and not confirmation_status.get("ready"):
        raise RuntimeError(
            "Repair confirmation blocked because the source sizing was not finalized "
            f"after {confirmation_status.get('start_at')}. {A_PRESELECT_CONFIRMATION_REMINDER}"
        )
    sizing_path = source_run_dir / "sizing.csv"
    if not sizing_path.exists():
        raise RuntimeError(f"Missing sizing.csv for buy source date {buy_source_trade_date.isoformat()}. Run finalize first.")

    sizing_rows = _read_csv_rows(sizing_path)
    intended_rows: list[dict[str, object]] = []
    for row in sizing_rows:
        stock_id = str(row.get("stock_id", "")).strip()
        target_qty = _as_int(row.get("target_qty"), 0)
        if not stock_id or target_qty <= 0:
            continue
        basket_tag = normalize_basket_tag(row.get("basket_tag"))
        strategy_lot_id = str(row.get("strategy_lot_id", "")).strip() or _strategy_lot_id(
            buy_source_trade_date,
            stock_id,
            basket_tag,
        )
        intended_rows.append(
            {
                **row,
                "strategy_lot_id": strategy_lot_id,
                "basket_tag": basket_tag,
                "target_qty": target_qty,
                "broker_custom_field": str(row.get("broker_custom_field", "")).strip()
                or _broker_custom_field(strategy_lot_id, "BL"),
            }
        )
    if not intended_rows:
        raise RuntimeError("No positive target_qty rows found in sizing.csv.")

    order_rows = _buy_loop_existing_order_rows(plan, trade_date, buy_source_trade_date)
    fill_rows = _read_csv_rows(run_dir / "fills.csv")
    position_rows = _read_csv_rows(run_dir / "positions.csv")
    if source_run_dir != run_dir:
        fill_rows.extend(_read_csv_rows(source_run_dir / "fills.csv"))
        position_rows.extend(_read_csv_rows(source_run_dir / "positions.csv"))

    broker_order_rows: list[dict[str, object]] = []
    live_checked = False
    if args.live:
        broker = ShioajiSinoPacBrokerAdapter(settings, simulation=False)
        summary = broker.get_account_summary()
        if not summary.signed:
            raise RuntimeError("Repair confirmation blocked because broker account is not signed.")
        live_checked = True
        target_stock_ids = {
            str(row.get("stock_id", "")).strip()
            for row in intended_rows
            if str(row.get("stock_id", "")).strip()
        }
        fill_rows = _selected_fill_rows(
            broker=broker,
            trade_date=trade_date,
            target_stock_ids=target_stock_ids,
        )
        for row in intended_rows:
            stock_id = str(row.get("stock_id", "")).strip()
            strategy_lot_id = str(row.get("strategy_lot_id", "")).strip()
            broker_custom_field = str(row.get("broker_custom_field", "")).strip()
            snapshot = broker.get_managed_order_by_custom_field(
                broker_custom_field,
                side="buy",
                stock_id=stock_id,
            )
            if snapshot is None:
                continue
            broker_order_rows.append(
                {
                    "strategy_lot_id": strategy_lot_id,
                    "stock_id": stock_id,
                    "stock_name": row.get("stock_name", stock_id),
                    "status": snapshot.status,
                    "order_id": snapshot.order_id,
                    "order_price": snapshot.order_price,
                    "order_qty": snapshot.order_qty,
                    "active_order_qty": snapshot.remaining_qty if snapshot.status == "active" else 0,
                    "filled_qty": snapshot.filled_qty,
                    "remaining_qty": snapshot.remaining_qty,
                    "broker_custom_field": broker_custom_field,
                }
            )

    confirmation_rows = build_repair_confirmation_rows(
        intended_rows=intended_rows,
        order_rows=order_rows,
        fill_rows=fill_rows,
        position_rows=position_rows,
        broker_order_rows=broker_order_rows,
    )
    summary = summarize_repair_confirmation(confirmation_rows)
    generated_at = repair_confirmation_now_iso(TAIPEI)
    email_to = str(args.email_to or "ops@example.com").strip()
    report_payload = {
        "trade_date": trade_date.isoformat(),
        "buy_source_trade_date": buy_source_trade_date.isoformat(),
        "generated_at": generated_at,
        "email_to": email_to,
        "policy_scope": "all_future_live_basket_buy_repairs",
        "live_checked": live_checked,
        "reply_required": bool(summary.get("approval_required")),
        "reply_allows_submit": bool(summary.get("approval_allowed")),
        "reply_instruction": "Reply to the confirmation email before submitting any not-yet-submitted remainder.",
        "summary": summary,
        "rows": confirmation_rows,
    }
    report_json_path = run_dir / "repair_confirmation_report.json"
    report_md_path = run_dir / "repair_confirmation_report.md"
    rows_csv_path = run_dir / "repair_confirmation_rows.csv"
    run_dir.mkdir(parents=True, exist_ok=True)
    report_json_path.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    report_md_path.write_text(
        render_repair_confirmation_markdown(
            trade_date=trade_date.isoformat(),
            buy_source_trade_date=buy_source_trade_date.isoformat(),
            generated_at=generated_at,
            email_to=email_to,
            rows=confirmation_rows,
            summary=summary,
        ),
        encoding="utf-8",
    )
    store = SQLiteStateStore(run_dir)
    store.initialize()
    store.write_rows_csv("repair_confirmation_rows.csv", confirmation_rows)
    store.merge_state_json(
        {
            "repair_confirmation": {
                "generated_at": generated_at,
                "email_to": email_to,
                "live_checked": live_checked,
                "report_json": str(report_json_path),
                "report_markdown": str(report_md_path),
                "rows_csv": str(rows_csv_path),
                "reply_required": bool(summary.get("approval_required")),
                "reply_allows_submit": bool(summary.get("approval_allowed")),
                "summary": summary,
            }
        }
    )
    store.append_event(
        run_id=_run_id(trade_date),
        timestamp=generated_at,
        level="INFO",
        event_type="repair_confirmation",
        message=(
            "Built repair confirmation report: "
            f"to_submit_qty={summary.get('to_submit_qty')} "
            f"live_checked={str(live_checked).lower()}."
        ),
        metadata=report_payload,
    )

    print(f"trade_date: {trade_date.isoformat()}")
    print(f"buy_source_trade_date: {buy_source_trade_date.isoformat()}")
    print(f"live_checked: {str(live_checked).lower()}")
    print(f"email_to: {email_to}")
    print(f"intended_count: {summary.get('intended_count')}")
    print(f"bought_count: {summary.get('bought_count')}")
    print(f"active_count: {summary.get('active_count')}")
    print(f"to_submit_count: {summary.get('to_submit_count')}")
    print(f"to_submit_qty: {summary.get('to_submit_qty')}")
    print(f"ambiguous_count: {summary.get('ambiguous_count')}")
    print(f"reply_required: {str(bool(summary.get('approval_required'))).lower()}")
    print(f"reply_allows_submit: {str(bool(summary.get('approval_allowed'))).lower()}")
    print(f"report_json: {report_json_path}")
    print(f"report_markdown: {report_md_path}")
    print(f"rows_csv: {rows_csv_path}")
    return 0


def command_render_report(args: argparse.Namespace) -> int:
    settings = Settings.load()
    trade_date = _parse_trade_date(args.trade_date)
    plan = resolve_week_trade_plan(trade_date)
    report = _build_daily_report(settings, trade_date)
    normalized_post_guarded_check = _normalize_post_guarded_check_for_display(
        report.get("post_guarded_order_check") if isinstance(report, dict) else {}
    )
    if isinstance(report, dict):
        report = {
            **report,
            "post_guarded_order_check": normalized_post_guarded_check,
        }
    run_dir = auto_trading_dir_for(trade_date)
    store = SQLiteStateStore(run_dir)
    store.initialize()
    existing_state = store.read_state_json()
    preserved_status = str(existing_state.get("status", "")).strip() or "report_rendered"
    resolved_provider_name = _resolved_provider_name(state_data=existing_state, settings=settings)
    last_materializing_refresh = _resolve_last_materializing_refresh_payload(trade_date, existing_state)
    selection_rows = list(report.get("selection_rows", [])) if isinstance(report.get("selection_rows", []), list) else []
    preselect_count, final_list_count = _selection_snapshot_counts(selection_rows)
    render_daily_report(
        report,
        daily_note_path(trade_date),
        daily_html_report_path(trade_date),
        snapshot_json_path=dated_snapshot_json_path(trade_date),
        current_html_path=current_html_report_path(),
        current_snapshot_path=current_snapshot_json_path(),
        legacy_html_path=dated_html_report_path(trade_date),
    )
    _update_run_state(
        store,
        trade_date,
        status=preserved_status,
        provider_name=resolved_provider_name,
        buy_cutoff_day=plan.buy_cutoff_day.isoformat() if plan.buy_cutoff_day else "",
        last_trade_day=plan.last_trade_day.isoformat() if plan.last_trade_day else "",
        report_outputs={
            "daily_note": str(daily_note_path(trade_date)),
            "daily_html": str(daily_html_report_path(trade_date)),
            "current_html": str(current_html_report_path()),
            "snapshot_json": str(dated_snapshot_json_path(trade_date)),
        },
        dashboard_refresh_last_materializing=last_materializing_refresh,
        preselect_count=preselect_count,
        final_list_count=final_list_count,
        post_guarded_order_check=normalized_post_guarded_check,
        sell_loop_readiness=report.get("sell_loop_readiness", {})
        if isinstance(report.get("sell_loop_readiness", {}), dict)
        else {},
        today_status=str(report.get("overview", {}).get("today_status", "")).strip(),
        today_status_note=str(report.get("overview", {}).get("today_status_note", "")).strip(),
        selection_source_path=str(report.get("overview", {}).get("selection_source_path", "")).strip(),
        selection_source_last_modified=str(report.get("overview", {}).get("selection_source_last_modified", "")).strip(),
        selection_source_status=str(report.get("overview", {}).get("selection_source_status", "")).strip(),
        selection_source_note=str(report.get("overview", {}).get("selection_source_note", "")).strip(),
        today_ordering_status=str(report.get("overview", {}).get("today_ordering_status", "")).strip(),
        today_ordering_note=str(report.get("overview", {}).get("today_ordering_note", "")).strip(),
        today_ordering_conflict_status=str(report.get("overview", {}).get("today_ordering_conflict_status", "")).strip(),
        today_ordering_conflict_note=str(report.get("overview", {}).get("today_ordering_conflict_note", "")).strip(),
        today_ordering_conflict_resolution_status=str(report.get("overview", {}).get("today_ordering_conflict_resolution_status", "")).strip(),
        today_ordering_conflict_resolution_action=str(report.get("overview", {}).get("today_ordering_conflict_resolution_action", "")).strip(),
        today_ordering_conflict_resolution_note=str(report.get("overview", {}).get("today_ordering_conflict_resolution_note", "")).strip(),
        today_new_order_submission_open=bool(report.get("overview", {}).get("today_new_order_submission_open", False)),
        today_new_order_submission_status=str(report.get("overview", {}).get("today_new_order_submission_status", "")).strip(),
        today_new_order_submission_note=str(report.get("overview", {}).get("today_new_order_submission_note", "")).strip(),
        selection_source_carry_forward_open=bool(report.get("overview", {}).get("selection_source_carry_forward_open", False)),
        selection_source_carry_forward_status=str(report.get("overview", {}).get("selection_source_carry_forward_status", "")).strip(),
        selection_source_carry_forward_next_trade_day=str(report.get("overview", {}).get("selection_source_carry_forward_next_trade_day", "")).strip(),
        selection_source_carry_forward_note=str(report.get("overview", {}).get("selection_source_carry_forward_note", "")).strip(),
        dashboard_refresh_status=str(report.get("overview", {}).get("dashboard_refresh_status", "")).strip(),
        dashboard_refresh_steps=str(report.get("overview", {}).get("dashboard_refresh_steps", "")).strip(),
        dashboard_refresh_note=str(report.get("overview", {}).get("dashboard_refresh_note", "")).strip(),
        dashboard_refresh_trigger_status=str(report.get("overview", {}).get("dashboard_refresh_trigger_status", "")).strip(),
        dashboard_refresh_trigger_artifacts=str(report.get("overview", {}).get("dashboard_refresh_trigger_artifacts", "")).strip(),
        dashboard_refresh_trigger_note=str(report.get("overview", {}).get("dashboard_refresh_trigger_note", "")).strip(),
        dashboard_last_materialization_status=str(report.get("overview", {}).get("dashboard_last_materialization_status", "")).strip(),
        dashboard_last_materialization_steps=str(report.get("overview", {}).get("dashboard_last_materialization_steps", "")).strip(),
        dashboard_last_materialization_note=str(report.get("overview", {}).get("dashboard_last_materialization_note", "")).strip(),
        dashboard_last_materialization_trigger_status=str(report.get("overview", {}).get("dashboard_last_materialization_trigger_status", "")).strip(),
        dashboard_last_materialization_trigger_artifacts=str(report.get("overview", {}).get("dashboard_last_materialization_trigger_artifacts", "")).strip(),
        dashboard_last_materialization_trigger_note=str(report.get("overview", {}).get("dashboard_last_materialization_trigger_note", "")).strip(),
        weekly_settlement_open=bool(report.get("overview", {}).get("weekly_settlement_open", False)),
        weekly_settlement_status=str(report.get("overview", {}).get("weekly_settlement_status", "")).strip(),
        weekly_settlement_artifacts=str(report.get("overview", {}).get("weekly_settlement_artifacts", "")).strip(),
        weekly_settlement_note=str(report.get("overview", {}).get("weekly_settlement_note", "")).strip(),
        weekly_settlement_next_action=str(report.get("overview", {}).get("weekly_settlement_next_action", "")).strip(),
        weekly_settlement_next_action_note=str(report.get("overview", {}).get("weekly_settlement_next_action_note", "")).strip(),
        selection_materialization_open=bool(report.get("overview", {}).get("selection_materialization_open", False)),
        selection_materialization_status=str(report.get("overview", {}).get("selection_materialization_status", "")).strip(),
        selection_materialization_missing_artifacts=str(report.get("overview", {}).get("selection_materialization_missing_artifacts", "")).strip(),
        selection_materialization_note=str(report.get("overview", {}).get("selection_materialization_note", "")).strip(),
        selection_materialization_next_action=str(report.get("overview", {}).get("selection_materialization_next_action", "")).strip(),
        selection_materialization_next_action_note=str(report.get("overview", {}).get("selection_materialization_next_action_note", "")).strip(),
    )
    _best_effort_obsidian_sync(settings, trade_date, event_summary="render_report generated")
    print(f"markdown_report: {daily_note_path(trade_date)}")
    print(f"html_report: {daily_html_report_path(trade_date)}")
    print(f"current_dashboard: {current_html_report_path()}")
    print(f"snapshot_json: {dated_snapshot_json_path(trade_date)}")
    return 0


def command_settle_week(args: argparse.Namespace) -> int:
    settings = Settings.load()
    trade_date = _parse_trade_date(args.trade_date)
    plan = resolve_week_trade_plan(trade_date)
    first_day = plan.week_trade_days[0] if plan.week_trade_days else trade_date
    last_day = plan.week_trade_days[-1] if plan.week_trade_days else trade_date
    summary = _build_weekly_summary(settings, trade_date)
    path = weekly_note_path(first_day, last_day)
    run_dir = auto_trading_dir_for(trade_date)
    store = SQLiteStateStore(run_dir)
    store.initialize()
    lot_ledger_rows = list(summary.get("lot_ledger_rows", []))
    if lot_ledger_rows:
        store.write_rows_csv("week_lot_ledger.csv", lot_ledger_rows)
    weekly_outputs = {
        "weekly_note": str(path),
        "weekly_html": str(weekly_html_report_path(first_day, last_day)),
        "weekly_snapshot_json": str(weekly_snapshot_json_path(first_day, last_day)),
        "week_lot_ledger": str(run_dir / "week_lot_ledger.csv") if lot_ledger_rows else "",
    }
    render_weekly_settlement(
        summary,
        path,
        html_path=weekly_html_report_path(first_day, last_day),
        snapshot_json_path=weekly_snapshot_json_path(first_day, last_day),
    )
    weekly_settlement = _weekly_settlement_summary(
        trade_date=trade_date,
        state_data={"weekly_outputs": weekly_outputs},
    )
    _update_run_state(
        store,
        trade_date,
        status="weekly_settled",
        weekly_outputs=weekly_outputs,
        **weekly_settlement,
    )
    _best_effort_obsidian_sync(settings, trade_date, event_summary="settle_week generated")
    print(f"weekly_note: {path}")
    print(f"weekly_html: {weekly_html_report_path(first_day, last_day)}")
    print(f"weekly_snapshot_json: {weekly_snapshot_json_path(first_day, last_day)}")
    return 0


def command_workflow_status(args: argparse.Namespace) -> int:
    settings = Settings.load()
    trade_date = _parse_trade_date(args.trade_date)
    plan = resolve_week_trade_plan(trade_date)
    run_dir = auto_trading_dir_for(trade_date)
    input_dir = input_dir_for(trade_date)
    store = SQLiteStateStore(run_dir)
    store.initialize()
    existing_state = store.read_state_json()
    resolved_provider_name = _resolved_provider_name(state_data=existing_state, settings=settings)
    last_materializing_refresh = _resolve_last_materializing_refresh_payload(trade_date, existing_state)

    rows = _workflow_status_rows(
        trade_date=trade_date,
        run_dir=run_dir,
        input_dir=input_dir,
        plan=plan,
        state_data={**existing_state, "provider_name": resolved_provider_name},
        settings=settings,
    )
    selection_rows = _load_selection_snapshot_rows(
        trade_date=trade_date,
        provider_name=resolved_provider_name,
    )
    preselect_count, final_list_count = _selection_snapshot_counts(selection_rows)
    for row in rows:
        if row["step"] == "workflow_status":
            row["status"] = "done"
    selection_source = _selection_source_summary(
        trade_date=trade_date,
        provider_name=resolved_provider_name,
        preselect_count=preselect_count,
        final_list_count=final_list_count,
        settings=settings,
    )
    dashboard_refresh = _dashboard_refresh_summary(existing_state)
    if not dashboard_refresh:
        dashboard_refresh = _refresh_dashboard_event_summary(trade_date)
    post_guarded_check = _normalize_post_guarded_check_for_display(
        _post_guarded_order_check_report_summary(trade_date)
    )
    sell_loop_readiness = _sell_loop_readiness_report_summary(trade_date)
    effective_buy_cutoff_day = _effective_buy_cutoff_day(settings, plan)
    today_ordering = _today_ordering_summary(
        guarded_effective_recommendation=_effective_guarded_post_recommendation(post_guarded_check),
        selection_source_status=selection_source.get("selection_source_status", ""),
        trade_date=trade_date,
        buy_cutoff_day=effective_buy_cutoff_day,
        last_trade_day=plan.last_trade_day,
    )
    today_ordering_conflict = _today_ordering_conflict_summary(
        selection_source_status=selection_source.get("selection_source_status", ""),
        trade_date=trade_date,
        buy_cutoff_day=effective_buy_cutoff_day,
        last_trade_day=plan.last_trade_day,
    )
    today_ordering_conflict_resolution = _today_ordering_conflict_resolution_summary(
        today_ordering_conflict_status=today_ordering_conflict.get("today_ordering_conflict_status", ""),
        trade_date=trade_date,
        next_trade_day=_next_trade_day_after(trade_date),
    )
    today_new_order_submission = _today_new_order_submission_summary(
        today_ordering_status=today_ordering.get("today_ordering_status", ""),
    )
    selection_source_carry_forward = _selection_source_carry_forward_summary(
        selection_source_status=selection_source.get("selection_source_status", ""),
        trade_date=trade_date,
    )
    selection_materialization = _selection_materialization_summary(
        trade_date=trade_date,
        selection_source_status=selection_source.get("selection_source_status", ""),
        buy_cutoff_day=effective_buy_cutoff_day,
        last_trade_day=plan.last_trade_day,
    )
    weekly_settlement = _weekly_settlement_summary(
        trade_date=trade_date,
        state_data=existing_state,
    )
    state_data = _update_run_state(
        store,
        trade_date,
        status="workflow_status_rendered",
        provider_name=resolved_provider_name,
        buy_cutoff_day=plan.buy_cutoff_day.isoformat() if plan.buy_cutoff_day else "",
        effective_buy_cutoff_day=effective_buy_cutoff_day.isoformat() if effective_buy_cutoff_day else "",
        buy_chase_after_first_trade_day_enabled=_allow_buy_after_first_trade_day(settings),
        last_trade_day=plan.last_trade_day.isoformat() if plan.last_trade_day else "",
        workflow_status={
            "completed_steps": len([row for row in rows if row["status"] == "done"]),
            "pending_steps": len([row for row in rows if row["status"] == "pending"]),
            "closed_steps": len([row for row in rows if row["status"] == "closed"]),
        },
        dashboard_refresh_last_materializing=last_materializing_refresh,
        preselect_count=preselect_count,
        final_list_count=final_list_count,
        **dashboard_refresh,
        **_dashboard_last_materializing_summary(
            {
                **existing_state,
                **dashboard_refresh,
                **({"dashboard_refresh_last_materializing": last_materializing_refresh} if last_materializing_refresh else {}),
            }
        ),
        **selection_source,
        **today_ordering,
        **today_ordering_conflict,
        **today_ordering_conflict_resolution,
        **today_new_order_submission,
        **selection_source_carry_forward,
        **selection_materialization,
        **weekly_settlement,
        post_guarded_order_check=post_guarded_check if isinstance(post_guarded_check, dict) else {},
        sell_loop_readiness=sell_loop_readiness if isinstance(sell_loop_readiness, dict) else {},
        guarded_post_check_status=str(post_guarded_check.get("after_status", "")).strip(),
        guarded_post_check_recommendation=str(post_guarded_check.get("recommendation", "")).strip(),
        guarded_post_check_effective_recommendation=_effective_guarded_post_recommendation(post_guarded_check),
        guarded_post_check_effective_recommendation_note=_describe_workflow_action(
            _effective_guarded_post_recommendation(post_guarded_check)
        ),
        guarded_post_check_reconciled=bool(post_guarded_check.get("reconciled", False)),
        guarded_post_check_fills_count=_as_int(post_guarded_check.get("fills_count"), 0),
        guarded_post_check_positions_count=_as_int(post_guarded_check.get("positions_count"), 0),
        guarded_post_check_next_run_guard_status=str(post_guarded_check.get("next_run_guard_status", "")).strip(),
        guarded_post_check_next_run_guard_message=str(post_guarded_check.get("next_run_guard_message", "")).strip(),
        guarded_post_check_config_timing_status=str(post_guarded_check.get("config_timing_status", "")).strip(),
        guarded_post_check_config_timing_message=str(post_guarded_check.get("config_timing_message", "")).strip(),
        guarded_post_check_config_path=str(post_guarded_check.get("config_path", "")).strip(),
        guarded_post_check_config_last_modified=str(post_guarded_check.get("config_last_modified", "")).strip(),
        guarded_post_check_task_recorded_at=str(post_guarded_check.get("task_recorded_at", "")).strip(),
        sell_loop_readiness_blocking_reason=str(sell_loop_readiness.get("blocking_reason", "")).strip(),
        sell_loop_readiness_next_action=str(sell_loop_readiness.get("next_action", "")).strip(),
        sell_loop_readiness_next_action_note=_describe_workflow_action(sell_loop_readiness.get("next_action", "")),
        sell_loop_readiness_positions_ready=bool(sell_loop_readiness.get("positions_ready", False)),
        sell_loop_readiness_positions_count=_as_int(sell_loop_readiness.get("positions_count"), 0),
        sell_loop_readiness_positions_source_date=str(sell_loop_readiness.get("positions_source_date", "")).strip(),
        sell_loop_readiness_post_guarded_effective_recommendation=str(
            sell_loop_readiness.get("post_guarded_effective_recommendation", "")
        ).strip(),
        sell_loop_readiness_post_guarded_effective_recommendation_note=_describe_workflow_action(
            sell_loop_readiness.get("post_guarded_effective_recommendation", "")
        ),
        sell_loop_readiness_post_guarded_next_run_guard_status=str(
            sell_loop_readiness.get("post_guarded_next_run_guard_status", "")
        ).strip(),
        sell_loop_readiness_post_guarded_config_timing_status=str(
            sell_loop_readiness.get("post_guarded_config_timing_status", "")
        ).strip(),
        sell_loop_readiness_post_guarded_config_timing_message=str(
            sell_loop_readiness.get("post_guarded_config_timing_message", "")
        ).strip(),
        sell_loop_readiness_post_guarded_config_path=str(
            sell_loop_readiness.get("post_guarded_config_path", "")
        ).strip(),
        sell_loop_readiness_post_guarded_config_last_modified=str(
            sell_loop_readiness.get("post_guarded_config_last_modified", "")
        ).strip(),
        sell_loop_readiness_post_guarded_task_recorded_at=str(
            sell_loop_readiness.get("post_guarded_task_recorded_at", "")
        ).strip(),
    )
    note_path = _workflow_status_note_path(trade_date)
    note_path.write_text(
        _workflow_status_markdown(
            trade_date=trade_date,
            state_data=state_data,
            rows=rows,
            selection_source=selection_source,
            post_guarded_check=post_guarded_check,
            sell_loop_readiness=sell_loop_readiness,
        ),
        encoding="utf-8",
    )
    store.append_event(
        run_id=_run_id(trade_date),
        timestamp=datetime.now(TAIPEI).isoformat(),
        level="INFO",
        event_type="workflow_status",
        message=f"Rendered workflow status with {len(rows)} checklist rows.",
        metadata={"note_path": str(note_path)},
    )
    print(f"workflow_note: {note_path}")
    print(f"completed_steps: {len([row for row in rows if row['status'] == 'done'])}")
    print(f"pending_steps: {len([row for row in rows if row['status'] == 'pending'])}")
    print(f"closed_steps: {len([row for row in rows if row['status'] == 'closed'])}")
    return 0


def command_refresh_dashboard(args: argparse.Namespace) -> int:
    settings = Settings.load()
    trade_date = _parse_trade_date(args.trade_date)
    plan = resolve_week_trade_plan(trade_date)
    run_dir = auto_trading_dir_for(trade_date)
    input_dir = input_dir_for(trade_date)
    run_dir.mkdir(parents=True, exist_ok=True)
    input_dir.mkdir(parents=True, exist_ok=True)

    steps_run: list[str] = []
    confirmation_status = _a_preselect_confirmation_time_status(settings, trade_date)
    materialization_allowed = not confirmation_status.get("required") or bool(confirmation_status.get("ready"))
    if confirmation_status.get("required"):
        print(f"note: {A_PRESELECT_CONFIRMATION_REMINDER}")
        if not materialization_allowed:
            print(f"a_preselect_confirmation_wait_until: {confirmation_status.get('start_at')}")
    refresh_details = _ab_same_day_source_refresh_details(
        trade_date=trade_date,
        settings=settings,
        run_dir=run_dir,
        input_dir=input_dir,
    )
    refresh_flags = {
        "prepare_week": bool(refresh_details.get("prepare_week", False)),
        "finalize": bool(refresh_details.get("finalize", False)),
    }

    if materialization_allowed and (
        args.prepare_week or refresh_flags.get("prepare_week", False) or not (run_dir / "preselect.csv").exists()
    ):
        prepare_args = argparse.Namespace(trade_date=trade_date.isoformat())
        command_prepare_week(prepare_args)
        steps_run.append("prepare_week")

    if materialization_allowed and (
        args.track_until_final or (args.auto_track and not (run_dir / "quote_snapshots.csv").exists())
    ):
        track_args = argparse.Namespace(trade_date=trade_date.isoformat())
        command_track_until_final(track_args)
        steps_run.append("track_until_final")

    if args.prepare_llm_selection:
        prepare_llm_args = argparse.Namespace(trade_date=trade_date.isoformat())
        command_prepare_llm_selection(prepare_llm_args)
        steps_run.append("prepare_llm_selection")

    if args.apply_llm_selection:
        apply_llm_args = argparse.Namespace(trade_date=trade_date.isoformat())
        command_apply_llm_selection(apply_llm_args)
        steps_run.append("apply_llm_selection")

    if materialization_allowed and (
        args.finalize or refresh_flags.get("finalize", False) or not (run_dir / "sizing.csv").exists()
    ):
        finalize_args = argparse.Namespace(trade_date=trade_date.isoformat(), max_names=args.max_names)
        command_finalize(finalize_args)
        steps_run.append("finalize")

    should_run_buy_loop = args.buy_loop
    if args.auto_buy_loop and plan.last_trade_day != trade_date and (run_dir / "sizing.csv").exists():
        should_run_buy_loop = True
    if should_run_buy_loop:
        buy_args = argparse.Namespace(
            trade_date=trade_date.isoformat(),
            live=args.live,
            confirm_live=args.confirm_live,
            reprice_threshold_ticks=args.reprice_threshold_ticks,
        )
        command_buy_loop(buy_args)
        steps_run.append("buy_loop")

    render_args = argparse.Namespace(trade_date=trade_date.isoformat())
    command_render_report(render_args)
    steps_run.append("render_report")

    if args.settle_week or (args.auto_settle_week and plan.last_trade_day == trade_date):
        settle_args = argparse.Namespace(trade_date=trade_date.isoformat())
        command_settle_week(settle_args)
        steps_run.append("settle_week")

    workflow_args = argparse.Namespace(trade_date=trade_date.isoformat())
    command_workflow_status(workflow_args)
    steps_run.append("workflow_status")

    store = SQLiteStateStore(run_dir)
    store.initialize()
    current_state = store.read_state_json()
    preserved_status = str(current_state.get("status", "")).strip() or "dashboard_refreshed"
    dashboard_refresh_payload = {
        "steps_run": steps_run,
        "live": bool(args.live),
        "confirm_live": bool(args.confirm_live),
        "source_refresh_trigger_status": str(refresh_details.get("trigger_status", "")).strip(),
        "source_refresh_trigger_artifacts": str(refresh_details.get("trigger_artifacts", "")).strip(),
        "source_refresh_trigger_note": str(refresh_details.get("trigger_note", "")).strip(),
    }
    dashboard_refresh_summary = _dashboard_refresh_summary({"dashboard_refresh": dashboard_refresh_payload})
    last_materializing_fields: dict[str, object] = {}
    if "prepare_week" in steps_run or "finalize" in steps_run:
        last_materializing_fields = {
            "dashboard_refresh_last_materializing": dashboard_refresh_payload,
            **_dashboard_last_materializing_summary(
                {"dashboard_refresh_last_materializing": dashboard_refresh_payload}
            ),
        }
    _update_run_state(
        store,
        trade_date,
        status=preserved_status,
        dashboard_refresh=dashboard_refresh_payload,
        **dashboard_refresh_summary,
        **last_materializing_fields,
    )
    store.append_event(
        run_id=_run_id(trade_date),
        timestamp=datetime.now(TAIPEI).isoformat(),
        level="INFO",
        event_type="refresh_dashboard",
        message=f"Dashboard refresh completed with {len(steps_run)} steps.",
        metadata=dashboard_refresh_payload,
    )
    print(f"refresh_steps: {', '.join(steps_run)}")
    return 0


def command_sync_obsidian(args: argparse.Namespace) -> int:
    settings = Settings.load()
    trade_date = _parse_trade_date(args.trade_date)
    written = sync_obsidian_snapshot(
        settings,
        trade_date,
        include_live_status=args.include_live_status,
        event_summary=args.event_summary,
    )
    for path in written:
        print(f"synced: {path}")
    return 0


def command_live_smoke_test(args: argparse.Namespace) -> int:
    settings = Settings.load()
    trade_date = _parse_trade_date(args.trade_date)
    if args.stock_id:
        items = [
            SelectionItem(
                stock_id=args.stock_id,
                stock_name=args.stock_name or args.stock_id,
                source=args.source,
            )
        ]
    else:
        provider = _provider_from_settings(settings)
        items = provider.load_final_list(trade_date) or provider.load_preselect(trade_date)
    quote_provider = _load_fake_quote_provider(settings)
    if args.live and args.confirm_live:
        broker = ShioajiSinoPacBrokerAdapter(settings, simulation=False)
    else:
        broker = FakeBrokerAdapter(cash_available=settings.auto_trading.test_max_total_buy_amount)
    result = run_live_smoke_test(
        items,
        broker=broker,
        quote_provider=quote_provider,
        settings=settings,
        live=args.live,
        confirm_live=args.confirm_live,
    )
    print(f"mode: {result.mode}")
    print(f"total_estimated_amount: {result.total_estimated_amount}")
    for order in result.orders:
        print(f"{order.stock_id} qty={order.qty} price={order.price} status={order.status} detail={order.detail}")
    return 0


def command_chase_stock_order(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    result = run_single_stock_chase(
        settings=settings,
        stock_id=args.stock_id,
        exchange=args.exchange,
        action="Buy",
        order_lot=args.order_lot,
        quantity=args.quantity,
        price_cap=args.price_cap,
        live=args.live,
        submit=args.submit,
        confirm_live=args.confirm_live,
        start_time=parse_hhmm(args.start_time),
        end_time=parse_hhmm(args.end_time),
        check_interval_seconds=args.check_interval_seconds,
        reprice_threshold_ticks=args.reprice_threshold_ticks,
        custom_prefix=args.custom_prefix,
    )
    print(f"mode: {'live' if args.live else 'simulation'}")
    print(f"stock_id: {result.stock_id}")
    print(f"stock_name: {result.stock_name}")
    print(f"quantity: {result.quantity}")
    print(f"order_lot: {result.order_lot}")
    print(f"price_cap: {result.price_cap}")
    print(f"submitted: {result.submitted}")
    print(f"final_state: {result.final_state}")
    print(f"final_order_id: {result.final_order_id}")
    print(f"final_order_price: {result.final_order_price}")
    print(f"summary_path: {result.summary_path}")
    if result.steps:
        last_step = result.steps[-1]
        print(
            f"last_step: timestamp={last_step.timestamp} action={last_step.action} "
            f"target_price={last_step.target_price} trade_state={last_step.trade_state} note={last_step.note}"
        )
    _best_effort_obsidian_sync(
        settings,
        _today(),
        include_live_status=args.live,
        event_summary=(
            f"chase_stock_order completed: stock_id={result.stock_id} "
            f"final_state={result.final_state} final_order_id={result.final_order_id}"
        ),
    )
    return 0


def command_login_check(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    simulation = not args.live
    api, accounts = login(settings, simulation=simulation, fetch_contract=not args.no_fetch_contract)
    print(f"mode: {'simulation' if simulation else 'live'}")
    for index, account in enumerate(accounts, start=1):
        print(f"account_{index}: {describe_account(account)}")
    if not simulation and settings.person_id:
        print(f"ca_expiretime: {api.get_ca_expiretime(settings.person_id)}")
    return 0


def command_api_test_stock(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    api, accounts = login(settings, simulation=True, fetch_contract=True)
    print("mode: simulation")
    for index, account in enumerate(accounts, start=1):
        print(f"account_{index}: {describe_account(account)}")
    contract = resolve_stock_contract(api, args.stock_id, exchange_hint=args.exchange)

    import shioaji as sj  # type: ignore

    price = args.price if args.price is not None else float(contract.reference)
    order_lot = getattr(sj.constant.StockOrderLot, args.order_lot)
    action = getattr(sj.constant.Action, args.action)
    order = api.Order(
        price=price,
        quantity=args.quantity,
        action=action,
        price_type=sj.constant.StockPriceType.LMT,
        order_type=sj.constant.OrderType.ROD,
        order_lot=order_lot,
        custom_field="APITST",
        account=api.stock_account,
    )
    trade = api.place_order(contract, order)
    trade_status = str(getattr(getattr(trade, "status", None), "status", ""))
    print(
        f"placed_trade: stock_id={args.stock_id} action={args.action} "
        f"quantity={args.quantity} order_lot={args.order_lot} price={price}"
    )
    print(
        "trade_status: "
        f"status={trade_status} "
        f"status_code={getattr(getattr(trade, 'status', None), 'status_code', '')} "
        f"order_id={getattr(getattr(trade, 'order', None), 'id', '')}"
    )
    if "Failed" in trade_status:
        print("cancelled_trade: skipped_due_to_failed_status")
        return 1
    if not args.keep_order:
        api.cancel_order(trade)
        api.update_status(api.stock_account)
    print("cancelled_trade: true")
    return 0


def command_approve_week(args: argparse.Namespace) -> int:
    settings = Settings.load()
    trade_date = _parse_trade_date(args.trade_date)
    weekly_execution_enabled = bool(args.execute)
    budget = args.weekly_budget
    if budget is None:
        if weekly_execution_enabled:
            raise RuntimeError("approve_week --execute requires --weekly-budget.")
        budget = settings.auto_trading.weekly_budget
    if weekly_execution_enabled and budget <= 0:
        raise RuntimeError("approve_week --execute requires --weekly-budget greater than 0.")

    week_id = str(args.week_id or weekly_execution_week_id_for(trade_date)).strip()
    config_path = set_auto_trading_weekly_execution(
        weekly_budget=float(budget),
        weekly_execution_enabled=weekly_execution_enabled,
        weekly_execution_week_id=week_id,
        config_dir=settings.project_root / "config",
    )

    print(f"config_path: {config_path}")
    print(f"trade_date: {trade_date.isoformat()}")
    print(f"weekly_execution_week_id: {week_id}")
    print(f"weekly_budget: {float(budget)}")
    print(f"weekly_execution_enabled: {str(weekly_execution_enabled).lower()}")
    print("note: live submit still also requires live_enabled, SINOPAC_ALLOW_LIVE_SUBMIT, --live, --confirm-live, and AUTO_TRADE_LIVE=1.")
    print(f"note: {A_PRESELECT_CONFIRMATION_REMINDER}")
    return 0


def _submit_compat_order(
    stock_id: str,
    stock_name: str,
    exchange_hint: str,
    side: str,
    order_lot: str,
    quantity: int,
    limit_price: float,
):
    from .order_planner import PlannedOrder

    return PlannedOrder(
        plan_rank=1,
        stock_id=stock_id,
        stock_name=stock_name,
        exchange_hint=exchange_hint,
        side=side,
        order_lot=order_lot,
        quantity=quantity,
        reference_price=limit_price,
        limit_price=limit_price,
        budget_twd=limit_price * quantity,
        confidence=None,
        model_rank=None,
        stage_1_price=None,
        stage_2_price=None,
        target_price=None,
        source_csv="manual",
        note="manual_stock_order",
    )


def command_manual_stock_order(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    simulation = not args.live
    if args.live and args.submit:
        allowed, reason = settings.evaluate_live_submit_guard(confirm_live=args.confirm_live)
        if not allowed:
            raise RuntimeError(describe_live_submit_guard(reason))
    api, _accounts = login(settings, simulation=simulation, fetch_contract=True)
    contract = resolve_stock_contract(api, args.stock_id, exchange_hint=args.exchange)
    price = args.price if args.price is not None else float(contract.reference)
    print(f"mode: {'live' if args.live else 'simulation'}")
    print(f"stock_id: {contract.code}")
    print(f"stock_name: {contract.name}")
    print(f"exchange: {contract.exchange}")
    print(f"action: {args.action}")
    print(f"order_lot: {args.order_lot}")
    print(f"quantity: {args.quantity}")
    print(f"limit_price: {price}")
    if not args.submit:
        print("preview_only: true")
        return 0
    trade = submit_stock_order(
        api,
        _submit_compat_order(
            stock_id=contract.code,
            stock_name=contract.name,
            exchange_hint=str(contract.exchange),
            side=args.action,
            order_lot=args.order_lot,
            quantity=args.quantity,
            limit_price=price,
        ),
        custom_prefix=args.custom_prefix,
    )
    status = getattr(getattr(trade, "status", None), "status", "unknown")
    status_code = getattr(getattr(trade, "status", None), "status_code", "")
    order_id = getattr(getattr(trade, "order", None), "id", "")
    print(f"submitted: status={status} status_code={status_code} order_id={order_id}")
    _best_effort_obsidian_sync(
        settings,
        _today(),
        include_live_status=args.live,
        event_summary=(
            f"manual_stock_order submitted: stock_id={contract.code} action={args.action} "
            f"order_lot={args.order_lot} quantity={args.quantity} price={price} status={status}"
        ),
    )
    return 0


def command_run_allowed_live_order(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    trade_date = _parse_trade_date(args.trade_date)
    status = run_allowed_live_order_task(settings, trade_date=trade_date)
    print(f"task_name: {status.task_name}")
    print(f"trade_date: {status.trade_date}")
    print(f"status: {status.status}")
    print(f"message: {status.message}")
    if status.matched_order_id:
        print(f"matched_order_id: {status.matched_order_id}")
    if status.final_state:
        print(f"final_state: {status.final_state}")
    if status.final_order_id:
        print(f"final_order_id: {status.final_order_id}")
    if status.summary_path:
        print(f"summary_path: {status.summary_path}")
    if status.status in {"submitted", "skipped_existing_order"}:
        return 0
    if status.status == "failed":
        return 1
    return 2


def command_guarded_order_status(args: argparse.Namespace) -> int:
    settings = Settings.load()
    trade_date = _parse_trade_date(args.trade_date)
    summary = _guarded_live_order_status_summary(trade_date)
    post_guarded_check = _normalize_post_guarded_check_for_display(
        _post_guarded_order_check_report_summary(trade_date)
    )
    next_run_guard = _allowed_live_next_run_guard_summary(
        settings,
        scheduled_task_evidence={
            "status": str(summary.get("schedule_status", "")),
            "task_name": str(summary.get("schedule_task_name", "")),
            "state": str(summary.get("schedule_state", "")),
            "next_run_time": str(summary.get("schedule_next_run_time", "")),
            "last_run_time": str(summary.get("schedule_last_run_time", "")),
            "last_task_result": str(summary.get("schedule_last_task_result", "")),
            "description": str(summary.get("schedule_description", "")),
            "message": str(summary.get("schedule_message", "")),
        },
    )
    current_recommendation = _effective_guarded_post_recommendation(
        {
            "after_status": str(summary.get("status", "")),
            "recommendation": str(summary.get("recommendation", "")),
            "next_run_guard_status": str(next_run_guard.get("status", "")),
        }
    )
    summary = {
        **summary,
        "current_recommendation": current_recommendation,
        "current_recommendation_note": _describe_workflow_action(current_recommendation),
        "next_run_guard_status": str(next_run_guard.get("status", "")),
        "next_run_guard_message": str(next_run_guard.get("message", "")),
        "config_timing_status": str(post_guarded_check.get("config_timing_status", "")).strip(),
        "config_timing_message": str(post_guarded_check.get("config_timing_message", "")).strip(),
        "config_path": str(post_guarded_check.get("config_path", "")).strip(),
        "config_last_modified": str(post_guarded_check.get("config_last_modified", "")).strip(),
        "task_recorded_at": str(post_guarded_check.get("task_recorded_at", "")).strip(),
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    print(f"trade_date: {summary['trade_date']}")
    print(f"stock_id: {summary['stock_id']}")
    print(f"status: {summary['status']}")
    print(f"recommendation: {summary['recommendation']}")
    print(f"recommendation_note: {summary.get('recommendation_note', '')}")
    print(f"current_recommendation: {summary.get('current_recommendation', '')}")
    print(f"current_recommendation_note: {summary.get('current_recommendation_note', '')}")
    print(f"next_run_guard_status: {summary.get('next_run_guard_status', '')}")
    print(f"next_run_guard_message: {summary.get('next_run_guard_message', '')}")
    print(f"config_timing_status: {summary.get('config_timing_status', '')}")
    print(f"config_timing_message: {summary.get('config_timing_message', '')}")
    print(f"task_json_status: {summary['task_json_status']}")
    print(f"task_log_status: {summary['task_log_status']}")
    print(f"task_log_exit_code: {summary['task_log_exit_code']}")
    print(f"schedule_status: {summary['schedule_status']}")
    print(f"schedule_state: {summary['schedule_state']}")
    print(f"schedule_next_run_time: {summary['schedule_next_run_time']}")
    print(f"chase_submitted: {summary['chase_submitted']}")
    print(f"chase_final_state: {summary['chase_final_state']}")
    print(f"orders_count: {summary['orders_count']}")
    print(f"fills_count: {summary['fills_count']}")
    print(f"positions_count: {summary['positions_count']}")
    print(f"pnl_snapshots_count: {summary['pnl_snapshots_count']}")
    print(f"run_dir: {summary['run_dir']}")
    return 0


def command_post_guarded_order_check(args: argparse.Namespace) -> int:
    settings = Settings.load()
    trade_date = _parse_trade_date(args.trade_date)
    result = _post_guarded_order_check(
        settings=settings,
        trade_date=trade_date,
        live=args.live,
        reconcile=args.reconcile,
        sell_loop_readiness=args.sell_loop_readiness,
        render_report=args.render_report,
        workflow_status=args.workflow_status,
    )
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return 0

    print(f"trade_date: {result.trade_date}")
    print(f"before_status: {result.before_status}")
    print(f"after_status: {result.after_status}")
    print(f"reconciled: {result.reconciled}")
    print(f"fills_count: {result.fills_count}")
    print(f"positions_count: {result.positions_count}")
    print(f"sell_loop_readiness_recorded: {result.sell_loop_readiness_recorded}")
    print(f"reports_rendered: {result.reports_rendered}")
    print(f"workflow_status_rendered: {result.workflow_status_rendered}")
    print(f"recommendation: {result.recommendation}")
    print(f"recommendation_note: {result.recommendation_note}")
    print(f"effective_recommendation: {result.effective_recommendation}")
    print(f"effective_recommendation_note: {result.effective_recommendation_note}")
    print(f"config_timing_status: {getattr(result, 'config_timing_status', '')}")
    print(f"config_timing_message: {getattr(result, 'config_timing_message', '')}")
    print(f"run_dir: {auto_trading_dir_for(trade_date)}")
    return 0


def command_sell_loop_readiness(args: argparse.Namespace) -> int:
    trade_date = _parse_trade_date(args.trade_date)
    result = _write_sell_loop_readiness(trade_date)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return 0

    print(f"trade_date: {result.trade_date}")
    print(f"last_trade_day: {result.last_trade_day}")
    print(f"is_last_trade_day: {result.is_last_trade_day}")
    print(f"positions_ready: {result.positions_ready}")
    print(f"positions_count: {result.positions_count}")
    print(f"positions_source_date: {result.positions_source_date}")
    print(f"post_guarded_status: {result.post_guarded_status}")
    print(f"post_guarded_recommendation: {result.post_guarded_recommendation}")
    print(f"post_guarded_recommendation_note: {result.post_guarded_recommendation_note}")
    print(f"post_guarded_effective_recommendation: {result.post_guarded_effective_recommendation}")
    print(f"post_guarded_effective_recommendation_note: {result.post_guarded_effective_recommendation_note}")
    print(f"post_guarded_next_run_guard_status: {result.post_guarded_next_run_guard_status}")
    print(f"post_guarded_next_run_guard_message: {result.post_guarded_next_run_guard_message}")
    print(f"post_guarded_config_timing_status: {result.post_guarded_config_timing_status}")
    print(f"post_guarded_config_timing_message: {result.post_guarded_config_timing_message}")
    print(f"post_guarded_config_path: {result.post_guarded_config_path}")
    print(f"post_guarded_config_last_modified: {result.post_guarded_config_last_modified}")
    print(f"post_guarded_task_recorded_at: {result.post_guarded_task_recorded_at}")
    print(f"fills_count: {result.fills_count}")
    print(f"sell_decisions_count: {result.sell_decisions_count}")
    print(f"blocking_reason: {result.blocking_reason}")
    print(f"next_action: {result.next_action}")
    print(f"next_action_note: {result.next_action_note}")
    print(f"run_dir: {auto_trading_dir_for(trade_date)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SinoPac Auto Trading CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    simulate_buy = subparsers.add_parser(
        "simulate-buy",
        help="Plan buy quantities from a budget and create simulation-only dry-run orders.",
    )
    simulate_buy.add_argument("--budget", type=float, required=True, help="Total buying budget in TWD.")
    simulate_buy.add_argument(
        "--stock",
        action="append",
        required=True,
        help="Stock spec. Use CODE:PRICE or CODE:PRICE:WEIGHT. If price is omitted, --quote-file must contain it.",
    )
    simulate_buy.add_argument(
        "--lot",
        default="odd",
        help="Order lot: odd/intraday_odd_lot or common. Default: odd.",
    )
    simulate_buy.add_argument(
        "--quote-file",
        help="Optional CSV with stock_id and last_price/price/reference_price columns.",
    )
    simulate_buy.add_argument(
        "--buffer-multiplier",
        type=float,
        help="Optional cost safety buffer. Defaults to config auto_trading.cost_buffer_multiplier.",
    )
    simulate_buy.add_argument("--output", help="Optional CSV output path.")
    simulate_buy.add_argument("--no-write", action="store_true", help="Print only; do not write the simulation CSV.")
    simulate_buy.add_argument("--json", action="store_true", help="Print JSON output.")

    simple_buy = subparsers.add_parser(
        "buy",
        help="SinoPac-only simple buy preview. Simulation only; never submits to the broker.",
    )
    simple_buy.add_argument("--stock", required=True, help="Stock id, for example 2330.")
    simple_buy.add_argument("--budget", type=float, required=True, help="Buying budget in TWD.")
    simple_buy.add_argument("--price", type=float, help="Limit/reference price. Uses quote file if omitted.")
    simple_buy.add_argument("--lot", default="odd", help="Order lot: odd or common. Default: odd.")
    simple_buy.add_argument("--quote-file", help="Optional CSV with stock_id and last_price/price/reference_price columns.")
    simple_buy.add_argument("--buffer-multiplier", type=float, help="Optional cost safety buffer.")
    simple_buy.add_argument("--output", help="Optional CSV output path.")
    simple_buy.add_argument("--no-write", action="store_true", help="Print only; do not write the simulation CSV.")
    simple_buy.add_argument("--json", action="store_true", help="Print JSON output.")

    simple_sell = subparsers.add_parser(
        "sell",
        help="SinoPac-only simple sell preview. Simulation only; never submits to the broker.",
    )
    simple_sell.add_argument("--stock", required=True, help="Stock id, for example 2330.")
    simple_sell.add_argument("--quantity", "--qty", dest="quantity", type=int, required=True, help="Sell quantity.")
    simple_sell.add_argument("--price", type=float, help="Limit/reference price. Uses quote file if omitted.")
    simple_sell.add_argument("--lot", default="odd", help="Order lot: odd or common. Default: odd.")
    simple_sell.add_argument("--quote-file", help="Optional CSV with stock_id and last_price/price/reference_price columns.")
    simple_sell.add_argument("--output", help="Optional CSV output path.")
    simple_sell.add_argument("--no-write", action="store_true", help="Print only; do not write the simulation CSV.")
    simple_sell.add_argument("--json", action="store_true", help="Print JSON output.")

    simple_order = subparsers.add_parser(
        "order",
        help="Run a SinoPac-only simple buy/sell preview from a JSON file. Simulation only.",
    )
    simple_order.add_argument("--file", required=True, help="JSON order file.")
    simple_order.add_argument("--no-write", action="store_true", help="Print only; do not write the simulation CSV.")
    simple_order.add_argument("--json", action="store_true", help="Print JSON output.")

    model_orders = subparsers.add_parser(
        "model-orders",
        help="Read order intents from another trading model and preview SinoPac simulation-only orders.",
    )
    model_orders.add_argument("--file", required=True, help="JSON or CSV model order-intent file.")
    model_orders.add_argument("--source-model", help="Override source model name.")
    model_orders.add_argument("--buy-budget", type=float, help="Shared budget for buy intents without their own budget/quantity.")
    model_orders.add_argument("--quote-file", help="Optional CSV with stock_id and last_price/price/reference_price columns.")
    model_orders.add_argument("--buffer-multiplier", type=float, help="Optional cost safety buffer.")
    model_orders.add_argument("--output", help="Optional CSV output path.")
    model_orders.add_argument("--no-write", action="store_true", help="Print only; do not write the simulation CSV.")
    model_orders.add_argument("--json", action="store_true", help="Print JSON output.")

    subparsers.add_parser(
        "app",
        help="Open the simple SinoPac setup and trading interface.",
    )

    for name in (
        "prepare_week",
        "prepare_llm_selection",
        "apply_llm_selection",
        "track_until_final",
        "finalize",
        "buy_loop",
        "sell_loop",
        "reconcile_broker_state",
        "repair_confirmation",
        "render_report",
        "settle_week",
        "workflow_status",
        "refresh_dashboard",
    ):
        command_parser = subparsers.add_parser(name)
        command_parser.add_argument("--trade-date", help="Trade date in YYYY-MM-DD format.")
        if name == "finalize":
            command_parser.add_argument("--max-names", type=int, help="Maximum number of final symbols.")
        if name == "buy_loop":
            command_parser.add_argument("--live", action="store_true")
            command_parser.add_argument("--confirm-live", action="store_true")
            command_parser.add_argument("--reprice-threshold-ticks", type=int, default=5)
        if name == "sell_loop":
            command_parser.add_argument("--live", action="store_true")
            command_parser.add_argument("--confirm-live", action="store_true")
            command_parser.add_argument(
                "--prepare-only",
                action="store_true",
                help="Run sell readiness checks and write sell_decisions.csv without submitting sell orders.",
            )
        if name == "reconcile_broker_state":
            command_parser.add_argument("--live", action="store_true")
            command_parser.add_argument("--stock-id", action="append", help="Optional target stock id; repeatable.")
        if name == "repair_confirmation":
            command_parser.add_argument(
                "--live",
                action="store_true",
                help="Read broker order/fill state before building the repair confirmation report.",
            )
            command_parser.add_argument(
                "--email-to",
                default="ops@example.com",
                help="Recipient for the follow-up confirmation email.",
            )
        if name == "refresh_dashboard":
            command_parser.add_argument("--prepare-week", action="store_true")
            command_parser.add_argument("--track-until-final", action="store_true")
            command_parser.add_argument("--prepare-llm-selection", action="store_true")
            command_parser.add_argument("--apply-llm-selection", action="store_true")
            command_parser.add_argument("--finalize", action="store_true")
            command_parser.add_argument("--buy-loop", action="store_true")
            command_parser.add_argument("--settle-week", action="store_true")
            command_parser.add_argument("--auto-track", action="store_true")
            command_parser.add_argument("--auto-buy-loop", action="store_true", default=True)
            command_parser.add_argument("--auto-settle-week", action="store_true")
            command_parser.add_argument("--live", action="store_true")
            command_parser.add_argument("--confirm-live", action="store_true")
            command_parser.add_argument("--reprice-threshold-ticks", type=int, default=5)
            command_parser.add_argument("--max-names", type=int, help="Maximum number of final symbols.")

    allowed_live_order_parser = subparsers.add_parser(
        "run_allowed_live_order",
        help=f"Run the single permitted live order automation ({ALLOWED_LIVE_ORDER_TASK_NAME}).",
    )
    allowed_live_order_parser.add_argument("--trade-date", help="Trade date in YYYY-MM-DD format.")

    guarded_status = subparsers.add_parser("guarded_order_status")
    guarded_status.add_argument("--trade-date", help="Trade date in YYYY-MM-DD format.")
    guarded_status.add_argument("--json", action="store_true")

    post_guarded_order_check = subparsers.add_parser("post_guarded_order_check")
    post_guarded_order_check.add_argument("--trade-date", help="Trade date in YYYY-MM-DD format.")
    post_guarded_order_check.add_argument("--live", action="store_true")
    post_guarded_order_check.add_argument("--reconcile", action="store_true")
    post_guarded_order_check.add_argument("--sell-loop-readiness", action="store_true")
    post_guarded_order_check.add_argument("--render-report", action="store_true")
    post_guarded_order_check.add_argument("--workflow-status", action="store_true")
    post_guarded_order_check.add_argument("--json", action="store_true")

    sell_loop_readiness = subparsers.add_parser("sell_loop_readiness")
    sell_loop_readiness.add_argument("--trade-date", help="Trade date in YYYY-MM-DD format.")
    sell_loop_readiness.add_argument("--json", action="store_true")

    smoke = subparsers.add_parser("live_smoke_test")
    smoke.add_argument("--trade-date", help="Trade date in YYYY-MM-DD format.")
    smoke.add_argument("--stock-id", help="Optional direct smoke-test stock id.")
    smoke.add_argument("--stock-name", help="Optional direct smoke-test stock name.")
    smoke.add_argument("--source", default="manual")
    smoke.add_argument("--live", action="store_true")
    smoke.add_argument("--confirm-live", action="store_true")

    chase = subparsers.add_parser("chase-stock-order")
    chase.add_argument("--stock-id", required=True)
    chase.add_argument("--exchange", default="TSE")
    chase.add_argument("--price-cap", type=float)
    chase.add_argument("--quantity", type=int, required=True)
    chase.add_argument("--order-lot", choices=["Common", "IntradayOdd"], required=True)
    chase.add_argument("--live", action="store_true")
    chase.add_argument("--submit", action="store_true")
    chase.add_argument("--confirm-live", action="store_true")
    chase.add_argument("--start-time", default="09:10")
    chase.add_argument("--end-time", default="13:20")
    chase.add_argument("--check-interval-seconds", type=int, default=300)
    chase.add_argument("--reprice-threshold-ticks", type=int, default=5)
    chase.add_argument("--custom-prefix", default="CH")

    sync_parser = subparsers.add_parser("sync_obsidian")
    sync_parser.add_argument("--trade-date", help="Trade date in YYYY-MM-DD format.")
    sync_parser.add_argument("--include-live-status", action="store_true")
    sync_parser.add_argument("--event-summary")

    login_parser = subparsers.add_parser("login-check")
    login_parser.add_argument("--live", action="store_true")
    login_parser.add_argument("--no-fetch-contract", action="store_true")

    api_test = subparsers.add_parser("api-test-stock")
    api_test.add_argument("--stock-id", default="2890")
    api_test.add_argument("--exchange", default="TSE")
    api_test.add_argument("--price", type=float)
    api_test.add_argument("--quantity", type=int, default=1)
    api_test.add_argument("--order-lot", choices=["Common", "IntradayOdd"], default="Common")
    api_test.add_argument("--action", choices=["Buy", "Sell"], default="Buy")
    api_test.add_argument("--keep-order", action="store_true")

    approve_week = subparsers.add_parser(
        "approve_week",
        help="Set the user-approved weekly budget and execution switch.",
    )
    approve_week.add_argument("--trade-date", help="Trade date in YYYY-MM-DD format.")
    approve_week.add_argument("--weekly-budget", type=float, help="Approved weekly budget in TWD.")
    approve_week.add_argument("--week-id", help="Optional weekly execution id; defaults to ISO week.")
    approve_week_mode = approve_week.add_mutually_exclusive_group(required=True)
    approve_week_mode.add_argument("--execute", action="store_true", help="Allow this week to execute.")
    approve_week_mode.add_argument("--disable", action="store_true", help="Disable this week's execution gate.")

    manual = subparsers.add_parser("manual-stock-order")
    manual.add_argument("--stock-id", required=True)
    manual.add_argument("--exchange", default="")
    manual.add_argument("--price", type=float)
    manual.add_argument("--quantity", type=int, required=True)
    manual.add_argument("--order-lot", choices=["Common", "IntradayOdd"], required=True)
    manual.add_argument("--action", choices=["Buy", "Sell"], required=True)
    manual.add_argument("--live", action="store_true")
    manual.add_argument("--submit", action="store_true")
    manual.add_argument("--confirm-live", action="store_true")
    manual.add_argument("--custom-prefix", default="MO")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "simulate-buy":
            return command_simulate_buy(args)
        if args.command == "buy":
            return command_simple_buy(args)
        if args.command == "sell":
            return command_simple_sell(args)
        if args.command == "order":
            return command_simple_order(args)
        if args.command == "model-orders":
            return command_model_orders(args)
        if args.command == "app":
            from .simple_app import command_app

            return command_app(args)
        if args.command == "prepare_week":
            return command_prepare_week(args)
        if args.command == "prepare_llm_selection":
            return command_prepare_llm_selection(args)
        if args.command == "apply_llm_selection":
            return command_apply_llm_selection(args)
        if args.command == "track_until_final":
            return command_track_until_final(args)
        if args.command == "finalize":
            return command_finalize(args)
        if args.command == "buy_loop":
            return command_buy_loop(args)
        if args.command == "sell_loop":
            return command_sell_loop(args)
        if args.command == "reconcile_broker_state":
            return command_reconcile_broker_state(args)
        if args.command == "repair_confirmation":
            return command_repair_confirmation(args)
        if args.command == "render_report":
            return command_render_report(args)
        if args.command == "settle_week":
            return command_settle_week(args)
        if args.command == "workflow_status":
            return command_workflow_status(args)
        if args.command == "refresh_dashboard":
            return command_refresh_dashboard(args)
        if args.command == "run_allowed_live_order":
            return command_run_allowed_live_order(args)
        if args.command == "guarded_order_status":
            return command_guarded_order_status(args)
        if args.command == "post_guarded_order_check":
            return command_post_guarded_order_check(args)
        if args.command == "sell_loop_readiness":
            return command_sell_loop_readiness(args)
        if args.command == "live_smoke_test":
            return command_live_smoke_test(args)
        if args.command == "chase-stock-order":
            return command_chase_stock_order(args)
        if args.command == "sync_obsidian":
            return command_sync_obsidian(args)
        if args.command == "login-check":
            return command_login_check(args)
        if args.command == "api-test-stock":
            return command_api_test_stock(args)
        if args.command == "approve_week":
            return command_approve_week(args)
        return command_manual_stock_order(args)
    except (RuntimeError, FileNotFoundError) as exc:
        if getattr(args, "command", "") == "buy_loop" and bool(getattr(args, "live", False)):
            try:
                _record_live_buy_repair_required(
                    trade_date=_parse_trade_date(getattr(args, "trade_date", None)),
                    reason="live_buy_loop_error",
                    error=str(exc),
                )
            except Exception:
                pass
        print(f"error: {exc}", file=sys.stderr)
        return 1
