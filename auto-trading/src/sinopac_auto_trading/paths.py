from __future__ import annotations

from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
INPUTS_DIR = DATA_DIR / "inputs"
AUTO_TRADING_DIR = DATA_DIR / "auto_trading"
CALENDAR_DIR = DATA_DIR / "calendars"
NOTES_DIR = PROJECT_ROOT / "notes"
DAILY_NOTES_DIR = NOTES_DIR / "daily"
WEEKLY_NOTES_DIR = NOTES_DIR / "weeks"
REPORTS_DIR = PROJECT_ROOT / "reports" / "auto_trading"
REPORTS_DAILY_DIR = REPORTS_DIR / "daily"
REPORTS_WEEKS_DIR = REPORTS_DIR / "weeks"
REPORTS_DATA_DIR = REPORTS_DIR / "data"
SCHEMAS_DIR = PROJECT_ROOT / "schemas"
EXAMPLES_DIR = PROJECT_ROOT / "examples"


def ensure_runtime_directories() -> None:
    for path in (
        INPUTS_DIR,
        AUTO_TRADING_DIR,
        CALENDAR_DIR,
        DAILY_NOTES_DIR,
        WEEKLY_NOTES_DIR,
        REPORTS_DIR,
        REPORTS_DAILY_DIR,
        REPORTS_WEEKS_DIR,
        REPORTS_DATA_DIR,
        SCHEMAS_DIR,
        EXAMPLES_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)


def input_dir_for(trade_date: date) -> Path:
    return INPUTS_DIR / trade_date.isoformat()


def auto_trading_dir_for(trade_date: date) -> Path:
    return AUTO_TRADING_DIR / trade_date.isoformat()


def daily_note_path(trade_date: date) -> Path:
    return DAILY_NOTES_DIR / f"{trade_date.isoformat()}_auto_trading_daily.md"


def weekly_note_path(start_date: date, end_date: date) -> Path:
    return WEEKLY_NOTES_DIR / f"{start_date.isoformat()}_{end_date.isoformat()}_auto_trading_weekly.md"


def current_html_report_path() -> Path:
    return REPORTS_DIR / "current.html"


def dated_html_report_path(trade_date: date) -> Path:
    return REPORTS_DIR / f"{trade_date.isoformat()}.html"


def daily_html_report_path(trade_date: date) -> Path:
    return REPORTS_DAILY_DIR / f"{trade_date.isoformat()}.html"


def weekly_html_report_path(start_date: date, end_date: date) -> Path:
    return REPORTS_WEEKS_DIR / f"{start_date.isoformat()}_{end_date.isoformat()}.html"


def current_snapshot_json_path() -> Path:
    return REPORTS_DATA_DIR / "current_snapshot.json"


def dated_snapshot_json_path(trade_date: date) -> Path:
    return REPORTS_DATA_DIR / f"{trade_date.isoformat()}_snapshot.json"


def weekly_snapshot_json_path(start_date: date, end_date: date) -> Path:
    return REPORTS_DATA_DIR / f"{start_date.isoformat()}_{end_date.isoformat()}_weekly_snapshot.json"


def llm_selection_payload_path(trade_date: date) -> Path:
    return input_dir_for(trade_date) / "llm_selection_review_payload.json"


def llm_selection_decisions_path(trade_date: date) -> Path:
    return input_dir_for(trade_date) / "llm_selection_decisions.json"


def llm_selection_template_path(trade_date: date) -> Path:
    return input_dir_for(trade_date) / "llm_selection_decisions.template.json"


def llm_selection_brief_path(trade_date: date) -> Path:
    return DAILY_NOTES_DIR / f"{trade_date.isoformat()}-llm-selection-brief.md"
