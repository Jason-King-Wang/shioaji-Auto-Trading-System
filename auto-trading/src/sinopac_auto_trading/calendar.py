from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .paths import CALENDAR_DIR


def _parse_trade_date(raw: str) -> date:
    return date.fromisoformat(raw.strip())


@dataclass(slots=True)
class WeekTradePlan:
    anchor_date: date
    week_trade_days: list[date]
    buy_cutoff_day: date | None
    last_trade_day: date | None
    calendar_missing_warning: bool
    source_path: Path | None

    @property
    def no_buy_this_week(self) -> bool:
        return self.buy_cutoff_day is None


def load_trade_days(calendar_path: Path | None = None) -> tuple[list[date], bool, Path | None]:
    resolved_path = calendar_path or (CALENDAR_DIR / "twse_trading_calendar.csv")
    if not resolved_path.exists():
        return [], True, None

    with resolved_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        trade_days: list[date] = []
        for row in reader:
            raw = row.get("trade_date") or row.get("date") or next(iter(row.values()), "")
            if raw:
                trade_days.append(_parse_trade_date(raw))
    return sorted(trade_days), False, resolved_path


def fallback_weekdays(anchor_date: date) -> list[date]:
    monday = anchor_date - timedelta(days=anchor_date.weekday())
    return [monday + timedelta(days=offset) for offset in range(5)]


def compute_buy_cutoff_day(week_trade_days: list[date]) -> date | None:
    if len(week_trade_days) == 1:
        return None
    if len(week_trade_days) == 2:
        return week_trade_days[0]
    third_trade_day = week_trade_days[2]
    last_minus_two_trade_days = week_trade_days[-3]
    return min(third_trade_day, last_minus_two_trade_days)


def resolve_week_trade_plan(anchor_date: date, calendar_path: Path | None = None) -> WeekTradePlan:
    trade_days, missing_warning, source_path = load_trade_days(calendar_path)
    if not trade_days:
        week_trade_days = fallback_weekdays(anchor_date)
        return WeekTradePlan(
            anchor_date=anchor_date,
            week_trade_days=week_trade_days,
            buy_cutoff_day=compute_buy_cutoff_day(week_trade_days),
            last_trade_day=week_trade_days[-1] if week_trade_days else None,
            calendar_missing_warning=True,
            source_path=source_path,
        )

    monday = anchor_date - timedelta(days=anchor_date.weekday())
    sunday = monday + timedelta(days=6)
    week_trade_days = [item for item in trade_days if monday <= item <= sunday]
    if not week_trade_days:
        week_trade_days = fallback_weekdays(anchor_date)
        missing_warning = True

    return WeekTradePlan(
        anchor_date=anchor_date,
        week_trade_days=week_trade_days,
        buy_cutoff_day=compute_buy_cutoff_day(week_trade_days),
        last_trade_day=week_trade_days[-1] if week_trade_days else None,
        calendar_missing_warning=missing_warning,
        source_path=source_path,
    )
