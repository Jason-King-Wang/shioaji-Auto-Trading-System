from __future__ import annotations

import json
import re
from html import escape
from pathlib import Path
from typing import Any


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Unsupported JSON value: {type(value)!r}")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default), encoding="utf-8")


def _daily_snapshot_payload(report: dict[str, Any]) -> dict[str, Any]:
    payload = dict(report)
    payload["events"] = _event_rows_for_display(list(report.get("events", [])))
    overview = report.get("overview", {})
    if isinstance(overview, dict):
        overview = _augment_status_labels(dict(overview))
        overview = _augment_action_labels(overview)
        overview = _augment_misc_labels(overview)
        overview = _augment_overview_display_text(overview)
        payload["overview"] = overview
    overview_passthrough_keys = [
        "today_status",
        "today_status_note",
        "today_ordering_status",
        "today_ordering_note",
        "selection_source_status",
        "selection_source_note",
        "selection_source_path",
        "selection_source_last_modified",
        "dashboard_refresh_status",
        "dashboard_refresh_steps",
        "dashboard_refresh_note",
        "dashboard_refresh_trigger_status",
        "dashboard_refresh_trigger_artifacts",
        "dashboard_refresh_trigger_note",
        "dashboard_last_materialization_status",
        "dashboard_last_materialization_steps",
        "dashboard_last_materialization_note",
        "dashboard_last_materialization_trigger_status",
        "dashboard_last_materialization_trigger_artifacts",
        "dashboard_last_materialization_trigger_note",
        "weekly_settlement_status",
        "weekly_settlement_artifacts",
        "weekly_settlement_note",
        "weekly_settlement_next_action",
        "weekly_settlement_next_action_note",
        "selection_materialization_status",
        "selection_materialization_missing_artifacts",
        "selection_materialization_note",
        "selection_materialization_next_action",
        "selection_materialization_next_action_note",
    ]
    if isinstance(overview, dict):
        for key, value in overview.items():
            if key not in payload or payload.get(key) in ("", None):
                payload[key] = value
        for key in overview_passthrough_keys:
            if key not in payload or payload.get(key) in ("", None):
                payload[key] = overview.get(key, "")
    payload = _augment_status_labels(payload)
    payload = _augment_action_labels(payload)
    payload = _augment_misc_labels(payload)
    return payload


def _money(value: Any) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value or "")


def _pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return str(value or "")


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _display_bool(value: Any) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    return _text(value)


STATUS_DISPLAY_LABELS: dict[str, str] = {
    "workflow_status_rendered": "已輸出工作流狀態",
    "buy_window_closed": "買窗已關閉",
    "same_day_a_preselect_loaded": "同日 A 預選已載入",
    "same_day_a_preselect_missing_pass": "同日 A 預選缺失並直接 pass",
    "report_only_refresh": "僅重跑報表",
    "materialized_without_buy_loop": "已物化但未進入買進迴圈",
    "same_day_source_newer_than_local_preselect_artifacts": "同日來源比本地 preselect 產物更新",
    "historical_materialization_reason_not_recorded": "歷史物化原因未記錄",
    "weekly_settlement_current": "週結算已是最新",
    "same_day_source_expires_after_trade_date": "同日來源僅限當日，不延用到下個交易日",
    "local_materialization_current": "本地整包產物已對齊",
    "guarded_time_passed_no_backfill": "受保護下單補跑視窗已關閉",
    "basket_a_loaded": "整包 A 已載入",
    "basket_a_same_day_json_missing_pass": "整包 A 同日 JSON 缺失並直接略過",
    "basket_buy_window_closed_last_trade_day": "整包最後交易日買窗已關閉",
    "same_day_a_source_arrived_after_basket_buy_window_closed": "同日 A 來源晚於整包買窗",
    "requires_rule_alignment": "需要對齊規則",
    "no_auto_new_buy_paths_remaining_today": "今天已無自動新買單路徑",
    "skipped_config_live_disabled": "因設定未開啟真實下單而略過",
    "skipped_weekly_execution_disabled": "因本週執行開關未開啟而略過",
    "skipped_weekly_budget_missing": "因本週預算未設定而略過",
    "skipped_weekly_execution_week_mismatch": "因本週授權週別不符而略過",
    "live_guard_ready": "下次受保護下單排程已就緒",
    "scheduled_task_time_passed": "排程時間已過",
    "live_enabled_fixed_after_scheduled_run": "live_enabled 參數在排程後才修正",
    "historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill": "保護條件已修好；交易視窗內應補跑",
    "historical_guard_issue_already_fixed_wait_for_next_schedule": "歷史保護條件已修好並等待下次排程",
    "no_strategy_positions": "沒有策略部位",
}

COMMAND_DISPLAY_LABELS: dict[str, str] = {
    "prepare_week": "載入預選",
    "prepare_llm_selection": "準備 LLM 審核",
    "llm_decisions": "LLM 決策檔",
    "apply_llm_selection": "套用 LLM 決策",
    "track_until_final": "追蹤到收盤",
    "finalize": "完成訂版",
    "render_report": "輸出日報",
    "workflow_status": "輸出工作流狀態",
    "buy_loop": "買進迴圈",
    "fills": "成交回寫",
    "positions": "部位回寫",
    "excluded_positions": "排除部位回寫",
    "pnl_snapshots": "損益快照",
    "post_guarded_order_check": "受保護下單後檢查",
    "sell_loop_readiness": "賣出就緒檢查",
    "sell_loop": "賣出迴圈",
    "settle_week": "週結算",
    "refresh_dashboard": "刷新儀表板",
}

STATUS_LABEL_KEYS = [
    "today_status",
    "selection_source_status",
    "dashboard_refresh_status",
    "dashboard_refresh_trigger_status",
    "dashboard_last_materialization_status",
    "dashboard_last_materialization_trigger_status",
    "weekly_settlement_status",
    "selection_source_carry_forward_status",
    "selection_materialization_status",
    "today_ordering_status",
    "today_ordering_conflict_status",
    "today_ordering_conflict_resolution_status",
    "today_new_order_submission_status",
    "guarded_post_check_status",
    "guarded_post_check_effective_recommendation",
    "guarded_post_check_next_run_guard_status",
    "guarded_post_check_config_timing_status",
    "sell_loop_readiness_blocking_reason",
    "sell_loop_readiness_next_action",
    "sell_loop_readiness_post_guarded_next_run_guard_status",
    "sell_loop_readiness_post_guarded_config_timing_status",
    "status",
]

ACTION_DISPLAY_LABELS: dict[str, str] = {
    "weekly_settlement_current_no_action_required": "週結算已是最新，今天不需額外動作",
    "materialization_current_no_action_required": "本地整包產物已對齊，今天不需額外展開",
    "wait_for_next_trade_day_same_day_a_then_materialize": "等待下一個交易日拿到新的同日 A 預選後再展開",
    "align_a_source_timing_or_basket_buy_window_rule": "對齊 A 來源時間或 basket 買窗規則",
    "enable_live_in_config_before_next_scheduled_run": "下次排程前先啟用 auto_trading.live_enabled",
    "historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill": "保護條件已修好；交易視窗內應補跑",
}

ACTION_LABEL_KEYS = [
    "weekly_settlement_next_action",
    "selection_materialization_next_action",
    "today_ordering_conflict_resolution_action",
    "guarded_post_check_recommendation",
    "sell_loop_readiness_next_action",
    "sell_loop_readiness_post_guarded_effective_recommendation",
]

MODE_DISPLAY_LABELS: dict[str, str] = {
    "live_guarded": "受保護下單真實模式",
    "live": "真實模式",
    "dry_run": "乾跑模式",
    "simulation": "模擬模式",
}

PROVIDER_DISPLAY_LABELS: dict[str, str] = {
    "ab_llm_preselect_json": "同日 A 預選 JSON",
    "manual_csv": "手動 CSV",
    "stock_model_vault_export": "StockModelVault 匯出",
}

POSITION_DATA_QUALITY_LABELS: dict[str, str] = {
    "direct": "直接策略部位",
    "fallback": "fallback 券商快照",
}

BUY_MODE_DISPLAY_LABELS: dict[str, str] = {
    "normal": "一般追價",
    "add": "加強追價",
    "super_add": "超級追價",
}

ORDER_STATUS_DISPLAY_LABELS: dict[str, str] = {
    "not_submitted": "未送單",
    "pending": "待處理",
    "active": "有效掛單",
    "dry_run": "乾跑",
    "Submitted": "已送出",
    "PartFilled": "部分成交",
    "Filled": "已成交",
    "Cancelled": "已取消",
    "Failed": "失敗",
    "blocked_ambiguous_fill": "因待對帳成交而阻擋",
    "blocked_excluded_position": "因排除部位而阻擋",
    "blocked_broker_underheld": "因券商持股不足而阻擋",
    "existing_buy_order_unverified": "既有買單未驗明",
}

OVERVIEW_BULLET_LABELS: dict[str, str] = {
    "week_id": "週期代號",
    "run_id": "執行代號",
    "mode": "模式",
    "active_selection_provider": "啟用中的選股來源",
    "weekly_budget": "每週預算",
    "hard_budget": "硬上限預算",
    "used_cash": "已用現金",
    "remaining_cash": "剩餘現金",
    "current_equity": "目前權益",
    "strategy_pnl_after_fee_tax": "策略稅後損益",
    "strategy_return": "策略報酬率",
    "today_status": "今日狀態",
    "today_status_note": "今日狀態說明",
    "workflow_completed_steps": "已完成步驟數",
    "workflow_pending_steps": "待處理步驟數",
    "workflow_closed_steps": "今日關閉步驟數",
    "last_update_time": "最後更新時間",
    "selection_source_path": "選股來源路徑",
    "selection_source_last_modified": "選股來源最後更新",
    "selection_source_status": "選股來源狀態",
    "selection_source_note": "選股來源說明",
    "dashboard_refresh_status": "儀表板刷新狀態",
    "dashboard_refresh_steps": "儀表板刷新步驟",
    "dashboard_refresh_note": "儀表板刷新說明",
    "dashboard_refresh_trigger_status": "儀表板刷新觸發狀態",
    "dashboard_refresh_trigger_artifacts": "儀表板刷新觸發證據",
    "dashboard_refresh_trigger_note": "儀表板刷新觸發說明",
    "dashboard_last_materialization_status": "最近一次物化狀態",
    "dashboard_last_materialization_steps": "最近一次物化步驟",
    "dashboard_last_materialization_note": "最近一次物化說明",
    "dashboard_last_materialization_trigger_status": "最近一次物化觸發狀態",
    "dashboard_last_materialization_trigger_artifacts": "最近一次物化觸發證據",
    "dashboard_last_materialization_trigger_note": "最近一次物化觸發說明",
    "weekly_settlement_open": "週結算開啟中",
    "weekly_settlement_status": "週結算狀態",
    "weekly_settlement_artifacts": "週結算產物",
    "weekly_settlement_note": "週結算說明",
    "weekly_settlement_next_action": "週結算下一步",
    "weekly_settlement_next_action_note": "週結算下一步說明",
    "selection_source_carry_forward_open": "來源延用檢查開啟中",
    "selection_source_carry_forward_status": "來源延用狀態",
    "selection_source_carry_forward_next_trade_day": "來源延用下個交易日",
    "selection_source_carry_forward_note": "來源延用說明",
    "selection_materialization_open": "來源物化檢查開啟中",
    "selection_materialization_status": "來源物化狀態",
    "selection_materialization_missing_artifacts": "來源物化缺少產物",
    "selection_materialization_note": "來源物化說明",
    "selection_materialization_next_action": "來源物化下一步",
    "selection_materialization_next_action_note": "來源物化下一步說明",
    "today_ordering_status": "今日下單狀態",
    "today_ordering_note": "今日下單說明",
    "today_ordering_conflict_status": "今日下單衝突狀態",
    "today_ordering_conflict_note": "今日下單衝突說明",
    "today_ordering_conflict_resolution_status": "今日下單衝突解法狀態",
    "today_ordering_conflict_resolution_action": "今日下單衝突解法動作",
    "today_ordering_conflict_resolution_note": "今日下單衝突解法說明",
    "today_new_order_submission_open": "今日新單路徑開啟中",
    "today_new_order_submission_status": "今日新單路徑狀態",
    "today_new_order_submission_note": "今日新單路徑說明",
    "position_data_quality": "部位資料品質",
    "positions_source_date": "部位來源日期",
    "guarded_post_check_status": "受保護下單後檢查狀態",
    "guarded_post_check_recommendation": "受保護下單建議",
    "guarded_post_check_effective_recommendation": "受保護下單目前有效建議",
    "guarded_post_check_effective_recommendation_note": "受保護下單目前有效建議說明",
    "guarded_post_check_reconciled": "受保護下單已對帳",
    "guarded_post_check_fills_count": "受保護下單成交筆數",
    "guarded_post_check_positions_count": "受保護下單部位筆數",
    "guarded_post_check_next_run_guard_status": "受保護下單下次排程狀態",
    "guarded_post_check_next_run_guard_message": "受保護下單下次排程說明",
    "guarded_post_check_config_timing_status": "受保護下單設定時序狀態",
    "guarded_post_check_config_timing_message": "受保護下單設定時序說明",
    "guarded_post_check_config_path": "受保護下單設定檔路徑",
    "guarded_post_check_config_last_modified": "受保護下單設定檔最後更新",
    "guarded_post_check_task_recorded_at": "受保護下單任務記錄時間",
    "sell_loop_readiness_blocking_reason": "賣出就緒阻塞原因",
    "sell_loop_readiness_next_action": "賣出就緒下一步",
    "sell_loop_readiness_next_action_note": "賣出就緒下一步說明",
    "sell_loop_readiness_positions_ready": "賣出就緒部位已就緒",
    "sell_loop_readiness_positions_count": "賣出就緒部位數量",
    "sell_loop_readiness_positions_source_date": "賣出就緒部位來源日期",
    "sell_loop_readiness_post_guarded_effective_recommendation": "賣出就緒目前有效建議",
    "sell_loop_readiness_post_guarded_effective_recommendation_note": "賣出就緒目前有效建議說明",
    "sell_loop_readiness_post_guarded_next_run_guard_status": "賣出就緒下次排程狀態",
    "sell_loop_readiness_post_guarded_config_timing_status": "賣出就緒設定時序狀態",
    "sell_loop_readiness_post_guarded_config_timing_message": "賣出就緒設定時序說明",
    "sell_loop_readiness_post_guarded_config_path": "賣出就緒設定檔路徑",
    "sell_loop_readiness_post_guarded_config_last_modified": "賣出就緒設定檔最後更新",
    "sell_loop_readiness_post_guarded_task_recorded_at": "賣出就緒任務記錄時間",
    "ambiguous_fill_guard_count": "待對帳成交 Guard 次數",
    "excluded_position_guard_count": "排除部位 Guard 次數",
    "broker_underheld_guard_count": "券商持股不足 Guard 次數",
}


def _status_code_label(value: Any) -> str:
    token = _text(value).strip()
    if not token:
        return ""
    if token in STATUS_DISPLAY_LABELS:
        return STATUS_DISPLAY_LABELS[token]
    if "+" in token:
        parts = [part.strip() for part in token.split("+") if part.strip()]
        labels = [STATUS_DISPLAY_LABELS.get(part, part) for part in parts]
        if labels != parts:
            return " + ".join(labels)
    return token


def _status_with_inline_label(value: Any) -> str:
    token = _text(value).strip()
    if not token:
        return ""
    label = _status_code_label(token)
    if not label or label == token:
        return token
    return f"{token} ({label})"


def _status_with_display_label(value: Any) -> str:
    token = _text(value).strip()
    if not token:
        return ""
    label = _status_code_label(token)
    if not label or label == token:
        return token
    return f"{label} ({token})"


def _augment_status_labels(target: dict[str, Any]) -> dict[str, Any]:
    for key in STATUS_LABEL_KEYS:
        token = _text(target.get(key, "")).strip()
        if not token:
            continue
        label = _status_code_label(token)
        if label and label != token:
            target[f"{key}_label"] = label
    return target


def _action_code_label(value: Any) -> str:
    token = _text(value).strip()
    if not token:
        return ""
    return ACTION_DISPLAY_LABELS.get(token, token)


def _action_with_inline_label(value: Any) -> str:
    token = _text(value).strip()
    if not token:
        return ""
    label = _action_code_label(token)
    if not label or label == token:
        return token
    return f"{token} ({label})"


def _action_with_display_label(value: Any) -> str:
    token = _text(value).strip()
    if not token:
        return ""
    label = _action_code_label(token)
    if not label or label == token:
        return token
    return f"{label} ({token})"


def _mapped_code_label(value: Any, labels: dict[str, str]) -> str:
    token = _text(value).strip()
    if not token:
        return ""
    return labels.get(token, token)


def _mapped_with_inline_label(value: Any, labels: dict[str, str]) -> str:
    token = _text(value).strip()
    if not token:
        return ""
    label = _mapped_code_label(token, labels)
    if not label or label == token:
        return token
    return f"{token} ({label})"


def _mapped_with_display_label(value: Any, labels: dict[str, str]) -> str:
    token = _text(value).strip()
    if not token:
        return ""
    label = _mapped_code_label(token, labels)
    if not label or label == token:
        return token
    return f"{label} ({token})"


def _augment_action_labels(target: dict[str, Any]) -> dict[str, Any]:
    for key in ACTION_LABEL_KEYS:
        token = _text(target.get(key, "")).strip()
        if not token:
            continue
        label = _action_code_label(token)
        if label and label != token:
            target[f"{key}_label"] = label
    return target


def _augment_misc_labels(target: dict[str, Any]) -> dict[str, Any]:
    mappings = {
        "mode": MODE_DISPLAY_LABELS,
        "provider_name": PROVIDER_DISPLAY_LABELS,
        "position_data_quality": POSITION_DATA_QUALITY_LABELS,
    }
    for key, labels in mappings.items():
        token = _text(target.get(key, "")).strip()
        if not token:
            continue
        label = _mapped_code_label(token, labels)
        if label and label != token:
            target[f"{key}_label"] = label
    return target


OVERVIEW_TEXT_DISPLAY_KEYS = (
    "today_status_note",
    "selection_source_note",
    "dashboard_refresh_steps",
    "dashboard_refresh_note",
    "dashboard_refresh_trigger_note",
    "dashboard_last_materialization_steps",
    "dashboard_last_materialization_note",
    "dashboard_last_materialization_trigger_note",
    "weekly_settlement_note",
    "weekly_settlement_next_action_note",
    "selection_source_carry_forward_note",
    "selection_materialization_note",
    "selection_materialization_next_action_note",
    "today_ordering_note",
    "today_ordering_conflict_note",
    "today_ordering_conflict_resolution_note",
    "today_new_order_submission_note",
    "guarded_post_check_effective_recommendation_note",
    "guarded_post_check_next_run_guard_message",
    "guarded_post_check_config_timing_message",
    "sell_loop_readiness_next_action_note",
    "sell_loop_readiness_post_guarded_effective_recommendation_note",
    "sell_loop_readiness_post_guarded_config_timing_message",
)


def _normalize_display_text(value: Any) -> str:
    text = _normalize_event_message(value)
    for token, label in COMMAND_DISPLAY_LABELS.items():
        text = re.sub(
            rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])",
            f"{label} ({token})",
            text,
        )
    text = text.replace("歷史 guard 問題", "歷史保護條件問題")
    text = text.replace("受 guard 的真實模式", "受保護下單真實模式")
    text = text.replace("workflow 狀態", "工作流狀態")
    text = text.replace("dashboard refresh", "儀表板刷新")
    text = text.replace("2330 guarded 單", "2330 受保護下單")
    text = text.replace("2330 guarded 路徑", "2330 受保護下單路徑")
    text = text.replace("guarded 2330", "2330 受保護下單路徑")
    text = text.replace("guarded 真實執行", "受保護下單真實執行")
    text = text.replace("guarded 排程", "受保護下單排程")
    text = text.replace("guarded 下單", "受保護下單")
    text = text.replace("guarded 單", "受保護下單")
    text = text.replace("guarded 路徑", "受保護下單路徑")
    text = text.replace("guarded 任務", "受保護下單任務")
    text = text.replace("guarded 視窗", "受保護下單視窗")
    text = text.replace("guarded 後檢查", "受保護下單後檢查")
    text = text.replace("剩餘的 guarded 與 整包買入路徑都已關閉", "剩餘的受保護下單路徑與整包買進路徑都已關閉")
    text = text.replace("2330 guarded 單的 guard 已修好", "2330 受保護下單的保護條件已修好")
    text = text.replace("guard 問題", "保護條件問題")
    text = text.replace("guard 已修好", "保護條件已修好")
    text = text.replace("guard 設定", "保護條件設定")
    text = text.replace("guarded 真實執行", "受保護下單真實執行")
    text = text.replace("guarded 2330", "2330 受保護下單路徑")
    text = text.replace("guarded 單", "受保護下單")
    text = text.replace("price cap", "價格上限")
    text = text.replace("duplicate-order guard", "重複單保護")
    text = text.replace("重複單 guard", "重複單保護")
    text = text.replace("basket A", "整包 A")
    text = text.replace("basket 買進迴圈", "整包買進迴圈")
    text = text.replace("basket buy_loop", "整包買進迴圈 (buy_loop)")
    text = text.replace("basket 買入路徑", "整包買入路徑")
    text = text.replace("basket 買進路徑", "整包買進路徑")
    text = text.replace("basket 買窗", "整包買窗")
    text = text.replace("basket 產物", "整包產物")
    text = text.replace("same-day A", "同日 A")
    text = text.replace("live submit", "真實下單")
    text = text.replace("live-submit guard", "真實下單保護條件")
    text = text.replace("workflow status", "工作流狀態")
    text = text.replace("materializing refresh", "物化刷新")
    text = text.replace("trigger reason", "觸發原因")
    text = text.replace("task log", "任務日誌")
    text = text.replace("guarded 執行", "受保護下單執行")
    text = text.replace("guarded 與 basket 買入路徑", "受保護下單與整包買進路徑")
    text = text.replace("下一次 受保護下單真實執行", "下一次受保護下單真實執行")
    text = text.replace("錯過的 受保護下單", "錯過的受保護下單")
    text = text.replace("這次 受保護下單執行", "這次受保護下單執行")
    text = text.replace("本地 basket 產物已就緒", "本地整包產物已就緒")
    return text


def _augment_overview_display_text(overview: dict[str, Any]) -> dict[str, Any]:
    for key in OVERVIEW_TEXT_DISPLAY_KEYS:
        value = _text(overview.get(key, "")).strip()
        if value:
            overview[f"{key}_display"] = _normalize_display_text(value)
    return overview


def _augment_row_mapped_fields(target: dict[str, Any], key: str, labels: dict[str, str]) -> dict[str, Any]:
    token = _text(target.get(key, "")).strip()
    if not token:
        return target
    label = _mapped_code_label(token, labels)
    if label and label != token:
        target[f"{key}_label"] = label
    target[f"{key}_display_inline"] = _mapped_with_inline_label(token, labels)
    target[f"{key}_display"] = _mapped_with_display_label(token, labels)
    return target


def _augment_selection_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    augmented: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        _augment_row_mapped_fields(item, "provider_name", PROVIDER_DISPLAY_LABELS)
        augmented.append(item)
    return augmented


def _augment_buy_execution_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    augmented: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        _augment_row_mapped_fields(item, "current_mode", BUY_MODE_DISPLAY_LABELS)
        _augment_row_mapped_fields(item, "order_status_summary", ORDER_STATUS_DISPLAY_LABELS)
        augmented.append(item)
    return augmented


def _augment_daily_report_display(report: dict[str, Any]) -> dict[str, Any]:
    augmented = dict(report)
    overview = report.get("overview", {})
    if isinstance(overview, dict):
        display_overview = dict(overview)
        display_overview = _augment_status_labels(display_overview)
        display_overview = _augment_action_labels(display_overview)
        display_overview = _augment_misc_labels(display_overview)
        display_overview = _augment_overview_display_text(display_overview)
        augmented["overview"] = display_overview
    augmented["selection_rows"] = _augment_selection_rows(list(report.get("selection_rows", [])))
    augmented["buy_execution_rows"] = _augment_buy_execution_rows(list(report.get("buy_execution_rows", [])))
    return augmented


def _event_type_display_label(value: Any) -> str:
    token = _text(value).strip()
    labels = {
        "allowed_live_order_task": "受保護下單任務",
        "prepare_week": "預選載入",
        "finalize": "訂版完成",
        "workflow_status": "工作流狀態",
        "refresh_dashboard": "儀表板更新",
        "post_guarded_order_check": "受保護下單後檢查",
        "sell_loop_readiness": "賣出就緒檢查",
        "sell_loop": "賣出迴圈",
    }
    return labels.get(token, token)


def _event_action_display_label(action: Any, *, event_type: Any) -> str:
    token = _text(action).strip() or _text(event_type).strip()
    labels = {
        "allowed_live_order_task": "執行受保護下單任務",
        "prepare_week": "載入預選名單",
        "finalize": "完成訂版",
        "workflow_status": "輸出工作流狀態",
        "refresh_dashboard": "更新儀表板",
        "post_guarded_order_check": "檢查受保護下單產物",
        "sell_loop_readiness": "檢查賣出就緒狀態",
        "sell_loop": "評估賣出迴圈",
    }
    return labels.get(token, token)


def _normalize_event_message(value: Any) -> str:
    message = _text(value)
    if not message:
        return ""

    def _replace_status(match: re.Match[str]) -> str:
        key = match.group(1)
        token = match.group(2)
        return f"{key}={_status_with_inline_label(token)}"

    def _replace_action(match: re.Match[str]) -> str:
        key = match.group(1)
        token = match.group(2)
        return f"{key}={_action_with_inline_label(token)}"

    def _replace_provider(match: re.Match[str]) -> str:
        token = match.group(1)
        return f"來源提供者={_mapped_with_inline_label(token, PROVIDER_DISPLAY_LABELS)}"

    message = re.sub(
        r"\b(after|current_step|blocking)=([a-z]+(?:_[a-z0-9]+){1,}(?:\+[a-z]+(?:_[a-z0-9]+){1,})*)",
        _replace_status,
        message,
    )
    message = re.sub(
        r"\b(next_action)=([a-z]+(?:_[a-z0-9]+){1,})",
        _replace_action,
        message,
    )
    message = re.sub(
        r"\bprovider=([a-z]+(?:_[a-z0-9]+){1,})",
        _replace_provider,
        message,
    )
    message = re.sub(
        r"^Rendered workflow status with (\d+) checklist rows\.$",
        lambda match: f"已輸出工作流狀態，包含 {match.group(1)} 筆清單列。",
        message,
    )
    message = message.replace(
        "Checked guarded live order artifacts: ",
        "已檢查受保護下單產物：",
    )
    message = message.replace(
        "Checked guarded live order artifacts.",
        "已檢查受保護下單產物。",
    )
    message = message.replace(
        "Checked sell-loop readiness: ",
        "已檢查賣出就緒狀態：",
    )
    message = message.replace(
        "Checked sell-loop readiness.",
        "已檢查賣出就緒狀態。",
    )
    message = message.replace(
        "已檢查 guarded 下單 artifact：",
        "已檢查受保護下單產物：",
    )
    message = message.replace(
        "已檢查 sell-loop readiness：",
        "已檢查賣出就緒狀態：",
    )
    message = message.replace(
        "已輸出 workflow status，包含 ",
        "已輸出工作流狀態，包含 ",
    )
    message = message.replace(
        "已輸出 工作流狀態，包含 ",
        "已輸出工作流狀態，包含 ",
    )
    message = message.replace(" 筆 checklist 列。", " 筆清單列。")
    message = message.replace(" checklist 列。", " 清單列。")
    message = message.replace("sell-loop readiness 被擋住", "賣出就緒狀態被擋住")
    message = message.replace("guarded 視窗", "受保護下單視窗")
    message = message.replace("guarded 排程", "受保護下單排程")
    message = message.replace("guarded 執行", "受保護下單執行")
    message = message.replace("guarded 下單 artifact", "受保護下單產物")
    message = message.replace("sell-loop readiness", "賣出就緒狀態")
    message = message.replace("guarded 後檢查", "受保護下單後檢查")
    message = message.replace("workflow status", "工作流狀態")
    message = message.replace("checklist rows", "清單列")
    message = message.replace(
        "已檢查賣出就緒狀態：no_strategy_positions。",
        "已檢查賣出就緒狀態：no_strategy_positions (沒有策略部位)。",
    )
    if message.startswith("已檢查受保護下單產物：") or message.startswith("已檢查賣出就緒狀態："):
        message = message.replace(", current_step=", "，current_step=")
        message = message.replace(", next_action=", "，next_action=")
        if message.endswith("."):
            message = f"{message[:-1]}。"
    return message


def _event_rows_for_display(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    display_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        event_type = row.get("event_type", "")
        action = row.get("action", event_type)
        display_result_value = _text(row.get("display_result", "")).strip() or _text(row.get("result", "")).strip()
        display_warning_value = _text(row.get("display_warning_or_error", "")).strip() or _text(
            row.get("warning_or_error", "")
        ).strip()
        display_rows.append(
            {
                **row,
                "display_event_type": _text(row.get("display_event_type", "")).strip()
                or _event_type_display_label(event_type),
                "display_action": _text(row.get("display_action", "")).strip()
                or _event_action_display_label(action, event_type=event_type),
                "display_result": _normalize_event_message(display_result_value),
                "display_warning_or_error": _normalize_event_message(display_warning_value),
            }
        )
    return display_rows


def _overview_display_text(overview: dict[str, Any], key: str) -> str:
    display_key = f"{key}_display"
    value = _text(overview.get(display_key, "")).strip()
    if value:
        return value
    return _text(overview.get(key, ""))


def _localize_overview_markdown_bullet(line: str) -> str:
    if not line.startswith("- "):
        return line
    for key, label in OVERVIEW_BULLET_LABELS.items():
        prefix = f"- {key}:"
        if line.startswith(prefix):
            return line.replace(prefix, f"- {label}:", 1)
    return line


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "section"


def _table_rows_to_markdown(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> list[str]:
    header = "| " + " | ".join(label for _, label in columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| " + " | ".join(_text(row.get(key, "")).replace("\n", " ") for key, _ in columns) + " |"
        for row in rows
    ]
    return [header, divider, *body]


def _render_markdown_section(title: str, rows: list[dict[str, Any]], columns: list[tuple[str, str]], empty: str) -> list[str]:
    lines = [f"## {title}", ""]
    if not rows:
        lines.append(f"- {empty}")
        lines.append("")
        return lines
    lines.extend(_table_rows_to_markdown(rows, columns))
    lines.append("")
    return lines


def _render_table_html(
    title: str,
    rows: list[dict[str, Any]],
    columns: list[tuple[str, str]],
    empty: str,
    *,
    section_id: str | None = None,
) -> str:
    resolved_section_id = section_id or _slugify(title)
    if not rows:
        return (
            f"<section class='panel' id='{escape(resolved_section_id)}'><div class='panel-head'><h2>{escape(title)}</h2></div>"
            f"<p class='empty'>{escape(empty)}</p></section>"
        )

    head = "".join(
        f"<th><button class='sort-btn' type='button' data-index='{index}'>{escape(label)}</button></th>"
        for index, (_, label) in enumerate(columns)
    )
    body = []
    for row in rows:
        cells = "".join(f"<td>{escape(_display_bool(row.get(key, '')))}</td>" for key, _ in columns)
        body.append(f"<tr>{cells}</tr>")
    return (
        f"<section class='panel' id='{escape(resolved_section_id)}'>"
        f"<div class='panel-head'><h2>{escape(title)}</h2>"
        "<div class='panel-tools'>"
        f"<label class='search-wrap'><span>搜尋</span><input class='table-search' type='search' placeholder='搜尋這個區塊...' aria-label='搜尋 {escape(title)}' /></label>"
        "<span class='table-count'></span>"
        "</div></div>"
        "<div class='table-wrap'><table class='sortable-table'>"
        f"<thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"
        f"<p class='empty filtered-empty' hidden>{escape(empty)}</p></div></section>"
    )


def _render_list_panel(title: str, items: list[str], *, empty: str, section_id: str | None = None) -> str:
    resolved_section_id = section_id or _slugify(title)
    if not items:
        return (
            f"<section class='panel' id='{escape(resolved_section_id)}'><div class='panel-head'><h2>{escape(title)}</h2></div>"
            f"<p class='empty'>{escape(empty)}</p></section>"
        )
    body = "".join(f"<li>{escape(_normalize_display_text(item))}</li>" for item in items)
    return (
        f"<section class='panel' id='{escape(resolved_section_id)}'><div class='panel-head'><h2>{escape(title)}</h2></div>"
        f"<ul class='plain-list'>{body}</ul></section>"
    )


def _series_svg(chart: dict[str, Any], *, title: str, section_id: str | None = None) -> str:
    resolved_section_id = section_id or _slugify(title)
    series = chart.get("series", [])
    x_labels = chart.get("x_labels", [])
    if not series:
        return (
            f"<section class='panel' id='{escape(resolved_section_id)}'><div class='panel-head'><h2>{escape(title)}</h2></div>"
            "<p class='empty'>尚無圖表資料。</p></section>"
        )

    width = 760
    height = 240
    padding = 28
    values = [float(value) for line in series for value in line.get("values", [])]
    if not values:
        return (
            f"<section class='panel' id='{escape(resolved_section_id)}'><div class='panel-head'><h2>{escape(title)}</h2></div>"
            "<p class='empty'>尚無圖表資料。</p></section>"
        )

    min_value = min(values)
    max_value = max(values)
    span = max(max_value - min_value, 1e-9)
    point_count = max(len(x_labels), max(len(line.get("values", [])) for line in series))
    x_count = max(point_count - 1, 1)

    def to_points(values_for_series: list[Any]) -> str:
        points: list[str] = []
        for index, raw in enumerate(values_for_series):
            value = float(raw)
            x = padding + ((width - padding * 2) * index / x_count)
            y = height - padding - ((height - padding * 2) * (value - min_value) / span)
            points.append(f"{x:.2f},{y:.2f}")
        return " ".join(points)

    grid = []
    for step in range(5):
        y = padding + (height - padding * 2) * step / 4
        grid.append(f"<line x1='{padding}' y1='{y:.2f}' x2='{width - padding}' y2='{y:.2f}' class='grid' />")

    polylines = []
    legend = []
    palette = ["#244c5a", "#b85c38", "#5a7d4d", "#9a4b6a"]
    for index, line in enumerate(series):
        color = line.get("color") or palette[index % len(palette)]
        label = line.get("label", f"Series {index + 1}")
        polylines.append(
            f"<polyline points='{to_points(line.get('values', []))}' fill='none' stroke='{escape(color)}' stroke-width='3' />"
        )
        legend.append(f"<span><i style='background:{escape(color)}'></i>{escape(label)}</span>")

    x_axis_labels = []
    if x_labels:
        step = max(len(x_labels) // 4, 1)
        for index, label in enumerate(x_labels):
            if index % step != 0 and index != len(x_labels) - 1:
                continue
            x = padding + ((width - padding * 2) * index / x_count)
            x_axis_labels.append(f"<text x='{x:.2f}' y='{height - 6}' class='axis'>{escape(label)}</text>")

    return (
        f"<section class='panel chart-panel' id='{escape(resolved_section_id)}'><div class='panel-head'><h2>{escape(title)}</h2></div>"
        f"<div class='legend'>{''.join(legend)}</div>"
        f"<svg viewBox='0 0 {width} {height}' class='chart'>{''.join(grid)}{''.join(polylines)}{''.join(x_axis_labels)}</svg>"
        "</section>"
    )


def _render_cards(report: dict[str, Any]) -> str:
    overview = report.get("overview", {})
    cards = [
        ("週別", report.get("week_id", "")),
        ("交易日", report.get("trade_date", "")),
        ("最後更新", overview.get("last_update_time", "")),
        ("模式", _mapped_with_display_label(report.get("mode", ""), MODE_DISPLAY_LABELS)),
        ("選股來源", _mapped_with_display_label(report.get("provider_name", ""), PROVIDER_DISPLAY_LABELS)),
        ("今日狀態", _status_with_display_label(overview.get("today_status", ""))),
        ("每週預算", _money(overview.get("weekly_budget", 0))),
        ("硬上限", _money(overview.get("hard_budget", 0))),
        ("已用資金", _money(overview.get("used_cash", 0))),
        ("剩餘資金", _money(overview.get("remaining_cash", 0))),
        ("目前權益", _money(overview.get("current_equity", 0))),
        ("策略報酬率", _pct(overview.get("strategy_return", 0))),
    ]
    if _text(overview.get("selection_source_status", "")):
        cards.append(("選股來源狀態", _status_with_display_label(overview.get("selection_source_status", ""))))
    if _text(overview.get("today_status_note", "")):
        cards.append(("今日狀態說明", _overview_display_text(overview, "today_status_note")))
    if _text(overview.get("workflow_completed_steps", "")) or _text(overview.get("workflow_pending_steps", "")) or _text(overview.get("workflow_closed_steps", "")):
        cards.append(("Workflow 完成", overview.get("workflow_completed_steps", "")))
        cards.append(("Workflow 待辦", overview.get("workflow_pending_steps", "")))
        cards.append(("Workflow 關閉", overview.get("workflow_closed_steps", "")))
    if _text(overview.get("selection_source_note", "")):
        cards.append(("選股來源說明", _overview_display_text(overview, "selection_source_note")))
    if _text(overview.get("selection_source_path", "")):
        cards.append(("選股來源路徑", overview.get("selection_source_path", "")))
    if _text(overview.get("selection_source_last_modified", "")):
        cards.append(("選股來源時間", overview.get("selection_source_last_modified", "")))
    if _text(overview.get("dashboard_refresh_status", "")):
        cards.append(("最近刷新狀態", _status_with_display_label(overview.get("dashboard_refresh_status", ""))))
    if _text(overview.get("dashboard_refresh_steps", "")):
        cards.append(("最近刷新步驟", _overview_display_text(overview, "dashboard_refresh_steps")))
    if _text(overview.get("dashboard_refresh_note", "")):
        cards.append(("最近刷新說明", _overview_display_text(overview, "dashboard_refresh_note")))
    if _text(overview.get("dashboard_refresh_trigger_status", "")):
        cards.append(("刷新觸發狀態", _status_with_display_label(overview.get("dashboard_refresh_trigger_status", ""))))
    if _text(overview.get("dashboard_refresh_trigger_artifacts", "")):
        cards.append(("刷新觸發檔案", overview.get("dashboard_refresh_trigger_artifacts", "")))
    if _text(overview.get("dashboard_refresh_trigger_note", "")):
        cards.append(("刷新觸發說明", _overview_display_text(overview, "dashboard_refresh_trigger_note")))
    if _text(overview.get("dashboard_last_materialization_status", "")):
        cards.append(("最近物化刷新狀態", _status_with_display_label(overview.get("dashboard_last_materialization_status", ""))))
    if _text(overview.get("dashboard_last_materialization_steps", "")):
        cards.append(("最近物化刷新步驟", _overview_display_text(overview, "dashboard_last_materialization_steps")))
    if _text(overview.get("dashboard_last_materialization_note", "")):
        cards.append(("最近物化刷新說明", _overview_display_text(overview, "dashboard_last_materialization_note")))
    if _text(overview.get("dashboard_last_materialization_trigger_status", "")):
        cards.append(("最近物化觸發狀態", _status_with_display_label(overview.get("dashboard_last_materialization_trigger_status", ""))))
    if _text(overview.get("dashboard_last_materialization_trigger_artifacts", "")):
        cards.append(("最近物化觸發檔案", overview.get("dashboard_last_materialization_trigger_artifacts", "")))
    if _text(overview.get("dashboard_last_materialization_trigger_note", "")):
        cards.append(("最近物化觸發說明", _overview_display_text(overview, "dashboard_last_materialization_trigger_note")))
    if _text(overview.get("weekly_settlement_status", "")):
        cards.append(("週結算狀態", _status_with_display_label(overview.get("weekly_settlement_status", ""))))
    if _text(overview.get("weekly_settlement_note", "")):
        cards.append(("週結算說明", _overview_display_text(overview, "weekly_settlement_note")))
    if _text(overview.get("weekly_settlement_artifacts", "")):
        cards.append(("週結算產物", overview.get("weekly_settlement_artifacts", "")))
    if _text(overview.get("weekly_settlement_next_action_note", "")):
        cards.append(("週結算下一步", _overview_display_text(overview, "weekly_settlement_next_action_note")))
    if _text(overview.get("selection_source_carry_forward_status", "")):
        cards.append(("來源延用狀態", _status_with_display_label(overview.get("selection_source_carry_forward_status", ""))))
    if _text(overview.get("selection_source_carry_forward_note", "")):
        cards.append(("來源延用說明", _overview_display_text(overview, "selection_source_carry_forward_note")))
    if _text(overview.get("selection_materialization_status", "")):
        cards.append(("本地展開狀態", _status_with_display_label(overview.get("selection_materialization_status", ""))))
    if _text(overview.get("selection_materialization_missing_artifacts", "")):
        cards.append(("缺少本地產物", overview.get("selection_materialization_missing_artifacts", "")))
    if _text(overview.get("selection_materialization_note", "")):
        cards.append(("本地展開說明", _overview_display_text(overview, "selection_materialization_note")))
    if _text(overview.get("selection_materialization_next_action_note", "")):
        cards.append(("本地展開下一步", _overview_display_text(overview, "selection_materialization_next_action_note")))
    if _text(overview.get("today_ordering_status", "")):
        cards.append(("今日下單狀態", _status_with_display_label(overview.get("today_ordering_status", ""))))
    if _text(overview.get("today_ordering_note", "")):
        cards.append(("今日下單說明", _overview_display_text(overview, "today_ordering_note")))
    if _text(overview.get("today_ordering_conflict_status", "")):
        cards.append(("今日矛盾狀態", _status_with_display_label(overview.get("today_ordering_conflict_status", ""))))
    if _text(overview.get("today_ordering_conflict_note", "")):
        cards.append(("今日矛盾說明", _overview_display_text(overview, "today_ordering_conflict_note")))
    if _text(overview.get("today_ordering_conflict_resolution_status", "")):
        cards.append(("矛盾解法狀態", _status_with_display_label(overview.get("today_ordering_conflict_resolution_status", ""))))
    if _text(overview.get("today_ordering_conflict_resolution_note", "")):
        cards.append(("矛盾解法說明", _overview_display_text(overview, "today_ordering_conflict_resolution_note")))
    if _text(overview.get("today_new_order_submission_status", "")):
        cards.append(("今日可否新送單", _status_with_display_label(overview.get("today_new_order_submission_status", ""))))
    if _text(overview.get("today_new_order_submission_note", "")):
        cards.append(("今日送單結論", _overview_display_text(overview, "today_new_order_submission_note")))
    if _text(overview.get("position_data_quality", "")):
        cards.append(("部位資料", _mapped_with_display_label(overview.get("position_data_quality", ""), POSITION_DATA_QUALITY_LABELS)))
    if _text(overview.get("positions_source_date", "")):
        cards.append(("部位來源日", overview.get("positions_source_date", "")))
    if _text(overview.get("guarded_post_check_status", "")):
        cards.append(("受保護下單後檢查", _status_with_display_label(overview.get("guarded_post_check_status", ""))))
    if _text(overview.get("guarded_post_check_recommendation", "")):
        cards.append(("受保護下單建議", _action_with_display_label(overview.get("guarded_post_check_recommendation", ""))))
    if _text(overview.get("guarded_post_check_effective_recommendation", "")):
        cards.append(("受保護下單目前建議", _status_with_display_label(overview.get("guarded_post_check_effective_recommendation", ""))))
    if _text(overview.get("guarded_post_check_effective_recommendation_note", "")):
        cards.append(("受保護下單解讀", _overview_display_text(overview, "guarded_post_check_effective_recommendation_note")))
    if _text(overview.get("guarded_post_check_next_run_guard_status", "")):
        cards.append(("受保護下單下次排程", _status_with_display_label(overview.get("guarded_post_check_next_run_guard_status", ""))))
    if _text(overview.get("guarded_post_check_next_run_guard_message", "")):
        cards.append(("受保護下單排程說明", _overview_display_text(overview, "guarded_post_check_next_run_guard_message")))
    if _text(overview.get("guarded_post_check_config_timing_status", "")):
        cards.append(("受保護下單設定時序", _status_with_display_label(overview.get("guarded_post_check_config_timing_status", ""))))
    if _text(overview.get("guarded_post_check_config_timing_message", "")):
        cards.append(("受保護下單設定時序說明", _overview_display_text(overview, "guarded_post_check_config_timing_message")))
    if _text(overview.get("guarded_post_check_config_last_modified", "")):
        cards.append(("設定檔更新時間", overview.get("guarded_post_check_config_last_modified", "")))
    if _text(overview.get("guarded_post_check_task_recorded_at", "")):
        cards.append(("受保護下單記錄時間", overview.get("guarded_post_check_task_recorded_at", "")))
    if _text(overview.get("sell_loop_readiness_blocking_reason", "")):
        cards.append(("賣出就緒", _status_with_display_label(overview.get("sell_loop_readiness_blocking_reason", ""))))
    if _text(overview.get("sell_loop_readiness_next_action", "")):
        cards.append(("賣出下一步", _status_with_display_label(overview.get("sell_loop_readiness_next_action", ""))))
    if _text(overview.get("sell_loop_readiness_next_action_note", "")):
        cards.append(("賣出下一步說明", _overview_display_text(overview, "sell_loop_readiness_next_action_note")))
    if _text(overview.get("sell_loop_readiness_post_guarded_config_timing_status", "")):
        cards.append(("賣出參考受保護下單時序", _status_with_display_label(overview.get("sell_loop_readiness_post_guarded_config_timing_status", ""))))
    if _text(overview.get("sell_loop_readiness_post_guarded_config_timing_message", "")):
        cards.append(
            ("賣出參考受保護下單時序說明", _overview_display_text(overview, "sell_loop_readiness_post_guarded_config_timing_message"))
        )
    if _text(overview.get("sell_loop_readiness_post_guarded_config_last_modified", "")):
        cards.append(("賣出參考設定檔時間", overview.get("sell_loop_readiness_post_guarded_config_last_modified", "")))
    if _text(overview.get("sell_loop_readiness_post_guarded_task_recorded_at", "")):
        cards.append(("賣出參考受保護下單記錄時間", overview.get("sell_loop_readiness_post_guarded_task_recorded_at", "")))
    if float(overview.get("ambiguous_fill_guard_count", 0) or 0) > 0:
        cards.append(("歧義 Guard", overview.get("ambiguous_fill_guard_count", 0)))
    if float(overview.get("excluded_position_guard_count", 0) or 0) > 0:
        cards.append(("排除 Guard", overview.get("excluded_position_guard_count", 0)))
    if float(overview.get("broker_underheld_guard_count", 0) or 0) > 0:
        cards.append(("缺股 Guard", overview.get("broker_underheld_guard_count", 0)))
    body = "".join(
        f"<article class='card'><div class='label'>{escape(label)}</div><div class='value'>{escape(_text(value))}</div></article>"
        for label, value in cards
    )
    return f"<section class='card-grid'>{body}</section>"


def _render_basket_summary(report: dict[str, Any]) -> str:
    basket = report.get("basket_summary", {})
    rows = [
        {"metric": "整包範圍", "value": _text(basket.get("basket_scope", ""))},
        {"metric": "整包標籤", "value": _text(basket.get("basket_tags", ""))},
        {"metric": "整包市值", "value": _money(basket.get("basket_market_value", 0))},
        {"metric": "整包未實現損益", "value": _money(basket.get("basket_unrealized_pnl", 0))},
        {"metric": "整包未實現報酬率", "value": _pct(basket.get("basket_unrealized_pnl_pct", 0))},
        {"metric": "保守出清獲利", "value": _money(basket.get("basket_conservative_profit", 0))},
        {"metric": "整包門檻", "value": _money(basket.get("basket_threshold", 0))},
        {"metric": "整包建議", "value": _text(basket.get("basket_recommendation", ""))},
        {"metric": "虧損股佔比", "value": _pct(basket.get("loser_loss_ratio", 0))},
    ]
    return _render_table_html(
        "整包分析",
        rows,
        [("metric", "項目"), ("value", "數值")],
        "尚無整包分析資料。",
        section_id="basket-summary",
    )


def _render_warning_block(messages: list[str], *, title: str, section_id: str | None = None) -> str:
    resolved_section_id = section_id or _slugify(title)
    if not messages:
        return ""
    items = "".join(f"<li>{escape(_normalize_display_text(item))}</li>" for item in messages)
    return f"<section class='panel warning' id='{escape(resolved_section_id)}'><div class='panel-head'><h2>{escape(title)}</h2></div><ul>{items}</ul></section>"


def _render_section_nav(items: list[tuple[str, str]]) -> str:
    if not items:
        return ""
    body = "".join(
        f"<a class='nav-chip' href='#{escape(section_id)}'>{escape(label)}</a>"
        for section_id, label in items
    )
    return f"<nav class='page-nav' aria-label='區塊導覽'>{body}</nav>"


def _render_page_shell(*, title: str, subtitle: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape(title)}</title>
  <style>
    :root {{
      --bg: #f3f0e8;
      --panel: #fffdf7;
      --ink: #1e2b2f;
      --muted: #65767a;
      --line: #d9d0bf;
      --accent: #244c5a;
      --accent-2: #b85c38;
      --ok: #2f6a44;
      --warn: #8d5b2d;
      --shadow: 0 12px 24px rgba(46, 49, 44, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background:
        radial-gradient(circle at top right, rgba(184,92,56,0.12), transparent 32%),
        linear-gradient(180deg, #f7f4ec 0%, var(--bg) 100%);
      color: var(--ink);
      font-family: "Segoe UI", "Noto Sans TC", sans-serif;
      line-height: 1.5;
    }}
    .shell {{
      width: min(1280px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 28px 0 56px;
    }}
    .hero {{
      padding: 28px;
      border: 1px solid var(--line);
      background: linear-gradient(135deg, rgba(36,76,90,0.94), rgba(77,117,124,0.92));
      color: #f8f5ee;
      border-radius: 24px;
      box-shadow: var(--shadow);
      margin-bottom: 24px;
    }}
    .hero h1 {{
      margin: 0 0 6px;
      font-size: clamp(28px, 4vw, 44px);
      letter-spacing: 0.02em;
    }}
    .hero p {{
      margin: 0;
      color: rgba(248,245,238,0.86);
    }}
    .card-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 14px;
      margin-bottom: 24px;
    }}
    .card, .panel {{
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 18px;
      box-shadow: var(--shadow);
    }}
    .card {{
      padding: 16px;
      min-height: 108px;
    }}
    .card .label {{
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 12px;
    }}
    .card .value {{
      font-size: 24px;
      font-weight: 700;
      color: var(--accent);
      word-break: break-word;
    }}
    .layout {{
      display: grid;
      gap: 18px;
    }}
    .page-nav {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin: 0 0 20px;
    }}
    .nav-chip {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 10px 14px;
      border-radius: 999px;
      text-decoration: none;
      color: var(--accent);
      background: rgba(36, 76, 90, 0.08);
      border: 1px solid rgba(36, 76, 90, 0.12);
      font-size: 14px;
      font-weight: 600;
    }}
    .panel {{
      padding: 18px 18px 14px;
      scroll-margin-top: 16px;
    }}
    .panel-head {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 12px;
    }}
    .panel h2 {{
      margin: 0;
      font-size: 20px;
    }}
    .panel-tools {{
      display: inline-flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
      margin-left: auto;
    }}
    .search-wrap {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      font-size: 13px;
    }}
    .table-search {{
      min-width: 180px;
      padding: 9px 12px;
      border-radius: 999px;
      border: 1px solid #ddd3c2;
      background: #fffefb;
      color: var(--ink);
    }}
    .table-count {{
      font-size: 13px;
      color: var(--muted);
      white-space: nowrap;
    }}
    .table-wrap {{
      overflow-x: auto;
    }}
    .sort-btn {{
      appearance: none;
      background: none;
      border: 0;
      color: inherit;
      cursor: pointer;
      font: inherit;
      font-weight: 600;
      padding: 0;
      text-align: left;
    }}
    .sort-btn::after {{
      content: " ↕";
      color: #9aa7aa;
      font-size: 12px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }}
    th, td {{
      padding: 10px 12px;
      border-bottom: 1px solid #ece5d6;
      text-align: left;
      vertical-align: top;
    }}
    th {{
      color: var(--muted);
      font-weight: 600;
      white-space: nowrap;
    }}
    .chart-panel .legend {{
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      margin-bottom: 10px;
      color: var(--muted);
      font-size: 13px;
    }}
    .legend span {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }}
    .legend i {{
      width: 12px;
      height: 12px;
      border-radius: 999px;
      display: inline-block;
    }}
    .chart {{
      width: 100%;
      border-radius: 14px;
      background: #fcfaf4;
      border: 1px solid #ebe3d2;
    }}
    .grid {{
      stroke: #e6ddcb;
      stroke-width: 1;
    }}
    .axis {{
      fill: var(--muted);
      font-size: 10px;
      text-anchor: middle;
    }}
    .empty {{
      color: var(--muted);
      margin: 4px 0 0;
    }}
    .filtered-empty {{
      margin-top: 14px;
    }}
    .warning {{
      border-color: rgba(141, 91, 45, 0.35);
      background: #fff8ef;
    }}
    .warning ul,
    .plain-list {{
      margin: 0;
      padding-left: 18px;
    }}
    @media (max-width: 720px) {{
      .shell {{
        width: min(100vw - 20px, 100%);
      }}
      .hero {{
        padding: 22px;
      }}
      .panel-tools {{
        width: 100%;
      }}
      .table-search {{
        min-width: 0;
        width: 100%;
      }}
      th, td {{
        padding: 8px 10px;
      }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <h1>{escape(title)}</h1>
      <p>{escape(subtitle)}</p>
    </section>
    {body}
  </main>
  <script>
    (() => {{
      const tables = document.querySelectorAll('.sortable-table');
      tables.forEach((table) => {{
        const headers = table.querySelectorAll('.sort-btn');
        headers.forEach((button) => {{
          button.addEventListener('click', () => {{
            const tbody = table.querySelector('tbody');
            if (!tbody) return;
            const columnIndex = Number(button.dataset.index || 0);
            const currentDirection = button.dataset.direction === 'asc' ? 'asc' : 'desc';
            const nextDirection = currentDirection === 'asc' ? 'desc' : 'asc';
            headers.forEach((header) => {{
              if (header !== button) {{
                header.dataset.direction = '';
              }}
            }});
            button.dataset.direction = nextDirection;
            const rows = Array.from(tbody.querySelectorAll('tr'));
            rows.sort((rowA, rowB) => {{
              const a = rowA.children[columnIndex]?.textContent?.trim() || '';
              const b = rowB.children[columnIndex]?.textContent?.trim() || '';
              const aNum = Number(a.replace(/,/g, '').replace(/%/g, ''));
              const bNum = Number(b.replace(/,/g, '').replace(/%/g, ''));
              const bothNumeric = !Number.isNaN(aNum) && !Number.isNaN(bNum) && a !== '' && b !== '';
              let result = 0;
              if (bothNumeric) {{
                result = aNum - bNum;
              }} else {{
                result = a.localeCompare(b, 'zh-Hant');
              }}
              return nextDirection === 'asc' ? result : -result;
            }});
            rows.forEach((row) => tbody.appendChild(row));
          }});
        }});
      }});

      const filterPanels = document.querySelectorAll('.panel');
      filterPanels.forEach((panel) => {{
        const input = panel.querySelector('.table-search');
        const tbody = panel.querySelector('tbody');
        const count = panel.querySelector('.table-count');
        const empty = panel.querySelector('.filtered-empty');
        if (!input || !tbody || !count) {{
          return;
        }}

        const rows = Array.from(tbody.querySelectorAll('tr'));
        const total = rows.length;

        const applyFilter = () => {{
          const query = input.value.trim().toLowerCase();
          let visible = 0;
          rows.forEach((row) => {{
            const haystack = (row.textContent || '').toLowerCase();
            const match = !query || haystack.includes(query);
            row.hidden = !match;
            if (match) {{
              visible += 1;
            }}
          }});
          count.textContent = `顯示 ${{visible}} / ${{total}}`;
          if (empty) {{
            empty.hidden = visible !== 0;
          }}
        }};

        input.addEventListener('input', applyFilter);
        applyFilter();
      }});
    }})();
  </script>
</body>
</html>
"""


def _daily_markdown(report: dict[str, Any]) -> str:
    overview = report.get("overview", {})
    event_rows = _event_rows_for_display(list(report.get("events", [])))
    lines = [
        f"# {report.get('trade_date', '')} 自動交易日報",
        "",
        "## 總覽",
        f"- week_id: {report.get('week_id', '')}",
        f"- run_id: {report.get('run_id', '')}",
        f"- mode: {_mapped_with_inline_label(report.get('mode', ''), MODE_DISPLAY_LABELS)}",
        f"- active_selection_provider: {_mapped_with_inline_label(report.get('provider_name', ''), PROVIDER_DISPLAY_LABELS)}",
        f"- weekly_budget: {_money(overview.get('weekly_budget', 0))}",
        f"- hard_budget: {_money(overview.get('hard_budget', 0))}",
        f"- used_cash: {_money(overview.get('used_cash', 0))}",
        f"- remaining_cash: {_money(overview.get('remaining_cash', 0))}",
        f"- current_equity: {_money(overview.get('current_equity', 0))}",
        f"- strategy_pnl_after_fee_tax: {_money(overview.get('strategy_pnl_after_fee_tax', 0))}",
        f"- strategy_return: {_pct(overview.get('strategy_return', 0))}",
        f"- today_status: {_status_with_inline_label(overview.get('today_status', ''))}",
        f"- today_status_note: {_overview_display_text(overview, 'today_status_note')}",
        f"- workflow_completed_steps: {_text(overview.get('workflow_completed_steps', ''))}",
        f"- workflow_pending_steps: {_text(overview.get('workflow_pending_steps', ''))}",
        f"- workflow_closed_steps: {_text(overview.get('workflow_closed_steps', ''))}",
        f"- last_update_time: {_text(overview.get('last_update_time', ''))}",
        f"- selection_source_path: {_text(overview.get('selection_source_path', ''))}",
        f"- selection_source_last_modified: {_text(overview.get('selection_source_last_modified', ''))}",
        f"- selection_source_status: {_status_with_inline_label(overview.get('selection_source_status', ''))}",
        f"- selection_source_note: {_overview_display_text(overview, 'selection_source_note')}",
        f"- dashboard_refresh_status: {_status_with_inline_label(overview.get('dashboard_refresh_status', ''))}",
        f"- dashboard_refresh_steps: {_overview_display_text(overview, 'dashboard_refresh_steps')}",
        f"- dashboard_refresh_note: {_overview_display_text(overview, 'dashboard_refresh_note')}",
        f"- dashboard_refresh_trigger_status: {_status_with_inline_label(overview.get('dashboard_refresh_trigger_status', ''))}",
        f"- dashboard_refresh_trigger_artifacts: {_text(overview.get('dashboard_refresh_trigger_artifacts', ''))}",
        f"- dashboard_refresh_trigger_note: {_overview_display_text(overview, 'dashboard_refresh_trigger_note')}",
        f"- dashboard_last_materialization_status: {_status_with_inline_label(overview.get('dashboard_last_materialization_status', ''))}",
        f"- dashboard_last_materialization_steps: {_overview_display_text(overview, 'dashboard_last_materialization_steps')}",
        f"- dashboard_last_materialization_note: {_overview_display_text(overview, 'dashboard_last_materialization_note')}",
        f"- dashboard_last_materialization_trigger_status: {_status_with_inline_label(overview.get('dashboard_last_materialization_trigger_status', ''))}",
        f"- dashboard_last_materialization_trigger_artifacts: {_text(overview.get('dashboard_last_materialization_trigger_artifacts', ''))}",
        f"- dashboard_last_materialization_trigger_note: {_overview_display_text(overview, 'dashboard_last_materialization_trigger_note')}",
        f"- weekly_settlement_open: {_text(overview.get('weekly_settlement_open', ''))}",
        f"- weekly_settlement_status: {_status_with_inline_label(overview.get('weekly_settlement_status', ''))}",
        f"- weekly_settlement_artifacts: {_text(overview.get('weekly_settlement_artifacts', ''))}",
        f"- weekly_settlement_note: {_overview_display_text(overview, 'weekly_settlement_note')}",
        f"- weekly_settlement_next_action: {_action_with_inline_label(overview.get('weekly_settlement_next_action', ''))}",
        f"- weekly_settlement_next_action_note: {_overview_display_text(overview, 'weekly_settlement_next_action_note')}",
        f"- selection_source_carry_forward_open: {_text(overview.get('selection_source_carry_forward_open', ''))}",
        f"- selection_source_carry_forward_status: {_status_with_inline_label(overview.get('selection_source_carry_forward_status', ''))}",
        f"- selection_source_carry_forward_next_trade_day: {_text(overview.get('selection_source_carry_forward_next_trade_day', ''))}",
        f"- selection_source_carry_forward_note: {_overview_display_text(overview, 'selection_source_carry_forward_note')}",
        f"- selection_materialization_open: {_text(overview.get('selection_materialization_open', ''))}",
        f"- selection_materialization_status: {_status_with_inline_label(overview.get('selection_materialization_status', ''))}",
        f"- selection_materialization_missing_artifacts: {_text(overview.get('selection_materialization_missing_artifacts', ''))}",
        f"- selection_materialization_note: {_overview_display_text(overview, 'selection_materialization_note')}",
        f"- selection_materialization_next_action: {_action_with_inline_label(overview.get('selection_materialization_next_action', ''))}",
        f"- selection_materialization_next_action_note: {_overview_display_text(overview, 'selection_materialization_next_action_note')}",
        f"- today_ordering_status: {_status_with_inline_label(overview.get('today_ordering_status', ''))}",
        f"- today_ordering_note: {_overview_display_text(overview, 'today_ordering_note')}",
        f"- today_ordering_conflict_status: {_status_with_inline_label(overview.get('today_ordering_conflict_status', ''))}",
        f"- today_ordering_conflict_note: {_overview_display_text(overview, 'today_ordering_conflict_note')}",
        f"- today_ordering_conflict_resolution_status: {_status_with_inline_label(overview.get('today_ordering_conflict_resolution_status', ''))}",
        f"- today_ordering_conflict_resolution_action: {_action_with_inline_label(overview.get('today_ordering_conflict_resolution_action', ''))}",
        f"- today_ordering_conflict_resolution_note: {_overview_display_text(overview, 'today_ordering_conflict_resolution_note')}",
        f"- today_new_order_submission_open: {_text(overview.get('today_new_order_submission_open', ''))}",
        f"- today_new_order_submission_status: {_status_with_inline_label(overview.get('today_new_order_submission_status', ''))}",
        f"- today_new_order_submission_note: {_overview_display_text(overview, 'today_new_order_submission_note')}",
        f"- position_data_quality: {_mapped_with_inline_label(overview.get('position_data_quality', ''), POSITION_DATA_QUALITY_LABELS)}",
        f"- positions_source_date: {_text(overview.get('positions_source_date', ''))}",
        f"- guarded_post_check_status: {_status_with_inline_label(overview.get('guarded_post_check_status', ''))}",
        f"- guarded_post_check_recommendation: {_action_with_inline_label(overview.get('guarded_post_check_recommendation', ''))}",
        f"- guarded_post_check_effective_recommendation: {_status_with_inline_label(overview.get('guarded_post_check_effective_recommendation', ''))}",
        f"- guarded_post_check_effective_recommendation_note: {_overview_display_text(overview, 'guarded_post_check_effective_recommendation_note')}",
        f"- guarded_post_check_reconciled: {_text(overview.get('guarded_post_check_reconciled', ''))}",
        f"- guarded_post_check_fills_count: {_text(overview.get('guarded_post_check_fills_count', ''))}",
        f"- guarded_post_check_positions_count: {_text(overview.get('guarded_post_check_positions_count', ''))}",
        f"- guarded_post_check_next_run_guard_status: {_status_with_inline_label(overview.get('guarded_post_check_next_run_guard_status', ''))}",
        f"- guarded_post_check_next_run_guard_message: {_overview_display_text(overview, 'guarded_post_check_next_run_guard_message')}",
        f"- guarded_post_check_config_timing_status: {_status_with_inline_label(overview.get('guarded_post_check_config_timing_status', ''))}",
        f"- guarded_post_check_config_timing_message: {_overview_display_text(overview, 'guarded_post_check_config_timing_message')}",
        f"- guarded_post_check_config_path: {_text(overview.get('guarded_post_check_config_path', ''))}",
        f"- guarded_post_check_config_last_modified: {_text(overview.get('guarded_post_check_config_last_modified', ''))}",
        f"- guarded_post_check_task_recorded_at: {_text(overview.get('guarded_post_check_task_recorded_at', ''))}",
        f"- sell_loop_readiness_blocking_reason: {_status_with_inline_label(overview.get('sell_loop_readiness_blocking_reason', ''))}",
        f"- sell_loop_readiness_next_action: {_action_with_inline_label(overview.get('sell_loop_readiness_next_action', ''))}",
        f"- sell_loop_readiness_next_action_note: {_overview_display_text(overview, 'sell_loop_readiness_next_action_note')}",
        f"- sell_loop_readiness_positions_ready: {_text(overview.get('sell_loop_readiness_positions_ready', ''))}",
        f"- sell_loop_readiness_positions_count: {_text(overview.get('sell_loop_readiness_positions_count', ''))}",
        f"- sell_loop_readiness_positions_source_date: {_text(overview.get('sell_loop_readiness_positions_source_date', ''))}",
        f"- sell_loop_readiness_post_guarded_effective_recommendation: {_action_with_inline_label(overview.get('sell_loop_readiness_post_guarded_effective_recommendation', ''))}",
        f"- sell_loop_readiness_post_guarded_effective_recommendation_note: {_overview_display_text(overview, 'sell_loop_readiness_post_guarded_effective_recommendation_note')}",
        f"- sell_loop_readiness_post_guarded_next_run_guard_status: {_status_with_inline_label(overview.get('sell_loop_readiness_post_guarded_next_run_guard_status', ''))}",
        f"- sell_loop_readiness_post_guarded_config_timing_status: {_status_with_inline_label(overview.get('sell_loop_readiness_post_guarded_config_timing_status', ''))}",
        f"- sell_loop_readiness_post_guarded_config_timing_message: {_overview_display_text(overview, 'sell_loop_readiness_post_guarded_config_timing_message')}",
        f"- sell_loop_readiness_post_guarded_config_path: {_text(overview.get('sell_loop_readiness_post_guarded_config_path', ''))}",
        f"- sell_loop_readiness_post_guarded_config_last_modified: {_text(overview.get('sell_loop_readiness_post_guarded_config_last_modified', ''))}",
        f"- sell_loop_readiness_post_guarded_task_recorded_at: {_text(overview.get('sell_loop_readiness_post_guarded_task_recorded_at', ''))}",
        f"- ambiguous_fill_guard_count: {_text(overview.get('ambiguous_fill_guard_count', ''))}",
        f"- excluded_position_guard_count: {_text(overview.get('excluded_position_guard_count', ''))}",
        f"- broker_underheld_guard_count: {_text(overview.get('broker_underheld_guard_count', ''))}",
        "",
    ]
    lines = [_localize_overview_markdown_bullet(line) for line in lines]
    lines.extend(
        _render_markdown_section(
            "選股來源與訂版",
            report.get("selection_rows", []),
            [
                ("stock_id", "股票代號"),
                ("stock_name", "股票名稱"),
                ("source", "來源"),
                ("source_weight", "權重"),
                ("preselect_flag", "預選"),
                ("final_flag", "訂版"),
                ("role_level", "角色層級"),
                ("theme", "主題"),
                ("model_score", "模型分數"),
                ("finalizer_score", "訂版分數"),
                ("include_reason", "保留原因"),
                ("exclude_reason", "排除原因"),
                ("provider_name_display_inline", "資料來源"),
            ],
            "尚無選股資料。",
        )
    )
    lines.extend(
        _render_markdown_section(
            "買進執行",
            report.get("buy_execution_rows", []),
            [
                ("stock_id", "股票代號"),
                ("stock_name", "股票名稱"),
                ("basket_tag", "Basket"),
                ("target_qty", "目標股數"),
                ("bought_qty", "已買"),
                ("remaining_qty", "剩餘"),
                ("active_order_id", "委託單號"),
                ("active_order_price", "委託價格"),
                ("active_order_qty", "委託股數"),
                ("order_age", "掛單時間"),
                ("current_mode_display_inline", "模式"),
                ("last_price", "現價"),
                ("bid1", "買一"),
                ("ask1", "賣一"),
                ("quote_timestamp", "報價時間"),
                ("buy_submission_gate", "提交 Gate"),
                ("tick_distance_to_target", "Tick 差距"),
                ("next_check_time", "下次檢查"),
                ("order_status_summary_display_inline", "委託狀態"),
            ],
            "尚無買進執行資料。",
        )
    )
    lines.extend(
        _render_markdown_section(
            "持倉",
            report.get("positions_rows", []),
            [
                ("stock_id", "股票代號"),
                ("stock_name", "股票名稱"),
                ("basket_tag", "Basket"),
                ("holding_qty", "持有股數"),
                ("buy_avg_price", "買進均價"),
                ("buy_total_cost", "買進成本"),
                ("current_price", "現價"),
                ("market_value", "市值"),
                ("unrealized_pnl", "未實現損益"),
                ("unrealized_pnl_pct", "未實現報酬率"),
                ("estimated_exit_value_after_fee_tax", "預估出場淨額"),
                ("breakeven_sell_price", "損平出場價"),
            ],
            "尚無持倉資料。",
        )
    )
    lines.extend(
        _render_markdown_section(
            "排除部位",
            report.get("excluded_positions_rows", []),
            [
                ("stock_id", "股票代號"),
                ("stock_name", "股票名稱"),
                ("broker_qty", "券商股數"),
                ("strategy_qty", "策略股數"),
                ("excluded_qty", "排除股數"),
                ("reason", "原因"),
            ],
            "無排除部位。",
        )
    )
    lines.extend(
        _render_markdown_section(
            "缺股風險",
            report.get("broker_underheld_rows", []),
            [
                ("stock_id", "股票代號"),
                ("stock_name", "股票名稱"),
                ("broker_qty", "券商股數"),
                ("strategy_qty", "策略股數"),
                ("missing_qty", "缺口股數"),
                ("reason", "原因"),
            ],
            "無缺股風險。",
        )
    )
    lines.extend(
        _render_markdown_section(
            "待對帳成交",
            report.get("ambiguous_fill_rows", []),
            [
                ("stock_id", "股票代號"),
                ("side", "方向"),
                ("fill_qty", "股數"),
                ("fill_price", "成交價"),
                ("fill_time", "成交時間"),
                ("broker_fill_id", "券商成交單號"),
                ("broker_custom_field", "Broker Token"),
                ("fill_assignment_status", "歸戶狀態"),
            ],
            "無待對帳成交。",
        )
    )
    lines.extend(
        _render_markdown_section(
            "賣出狀態",
            report.get("sell_rows", []),
            [
                ("stock_id", "股票代號"),
                ("basket_tag", "Basket"),
                ("can_sell_flag", "可賣"),
                ("conservative_sell_price", "保守出場價"),
                ("conservative_profit", "保守獲利"),
                ("sell_decision", "賣出決策"),
                ("sell_decision_reason", "決策原因"),
                ("basket_recommendation", "整包建議"),
                ("basket_threshold", "整包門檻"),
                ("basket_loser_loss_ratio", "虧損股佔比"),
                ("quote_timestamp", "報價時間"),
                ("sell_submission_gate", "提交 Gate"),
                ("sell_order_price", "委託出場價"),
                ("sell_order_status", "委託狀態"),
                ("actual_fill_avg_price", "實際成交均價"),
                ("sold_qty", "已賣"),
                ("remaining_qty", "剩餘"),
                ("realized_pnl", "已實現損益"),
                ("sell_pnl_source", "PnL 來源"),
            ],
            "尚無賣出判斷資料。",
        )
    )
    lines.extend(
        _render_markdown_section(
            "整包分析",
            [
                {
                    "basket_market_value": _money(report.get("basket_summary", {}).get("basket_market_value", 0)),
                    "basket_unrealized_pnl": _money(report.get("basket_summary", {}).get("basket_unrealized_pnl", 0)),
                    "basket_unrealized_pnl_pct": _pct(report.get("basket_summary", {}).get("basket_unrealized_pnl_pct", 0)),
                    "basket_conservative_profit": _money(report.get("basket_summary", {}).get("basket_conservative_profit", 0)),
                    "basket_threshold": _money(report.get("basket_summary", {}).get("basket_threshold", 0)),
                    "basket_recommendation": _text(report.get("basket_summary", {}).get("basket_recommendation", "")),
                    "loser_loss_ratio": _pct(report.get("basket_summary", {}).get("loser_loss_ratio", 0)),
                }
            ],
            [
                ("basket_market_value", "整包市值"),
                ("basket_unrealized_pnl", "整包未實現損益"),
                ("basket_unrealized_pnl_pct", "整包未實現報酬率"),
                ("basket_conservative_profit", "整包保守獲利"),
                ("basket_threshold", "整包門檻"),
                ("basket_recommendation", "整包建議"),
                ("loser_loss_ratio", "虧損股佔比"),
            ],
            "尚無整包分析資料。",
        )
    )
    lines.extend(
        _render_markdown_section(
            "事件紀錄",
            event_rows,
            [
                ("time", "時間"),
                ("display_event_type", "事件"),
                ("stock_id", "股票"),
                ("display_action", "動作"),
                ("price", "價格"),
                ("qty", "股數"),
                ("display_result", "結果"),
                ("display_warning_or_error", "警告 / 錯誤"),
            ],
            "尚無事件紀錄。",
        )
    )
    lines.append("## 警告 / 備註")
    lines.extend([f"- {_normalize_display_text(item)}" for item in report.get("warnings", [])] or ["- 無"])
    lines.extend(["", "## 後續動作"])
    lines.extend(
        [f"- {_normalize_display_text(item)}" for item in report.get("next_actions", [])]
        or ["- 下個交易時段前再次執行 render_report。"]
    )
    lines.append("")
    return "\n".join(lines)


def _daily_html(report: dict[str, Any], *, title: str, subtitle: str) -> str:
    event_rows = _event_rows_for_display(list(report.get("events", [])))
    section_blocks: list[tuple[str, str, str]] = []
    warnings_block = _render_warning_block(report.get("warnings", []), title="警告 / 備註", section_id="warnings-notes")
    if warnings_block:
        section_blocks.append(("warnings-notes", "警告 / 備註", warnings_block))
    section_blocks.extend(
        [
            (
                "selection-finalization",
                "選股來源與訂版",
                _render_table_html(
                    "選股來源與訂版",
                    report.get("selection_rows", []),
                    [
                        ("stock_id", "股票代號"),
                        ("stock_name", "股票名稱"),
                        ("source", "來源"),
                        ("source_weight", "權重"),
                        ("preselect_flag", "預選"),
                        ("final_flag", "訂版"),
                        ("role_level", "角色層級"),
                        ("theme", "主題"),
                        ("model_score", "模型分數"),
                        ("finalizer_score", "訂版分數"),
                        ("include_reason", "保留原因"),
                        ("exclude_reason", "排除原因"),
                        ("provider_name_display", "資料來源"),
                    ],
                    "尚無選股資料。",
                    section_id="selection-finalization",
                ),
            ),
            (
                "buy-execution",
                "買進執行",
                _render_table_html(
                    "買進執行",
                    report.get("buy_execution_rows", []),
                    [
                        ("stock_id", "股票代號"),
                        ("stock_name", "股票名稱"),
                        ("basket_tag", "Basket"),
                        ("target_qty", "目標股數"),
                        ("bought_qty", "已買"),
                        ("remaining_qty", "剩餘"),
                        ("active_order_id", "委託單號"),
                        ("active_order_price", "委託價格"),
                        ("active_order_qty", "委託股數"),
                        ("order_age", "掛單時間"),
                        ("current_mode_display", "模式"),
                        ("last_price", "現價"),
                        ("bid1", "買一"),
                        ("ask1", "賣一"),
                        ("quote_timestamp", "報價時間"),
                        ("buy_submission_gate", "提交 Gate"),
                        ("tick_distance_to_target", "Tick 差距"),
                        ("next_check_time", "下次檢查"),
                        ("order_status_summary_display", "狀態"),
                    ],
                    "尚無買進執行資料。",
                    section_id="buy-execution",
                ),
            ),
            (
                "positions",
                "持倉",
                _render_table_html(
                    "持倉",
                    report.get("positions_rows", []),
                    [
                        ("stock_id", "股票代號"),
                        ("stock_name", "股票名稱"),
                        ("basket_tag", "Basket"),
                        ("holding_qty", "持有股數"),
                        ("buy_avg_price", "買進均價"),
                        ("buy_total_cost", "買進成本"),
                        ("current_price", "現價"),
                        ("market_value", "市值"),
                        ("unrealized_pnl", "未實現損益"),
                        ("unrealized_pnl_pct", "未實現報酬率"),
                        ("estimated_exit_value_after_fee_tax", "估計出場金額"),
                        ("breakeven_sell_price", "損益兩平價"),
                    ],
                    "尚無持倉資料。",
                    section_id="positions",
                ),
            ),
            (
                "excluded-positions",
                "排除部位",
                _render_table_html(
                    "排除部位",
                    report.get("excluded_positions_rows", []),
                    [
                        ("stock_id", "股票代號"),
                        ("stock_name", "股票名稱"),
                        ("broker_qty", "券商股數"),
                        ("strategy_qty", "策略股數"),
                        ("excluded_qty", "排除股數"),
                        ("reason", "原因"),
                    ],
                    "無排除部位。",
                    section_id="excluded-positions",
            ),
        ),
        (
            "broker-underheld",
            "缺股風險",
            _render_table_html(
                "缺股風險",
                report.get("broker_underheld_rows", []),
                [
                    ("stock_id", "股票代號"),
                    ("stock_name", "股票名稱"),
                    ("broker_qty", "券商股數"),
                    ("strategy_qty", "策略股數"),
                    ("missing_qty", "缺口股數"),
                    ("reason", "原因"),
                ],
                "無缺股風險。",
                section_id="broker-underheld",
            ),
        ),
        (
            "ambiguous-fills",
            "待對帳成交",
            _render_table_html(
                "待對帳成交",
                report.get("ambiguous_fill_rows", []),
                    [
                        ("stock_id", "股票代號"),
                        ("side", "方向"),
                        ("fill_qty", "股數"),
                        ("fill_price", "成交價"),
                        ("fill_time", "成交時間"),
                        ("broker_fill_id", "券商成交單號"),
                        ("broker_custom_field", "Broker Token"),
                        ("fill_assignment_status", "歸戶狀態"),
                    ],
                    "無待對帳成交。",
                    section_id="ambiguous-fills",
                ),
            ),
            (
                "sell-status",
                "賣出狀態",
                _render_table_html(
                    "賣出狀態",
                    report.get("sell_rows", []),
                    [
                        ("stock_id", "股票代號"),
                        ("basket_tag", "Basket"),
                        ("can_sell_flag", "可賣"),
                        ("conservative_sell_price", "保守出場價"),
                        ("conservative_profit", "保守獲利"),
                        ("sell_decision", "賣出決策"),
                        ("sell_decision_reason", "決策原因"),
                        ("basket_recommendation", "整包建議"),
                        ("basket_threshold", "整包門檻"),
                        ("basket_loser_loss_ratio", "虧損股佔比"),
                        ("quote_timestamp", "報價時間"),
                        ("sell_submission_gate", "提交 Gate"),
                        ("sell_order_price", "委託出場價"),
                        ("sell_order_status", "委託狀態"),
                        ("actual_fill_avg_price", "實際成交均價"),
                        ("sold_qty", "已賣"),
                        ("remaining_qty", "剩餘"),
                        ("realized_pnl", "已實現損益"),
                        ("sell_pnl_source", "PnL 來源"),
                    ],
                    "尚無賣出資料。",
                    section_id="sell-status",
                ),
            ),
            ("basket-summary", "整包分析", _render_basket_summary(report)),
            (
                "benchmark-chart",
                "策略 vs 加權 vs 2330",
                _series_svg(report.get("comparison_chart", {}), title="策略 vs 加權 vs 2330", section_id="benchmark-chart"),
            ),
            (
                "capital-chart",
                "現金 vs 已投入資金",
                _series_svg(report.get("capital_chart", {}), title="現金 vs 已投入資金", section_id="capital-chart"),
            ),
            (
                "events-log",
                "事件紀錄",
                _render_table_html(
                    "事件紀錄",
                    event_rows,
                    [
                        ("time", "時間"),
                        ("display_event_type", "事件"),
                        ("stock_id", "股票"),
                        ("display_action", "動作"),
                        ("price", "價格"),
                        ("qty", "股數"),
                        ("display_result", "結果"),
                        ("display_warning_or_error", "警告 / 錯誤"),
                    ],
                    "尚無事件紀錄。",
                    section_id="events-log",
                ),
            ),
            (
                "next-actions",
                "後續動作",
                _render_list_panel("後續動作", report.get("next_actions", []), empty="尚無後續動作。", section_id="next-actions"),
            ),
        ]
    )
    body = [
        _render_cards(report),
        _render_section_nav([(section_id, label) for section_id, label, _ in section_blocks]),
        "<div class='layout'>",
        *(html for _, _, html in section_blocks),
        "</div>",
    ]
    return _render_page_shell(title=title, subtitle=subtitle, body="".join(body))


def _weekly_markdown(summary: dict[str, Any]) -> str:
    totals = summary.get("weekly_totals", {})
    benchmark = summary.get("benchmark_summary", {})
    lines = [
        f"# {summary.get('week_id', '')} 自動交易週報",
        "",
        "## 本週總表",
        f"- provider_name: {summary.get('provider_name', '')}",
        f"- total_buy_cost: {_money(totals.get('total_buy_cost', 0))}",
        f"- final_market_value: {_money(totals.get('final_market_value', 0))}",
        f"- total_profit: {_money(totals.get('total_profit', 0))}",
        f"- strategy_return: {_pct(totals.get('strategy_return', 0))}",
        "",
        "## 基準對照",
        f"- twii_return: {_pct(benchmark.get('twii_return', 0))}",
        f"- tsmc_return: {_pct(benchmark.get('tsmc_return', 0))}",
        f"- strategy_excess_vs_twii: {_pct(benchmark.get('strategy_excess_vs_twii', 0))}",
        f"- strategy_excess_vs_tsmc: {_pct(benchmark.get('strategy_excess_vs_tsmc', 0))}",
        "",
    ]
    lines.extend(
        _render_markdown_section(
            "逐日表",
            summary.get("daily_rows", []),
            [
                ("date", "date"),
                ("twii", "twii"),
                ("tsmc", "2330"),
                ("preselect", "preselect"),
                ("final_list", "final_list"),
                ("equal_weight_version", "equal_weight_version"),
                ("weighted_version", "weighted_version"),
                ("monday_plan", "monday_plan"),
                ("secondary_add", "secondary_add"),
                ("actual_combined", "actual_combined"),
                ("position_data_quality", "position_data_quality"),
                ("fallback_lot_count", "fallback_lot_count"),
                ("ambiguous_fill_guard_count", "ambiguous_fill_guard_count"),
                ("excluded_position_guard_count", "excluded_position_guard_count"),
                ("broker_underheld_guard_count", "broker_underheld_guard_count"),
                ("positions_source_date", "positions_source_date"),
            ],
            "尚無逐日資料。",
        )
    )
    lines.extend(_render_markdown_section("排除項目", summary.get("excluded_positions", []), [("item", "item")], "無排除項目。"))
    lines.extend(
        _render_markdown_section(
            "缺股風險",
            summary.get("broker_underheld_rows", []),
            [
                ("date", "date"),
                ("stock_id", "stock_id"),
                ("stock_name", "stock_name"),
                ("broker_qty", "broker_qty"),
                ("strategy_qty", "strategy_qty"),
                ("missing_qty", "missing_qty"),
                ("reason", "reason"),
            ],
            "無缺股風險。",
        )
    )
    lines.extend(_render_markdown_section("實際交易成果", summary.get("trade_results", []), [("label", "label"), ("value", "value")], "尚無實際交易成果。"))
    lines.extend(
        _render_markdown_section(
            "待對帳成交",
            summary.get("ambiguous_fill_rows", []),
            [
                ("date", "date"),
                ("stock_id", "stock_id"),
                ("side", "side"),
                ("fill_qty", "fill_qty"),
                ("fill_price", "fill_price"),
                ("fill_time", "fill_time"),
                ("broker_fill_id", "broker_fill_id"),
                ("broker_custom_field", "broker_custom_field"),
                ("fill_assignment_status", "fill_assignment_status"),
            ],
            "無待對帳成交。",
        )
    )
    lines.extend(
        _render_markdown_section(
            "本週 Lot Ledger",
            summary.get("lot_ledger_rows", []),
            [
                ("strategy_lot_id", "strategy_lot_id"),
                ("stock_id", "stock_id"),
                ("buy_fill_qty", "buy_fill_qty"),
                ("sell_fill_qty", "sell_fill_qty"),
                ("closing_qty", "closing_qty"),
                ("realized_pnl", "realized_pnl"),
                ("lot_status", "lot_status"),
            ],
            "尚無 lot ledger 資料。",
        )
    )
    lines.extend(
        _render_markdown_section(
            "未成交 / 失效",
            summary.get("expired_unfilled", []),
            [("stock_id", "stock_id"), ("stop_day", "stop_day"), ("reason", "reason")],
            "尚無未成交 / 失效資料。",
        )
    )
    lines.append("## 下週調參建議")
    lines.extend([f"- {item}" for item in summary.get("tuning_suggestions", [])] or ["- 先持續觀察 live 成交品質，再決定是否放寬參數。"])
    lines.append("")
    return "\n".join(lines)


def _weekly_html(summary: dict[str, Any]) -> str:
    totals = summary.get("weekly_totals", {})
    benchmark_summary = summary.get("benchmark_summary", {})
    overview = {
        "week_id": summary.get("week_id", ""),
        "trade_date": summary.get("end_date", ""),
        "mode": summary.get("mode", ""),
        "provider_name": summary.get("provider_name", ""),
        "overview": {
            "last_update_time": summary.get("last_update_time", ""),
            "today_status": "已結算",
            "weekly_budget": totals.get("weekly_budget", 0),
            "hard_budget": totals.get("hard_budget", 0),
            "used_cash": totals.get("total_buy_cost", 0),
            "remaining_cash": max(float(totals.get("hard_budget", 0)) - float(totals.get("total_buy_cost", 0)), 0.0),
            "current_equity": totals.get("final_market_value", 0),
            "strategy_return": totals.get("strategy_return", 0),
        },
    }
    section_blocks: list[tuple[str, str, str]] = [
        (
            "weekly-totals",
            "本週總表",
            _render_table_html(
                "本週總表",
                [
                    {"metric": "總買進成本", "value": _money(totals.get("total_buy_cost", 0))},
                    {"metric": "期末市值", "value": _money(totals.get("final_market_value", 0))},
                    {"metric": "總獲利", "value": _money(totals.get("total_profit", 0))},
                    {"metric": "策略報酬率", "value": _pct(totals.get("strategy_return", 0))},
                ],
                [("metric", "項目"), ("value", "數值")],
                "尚無本週總表資料。",
                section_id="weekly-totals",
            ),
        ),
        (
            "benchmark",
            "基準對照",
            _render_table_html(
                "基準對照",
                [
                    {"metric": "加權報酬率", "value": _pct(benchmark_summary.get("twii_return", 0))},
                    {"metric": "2330 報酬率", "value": _pct(benchmark_summary.get("tsmc_return", 0))},
                    {"metric": "策略超額 vs 加權", "value": _pct(benchmark_summary.get("strategy_excess_vs_twii", 0))},
                    {"metric": "策略超額 vs 2330", "value": _pct(benchmark_summary.get("strategy_excess_vs_tsmc", 0))},
                ],
                [("metric", "項目"), ("value", "數值")],
                "尚無基準對照資料。",
                section_id="benchmark",
            ),
        ),
        (
            "daily-rows",
            "逐日表",
            _render_table_html(
                    "逐日表",
                    summary.get("daily_rows", []),
                    [
                        ("date", "日期"),
                        ("twii", "TAIEX"),
                        ("tsmc", "2330"),
                        ("preselect", "預選"),
                        ("final_list", "訂版"),
                        ("equal_weight_version", "同股數版"),
                        ("weighted_version", "加權版"),
                        ("monday_plan", "週一主方案"),
                        ("secondary_add", "週二加買"),
                        ("actual_combined", "實際合併版"),
                        ("position_data_quality", "部位資料"),
                        ("fallback_lot_count", "Fallback Lots"),
                        ("ambiguous_fill_guard_count", "Guard Lots"),
                        ("excluded_position_guard_count", "Excluded Guard Lots"),
                        ("broker_underheld_guard_count", "Broker Guard Lots"),
                        ("positions_source_date", "部位來源日"),
                    ],
                "尚無逐日資料。",
                section_id="daily-rows",
            ),
        ),
        (
            "excluded-positions",
            "排除項目",
            _render_table_html(
                "排除項目",
                summary.get("excluded_positions", []),
                [("item", "項目")],
                "無排除項目。",
                section_id="excluded-positions",
            ),
        ),
        (
            "broker-underheld",
            "缺股風險",
            _render_table_html(
                "缺股風險",
                summary.get("broker_underheld_rows", []),
                [
                    ("date", "日期"),
                    ("stock_id", "股票代號"),
                    ("stock_name", "股票名稱"),
                    ("broker_qty", "券商股數"),
                    ("strategy_qty", "策略股數"),
                    ("missing_qty", "缺口股數"),
                    ("reason", "原因"),
                ],
                "無缺股風險。",
                section_id="broker-underheld",
            ),
        ),
        (
            "trade-results",
            "實際交易成果",
            _render_table_html(
                "實際交易成果",
                summary.get("trade_results", []),
                [("label", "項目"), ("value", "數值")],
                "尚無實際交易成果。",
                section_id="trade-results",
            ),
        ),
        (
            "ambiguous-fills",
            "待對帳成交",
            _render_table_html(
                "待對帳成交",
                summary.get("ambiguous_fill_rows", []),
                [
                    ("date", "日期"),
                    ("stock_id", "股票"),
                    ("side", "方向"),
                    ("fill_qty", "股數"),
                    ("fill_price", "成交價"),
                    ("fill_time", "成交時間"),
                    ("broker_fill_id", "券商成交單號"),
                    ("broker_custom_field", "Broker Token"),
                    ("fill_assignment_status", "歸戶狀態"),
                ],
                "無待對帳成交。",
                section_id="ambiguous-fills",
            ),
        ),
        (
            "lot-ledger",
            "本週 Lot Ledger",
            _render_table_html(
                "本週 Lot Ledger",
                summary.get("lot_ledger_rows", []),
                [
                    ("strategy_lot_id", "Strategy Lot"),
                    ("stock_id", "股票"),
                    ("stock_name", "名稱"),
                    ("buy_fill_qty", "買進股數"),
                    ("sell_fill_qty", "賣出股數"),
                    ("closing_qty", "期末股數"),
                    ("realized_pnl", "已實現損益"),
                    ("lot_status", "狀態"),
                ],
                "尚無 lot ledger 資料。",
                section_id="lot-ledger",
            ),
        ),
        (
            "expired-unfilled",
            "未成交 / 失效",
            _render_table_html(
                "未成交 / 失效",
                summary.get("expired_unfilled", []),
                [("stock_id", "股票"), ("stop_day", "停止追價日"), ("reason", "原因")],
                "尚無未成交 / 失效資料。",
                section_id="expired-unfilled",
            ),
        ),
    ]
    tuning_block = _render_warning_block(summary.get("tuning_suggestions", []), title="下週調參建議", section_id="tuning-suggestions")
    if tuning_block:
        section_blocks.append(("tuning-suggestions", "下週調參建議", tuning_block))
    section_blocks.append(
        (
            "comparison-chart",
            "策略 vs 加權 vs 2330",
            _series_svg(summary.get("comparison_chart", {}), title="策略 vs 加權 vs 2330", section_id="comparison-chart"),
        )
    )
    body = [
        _render_cards(overview),
        _render_section_nav([(section_id, label) for section_id, label, _ in section_blocks]),
        "<div class='layout'>",
        *(html for _, _, html in section_blocks),
        "</div>",
    ]
    return _render_page_shell(
        title=f"{summary.get('week_id', '')} 自動交易週報",
        subtitle="永豐自動交易每週執行儀表板。",
        body="".join(body),
    )


def render_daily_report(
    report: dict[str, Any],
    markdown_path: Path,
    html_path: Path,
    *,
    snapshot_json_path: Path | None = None,
    current_html_path: Path | None = None,
    current_snapshot_path: Path | None = None,
    legacy_html_path: Path | None = None,
) -> None:
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    display_report = _augment_daily_report_display(report)
    snapshot_payload = _daily_snapshot_payload(display_report)

    markdown_path.write_text(_daily_markdown(display_report), encoding="utf-8")
    html = _daily_html(
        display_report,
        title=f"{display_report.get('trade_date', '')} 永豐自動交易",
        subtitle="永豐自動交易執行儀表板與報告頁。",
    )
    html_path.write_text(html, encoding="utf-8")

    if snapshot_json_path is not None:
        _write_json(snapshot_json_path, snapshot_payload)
    if current_html_path is not None:
        current_html_path.parent.mkdir(parents=True, exist_ok=True)
        current_html_path.write_text(html, encoding="utf-8")
    if current_snapshot_path is not None:
        _write_json(current_snapshot_path, snapshot_payload)
    if legacy_html_path is not None:
        legacy_html_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_html_path.write_text(html, encoding="utf-8")


def render_weekly_settlement(
    summary: dict[str, Any],
    note_path: Path,
    *,
    html_path: Path | None = None,
    snapshot_json_path: Path | None = None,
) -> None:
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(_weekly_markdown(summary), encoding="utf-8")

    if html_path is not None:
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(_weekly_html(summary), encoding="utf-8")
    if snapshot_json_path is not None:
        _write_json(snapshot_json_path, summary)

