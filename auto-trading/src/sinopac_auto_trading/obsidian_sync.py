from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .calendar import resolve_week_trade_plan
from .config import Settings
from .paths import auto_trading_dir_for, current_html_report_path, daily_html_report_path, daily_note_path, weekly_note_path
from .shioaji_client import describe_account, login
from .time_utils import TAIPEI

ALLOWED_LIVE_AUTOMATION = "`2330 / Buy / IntradayOdd / 1股 / 09:10 / 價格上限 2100`"
USER_MAXIM_NOTE = "[[我的箴言語錄]]"
USER_PREF_NOTE = "[[使用者操作偏好]]"
PROJECT_NOTE = "[[永豐自動交易_專案說明書]]"
BLUEPRINT_NOTE = "[[自動交易核心施工藍圖]]"


@dataclass(slots=True)
class LiveStatus:
    account_lines: list[str]
    trade_count: int
    ca_expiretime: str | None


def _require_vault_root(settings: Settings) -> Path:
    if not settings.obsidian_vault_root:
        raise RuntimeError("Obsidian vault root is not configured or the default vault was not found.")
    return settings.obsidian_vault_root


def _read_state_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _collect_live_status(settings: Settings) -> LiveStatus:
    api, accounts = login(settings, simulation=False, fetch_contract=False)
    api.update_status(api.stock_account)
    trades = list(api.list_trades())
    return LiveStatus(
        account_lines=[describe_account(account) for account in accounts],
        trade_count=len(trades),
        ca_expiretime=api.get_ca_expiretime(settings.person_id) if settings.person_id else None,
    )


def _last_trade_day_for(trade_date: date, state_data: dict[str, object], fallback: date) -> str:
    raw = str(state_data.get("last_trade_day", "")).strip()
    return raw or fallback.isoformat()


def _related_note_lines() -> list[str]:
    return [
        "## 先讀筆記",
        f"- {USER_MAXIM_NOTE}",
        f"- {USER_PREF_NOTE}",
        f"- {PROJECT_NOTE}",
        f"- {BLUEPRINT_NOTE}",
    ]


def _live_status_lines(live_status: LiveStatus | None) -> list[str]:
    if not live_status:
        return [
            "## Live 狀態",
            "- 本次同步未額外讀取券商 live 狀態。",
            "- 若要附帶券商帳號與 CA 狀態，請改用 `sync_obsidian --include-live-status`。",
        ]

    lines = [
        "## Live 狀態",
        *[f"- {line}" for line in live_status.account_lines],
        f"- 券商 trade_count: `{live_status.trade_count}`",
        f"- CA 到期日: `{live_status.ca_expiretime or ''}`",
    ]
    return lines


def _render_current_status(
    settings: Settings,
    trade_date: date,
    *,
    state_data: dict[str, object],
    live_status: LiveStatus | None,
    event_summary: str | None,
) -> str:
    timestamp = datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M:%S %Z")
    provider_name = str(state_data.get("provider_name", settings.providers.active))
    buy_cutoff_day = str(state_data.get("buy_cutoff_day", ""))
    last_trade_day = str(state_data.get("last_trade_day", ""))
    run_status = str(state_data.get("status", ""))
    calendar_warning = bool(state_data.get("calendar_missing_warning", False))

    lines = [
        "# 永豐自動交易 Current Status",
        "",
        f"> 更新時間：{timestamp}",
        "",
        *_related_note_lines(),
        "",
        "## 判讀原則",
        f"- 新增的使用者偏好筆記以 {USER_MAXIM_NOTE} 與 {USER_PREF_NOTE} 為主。",
        "- 舊筆記保留歷史脈絡，但若與新偏好衝突，應以新偏好為準。",
        "- 真實下單仍必須保留既有 guardrail，不等於可以跳過檢查。",
        "",
        "## 系統現況",
        f"- trade_date: `{trade_date.isoformat()}`",
        f"- provider: `{provider_name}`",
        "- candidate_pool_source: private upstream signal export",
        "- selection_source: private daily signal JSON",
        "- selection_rule: `target_trade_date matches trade_date / A-only / no B / missing usable target => pass`",
        "- a_preselect_confirmation_start: `目標交易日 10:00 Asia/Taipei`",
        "- a_preselect_confirmation_reminder: `10點後才會執行永豐自動交易的確定A預選工作。`",
        "- a_preselect_confirmation_reason: `週一 06:00 才開始產出預選名單，09:00 可能尚未完成；開盤前測試若太早 finalize，可能把週末產出的檔案誤鎖成週一最新正式清單。`",
        "- live_buy_repair_confirmation_policy: `所有未來整包 live 買進只要修復後想續下，都必須先產生 repair_confirmation 對帳報告並寄信給使用者；使用者不需要固定制式回覆，依 mail 內容判斷；只有回覆內容明確授權、仍是授權交易日、broker/local 狀態明確，才可補下未送出的剩餘單。`",
        "- duplicate_buy_guard_policy: `防重買優先於續下；同一 strategy_lot_id 若在 local orders / positions / week lot ledger / broker fills / broker order id / broker custom_field 已有 active 或 filled buy，不得再送同一筆。`",
        f"- run_status: `{run_status}`",
        f"- buy_cutoff_day: `{buy_cutoff_day}`",
        f"- last_trade_day: `{last_trade_day}`",
        f"- calendar_missing_warning: `{calendar_warning}`",
        f"- latest_report: `{daily_html_report_path(trade_date)}`",
        f"- latest_daily_note: `{daily_note_path(trade_date)}`",
    ]
    if event_summary:
        lines.append(f"- latest_event: {event_summary}")

    lines.extend(
        [
            "",
            "## Live Guardrail",
            f"- 目前唯一允許的受 guard live automation：{ALLOWED_LIVE_AUTOMATION}",
            "- 單股受 guard live 任務可真實送單，但整包 buy_loop / sell_loop 仍有各自 live 條件。",
            "- 受 guard 排程必須先通過共同 preflight；09:10 已過但 13:20 未過時，應補跑並每 5 分鐘重試。",
            "- 每次真實送單前都要查券商既有委託；找到同日同條件委託就略過，避免重複下單。",
            "- 若使用者新偏好與舊文件或系統預設衝突，先指出衝突，再由使用者決定。",
            "",
            *_live_status_lines(live_status),
            "",
            "## 路徑",
            f"- project_root: `{settings.project_root}`",
            f"- report_current_html: `{current_html_report_path()}`",
            f"- obsidian_vault_root: `{_require_vault_root(settings)}`",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def _render_current_commands(trade_date: date, *, sell_trade_date: str) -> str:
    return f"""# 永豐自動交易 Current Commands

## 先讀
- {USER_MAXIM_NOTE}
- {USER_PREF_NOTE}

## 現在的選股來源
- Upstream universe comes from a private signal-export JSON.
- The execution layer consumes only the exported daily final list.
- 正式買進來源必須以 `target_trade_date` / `source_target_trade_date` 對準目標交易日
- 檔名等於交易日但 target 不是該交易日的 JSON，不可拿來當天買進籃子
- 只讀 JSON 的 `a_preselect`，也就是 A 預選
- 目前完全不看 `B 預選`
- 如果找不到 target 對準目標交易日的 A 預選，就直接 pass，不回退舊日期名單

## A 預選確定時間
- 永豐自動交易的確定 A 預選工作固定在目標交易日 `10:00 Asia/Taipei` 之後才做。
- 操作提醒固定句：`10點後才會執行永豐自動交易的確定A預選工作。`
- 10:00 前可以做 `workflow_status`、`render_report`、live 登入檢查等只讀檢查，但不可把 A 預選物化成正式買進籃子。
- 原因：週一早上 06:00 才開始執行預選名單的產出，9:00 可能還沒產完；我們又可能在開盤前做測試，若太早 finalize，會把週末產出的 A 預選誤當成週一最新正式清單。

## 日常同步
```powershell
python run.py workflow_status --trade-date {trade_date.isoformat()}
python run.py render_report --trade-date {trade_date.isoformat()}
python run.py refresh_dashboard --trade-date {trade_date.isoformat()}
python run.py sync_obsidian --trade-date {trade_date.isoformat()}
```

## A 預選主線
10:00 後才執行：

```powershell
python run.py prepare_week --trade-date {trade_date.isoformat()}
python run.py finalize --trade-date {trade_date.isoformat()}
python run.py buy_loop --trade-date {trade_date.isoformat()}
python run.py sell_loop_readiness --trade-date {sell_trade_date} --json
python run.py sell_loop --trade-date {sell_trade_date}
```

## 整包 live 買進修復確認流程
- 這套流程適用所有未來整包 live 買進，不只限 2026-04-27。
- 若使用者明確授權的整包 live 買進失敗，修復 blocking issue 後不能直接續下；要先跑 `repair_confirmation --live`，查 broker / local 已送、已成交、未送的差異。
- mail 給 `ops@example.com`，內容必須列出「買了什麼 / 已送但未成交 / 什麼還沒買」。
- 使用者不需要固定制式回覆；要依 mail 回覆內容判斷，例如同意補下、不同意補下、只補某幾檔或先停住。若內容不清楚，停止並回報。
- 只有使用者回覆該 mail 且內容明確授權後，且仍是原本授權的交易日 / 第一交易日，才可用同一輪 live gate 補下尚未送出的剩餘單。
- 接著下之前仍要確認 broker / local artifacts 沒有不明委託、active duplicate、ambiguous fill、部分成交矛盾或狀態衝突。
- 防重買優先：若同一 `strategy_lot_id` 在 local orders / positions / week lot ledger / broker fills / broker order id / broker `custom_field` 已有 active 或 filled buy，就不得再送。
- 若已經不是週一 / 第一交易日，或狀態不明，停止並回報，不延伸成週二 / 週三追買。
- 若 `buy_loop --live` 因 live gate、broker 回報 failed / rejected / error、單列被 blocked、或有目標股數尚未完全送出，狀態檔會標記 `repair_confirmation_required`，後續修復入口必須走此 mail 確認流程。

## 上游 AB / LLM 備註
- Private selection rules stay outside this public interview repository.
- Upstream automation produces a sanitized daily signal export consumed by this execution layer.
- 本 repo 下游直接吃這份 JSON 的 `A 預選`

## Dry-run / 只檢查不送單
```powershell
python run.py login-check --live --no-fetch-contract
python run.py chase-stock-order --stock-id 2330 --price-cap 2100 --quantity 1 --order-lot IntradayOdd --live
python run.py buy_loop --trade-date {trade_date.isoformat()}
```

## Live Guardrail
- 唯一受 guard live automation：{ALLOWED_LIVE_AUTOMATION}
- guarded command: `python run.py run_allowed_live_order`
- read-only reconcile: `python run.py reconcile_broker_state --trade-date {trade_date.isoformat()} --live --stock-id 2330`
- post guarded check: `python run.py post_guarded_order_check --trade-date {trade_date.isoformat()} --json`
- post guarded reconcile/report: `python run.py post_guarded_order_check --trade-date {trade_date.isoformat()} --live --reconcile --sell-loop-readiness --render-report --workflow-status`
- install guarded schedule: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_allowed_2330_live_order_task.ps1 -RunDate {trade_date.isoformat()} -AtTime 09:10 -UntilTime 13:20 -RetryIntervalMinutes 5`
- remove: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/remove_allowed_2330_live_order_task.ps1`
- runner: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_allowed_2330_live_order_task.ps1`
- install 會先跑共同 preflight；如果 09:10 已過但 13:20 還沒過，會排最近一次補跑並每 5 分鐘重試；每次送單前都會先查券商委託避免重複下單。
- guard skip 不再算成功：只有實際送出或查到同日同條件既有委託，runner 才回傳 exit code 0。

## 其他需要明確確認的 live 指令
- `python run.py manual-stock-order --stock-id 2330 --price 2100 --quantity 1 --order-lot IntradayOdd --action Buy --live --submit --confirm-live`
- `python run.py chase-stock-order --stock-id 2330 --price-cap 2100 --quantity 1 --order-lot IntradayOdd --live --submit --confirm-live --start-time 09:10`
- `python run.py buy_loop --trade-date {trade_date.isoformat()} --live --confirm-live`
- `python run.py sell_loop --trade-date {sell_trade_date} --live --confirm-live`
"""


def _render_daily_sync_note(
    trade_date: date,
    *,
    state_data: dict[str, object],
    project_daily_note_path: Path,
    project_weekly_note_path: Path,
    report_path: Path,
    event_summary: str | None,
    live_status: LiveStatus | None,
) -> str:
    timestamp = datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M:%S %Z")
    provider_name = str(state_data.get("provider_name", ""))
    buy_cutoff_day = str(state_data.get("buy_cutoff_day", ""))
    last_trade_day = str(state_data.get("last_trade_day", ""))
    run_status = str(state_data.get("status", ""))

    lines = [
        f"# {trade_date.isoformat()} 自動交易同步",
        "",
        f"> 更新時間：{timestamp}",
        "",
        *_related_note_lines(),
        "",
        "## 今日摘要",
        f"- run_status: `{run_status}`",
        f"- provider: `{provider_name}`",
        f"- buy_cutoff_day: `{buy_cutoff_day}`",
        f"- last_trade_day: `{last_trade_day}`",
        f"- project_daily_note: `{project_daily_note_path}`",
        f"- project_weekly_note: `{project_weekly_note_path}`",
        f"- report_html: `{report_path}`",
    ]
    if event_summary:
        lines.append(f"- latest_event: {event_summary}")

    lines.extend(
        [
            "",
            "## 今日判讀原則",
            f"- 以 {USER_MAXIM_NOTE} 與 {USER_PREF_NOTE} 為主。",
            "- 舊筆記保留記錄用途，但若有落差，以更新後的偏好與現況為準。",
            f"- 目前唯一受 guard 的 live automation：{ALLOWED_LIVE_AUTOMATION}",
            "",
            *_live_status_lines(live_status),
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def _render_weekly_sync_note(
    week_id: str,
    *,
    project_weekly_note_path: Path,
    report_path: Path,
) -> str:
    timestamp = datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M:%S %Z")
    lines = [
        f"# {week_id} 自動交易週同步",
        "",
        f"> 更新時間：{timestamp}",
        "",
        *_related_note_lines(),
        "",
        "## 週摘要",
        f"- project_weekly_note: `{project_weekly_note_path}`",
        f"- latest_report_html: `{report_path}`",
        "",
        "## 週期提醒",
        f"- 使用者偏好以 {USER_MAXIM_NOTE} 與 {USER_PREF_NOTE} 為主。",
        "- 舊筆記保留歷史，不代表一定是最新執行規則。",
        f"- 目前唯一受 guard 的 live automation：{ALLOWED_LIVE_AUTOMATION}",
        "",
    ]
    return "\n".join(lines) + "\n"


def sync_obsidian_snapshot(
    settings: Settings,
    trade_date: date,
    *,
    include_live_status: bool = False,
    event_summary: str | None = None,
) -> list[Path]:
    vault_root = _require_vault_root(settings)
    (vault_root / "90_current").mkdir(parents=True, exist_ok=True)
    (vault_root / "30_daily").mkdir(parents=True, exist_ok=True)
    (vault_root / "20_weeks").mkdir(parents=True, exist_ok=True)

    plan = resolve_week_trade_plan(trade_date)
    state_data = _read_state_json(auto_trading_dir_for(trade_date) / "state.json")
    live_status = _collect_live_status(settings) if include_live_status else None

    start_date = plan.week_trade_days[0] if plan.week_trade_days else trade_date
    end_date = plan.week_trade_days[-1] if plan.week_trade_days else trade_date
    project_daily_note_path = daily_note_path(trade_date)
    project_weekly_note_path = weekly_note_path(start_date, end_date)
    report_path = daily_html_report_path(trade_date)
    week_id = f"{start_date.isoformat()}_{end_date.isoformat()}"
    sell_trade_date = _last_trade_day_for(trade_date, state_data, end_date)

    status_path = vault_root / "90_current" / "current_system_status.md"
    commands_path = vault_root / "90_current" / "current_commands.md"
    synced_daily_path = vault_root / "30_daily" / f"{trade_date.isoformat()}_auto_trading_sync.md"
    synced_weekly_path = vault_root / "20_weeks" / f"{week_id}_auto_trading_weekly.md"

    status_path.write_text(
        _render_current_status(
            settings,
            trade_date,
            state_data=state_data,
            live_status=live_status,
            event_summary=event_summary,
        ),
        encoding="utf-8",
    )
    commands_path.write_text(_render_current_commands(trade_date, sell_trade_date=sell_trade_date), encoding="utf-8")
    synced_daily_path.write_text(
        _render_daily_sync_note(
            trade_date,
            state_data=state_data,
            project_daily_note_path=project_daily_note_path,
            project_weekly_note_path=project_weekly_note_path,
            report_path=report_path,
            event_summary=event_summary,
            live_status=live_status,
        ),
        encoding="utf-8",
    )
    synced_weekly_path.write_text(
        _render_weekly_sync_note(
            week_id,
            project_weekly_note_path=project_weekly_note_path,
            report_path=report_path,
        ),
        encoding="utf-8",
    )
    return [status_path, commands_path, synced_daily_path, synced_weekly_path]
