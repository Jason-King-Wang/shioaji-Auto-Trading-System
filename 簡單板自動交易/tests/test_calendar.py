from __future__ import annotations

import csv
import unittest
from datetime import date
from pathlib import Path
import shutil
import uuid

from sinopac_auto_trading.calendar import resolve_week_trade_plan


class CalendarTests(unittest.TestCase):
    def _calendar_path(self, trade_days: list[date]) -> Path:
        temp_dir = Path(__file__).resolve().parent / "_tmp" / f"calendar-{uuid.uuid4().hex}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        path = temp_dir / "calendar.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["trade_date"])
            writer.writeheader()
            for trade_day in trade_days:
                writer.writerow({"trade_date": trade_day.isoformat()})
        return path

    def test_normal_week_cutoff_is_third_trade_day(self) -> None:
        path = self._calendar_path(
            [
                date(2026, 4, 20),
                date(2026, 4, 21),
                date(2026, 4, 22),
                date(2026, 4, 23),
                date(2026, 4, 24),
            ]
        )
        plan = resolve_week_trade_plan(date(2026, 4, 20), calendar_path=path)
        self.assertEqual(plan.buy_cutoff_day, date(2026, 4, 22))
        self.assertEqual(plan.last_trade_day, date(2026, 4, 24))

    def test_four_day_week_cutoff_is_second_trade_day(self) -> None:
        path = self._calendar_path(
            [
                date(2026, 4, 20),
                date(2026, 4, 21),
                date(2026, 4, 22),
                date(2026, 4, 23),
            ]
        )
        plan = resolve_week_trade_plan(date(2026, 4, 20), calendar_path=path)
        self.assertEqual(plan.buy_cutoff_day, date(2026, 4, 21))

    def test_three_day_week_cutoff_is_first_trade_day(self) -> None:
        path = self._calendar_path(
            [
                date(2026, 4, 20),
                date(2026, 4, 21),
                date(2026, 4, 22),
            ]
        )
        plan = resolve_week_trade_plan(date(2026, 4, 20), calendar_path=path)
        self.assertEqual(plan.buy_cutoff_day, date(2026, 4, 20))

    def test_last_trade_day_is_not_a_buy_day(self) -> None:
        path = self._calendar_path(
            [
                date(2026, 4, 20),
                date(2026, 4, 21),
                date(2026, 4, 22),
                date(2026, 4, 23),
                date(2026, 4, 24),
            ]
        )
        plan = resolve_week_trade_plan(date(2026, 4, 24), calendar_path=path)
        self.assertEqual(plan.last_trade_day, date(2026, 4, 24))
        self.assertLess(plan.buy_cutoff_day, plan.last_trade_day)


if __name__ == "__main__":
    unittest.main()
