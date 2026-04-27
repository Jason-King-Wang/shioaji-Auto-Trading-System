from __future__ import annotations

import unittest
from datetime import date, datetime
from types import SimpleNamespace

from sinopac_auto_trading.cli import (
    _a_preselect_confirmation_time_status,
    _a_preselect_sizing_confirmation_status,
)
from sinopac_auto_trading.time_utils import TAIPEI


class APreselectConfirmationTimeTests(unittest.TestCase):
    def _settings(self, active: str = "ab_llm_preselect_json"):
        return SimpleNamespace(
            providers=SimpleNamespace(active=active),
            auto_trading=SimpleNamespace(a_preselect_confirmation_start_time="10:00"),
        )

    def test_a_preselect_confirmation_waits_until_10_for_target_trade_date(self) -> None:
        settings = self._settings()
        status = _a_preselect_confirmation_time_status(
            settings,
            date(2026, 4, 27),
            now=datetime(2026, 4, 27, 9, 59, tzinfo=TAIPEI),
        )

        self.assertFalse(status["ready"])
        self.assertEqual(status["reason"], "before_a_preselect_confirmation_start_time")
        self.assertEqual(status["start_at"], "2026-04-27T10:00:00+08:00")

    def test_a_preselect_confirmation_allows_at_10(self) -> None:
        settings = self._settings()
        status = _a_preselect_confirmation_time_status(
            settings,
            date(2026, 4, 27),
            now=datetime(2026, 4, 27, 10, 0, tzinfo=TAIPEI),
        )

        self.assertTrue(status["ready"])
        self.assertEqual(status["status"], "a_preselect_confirmation_time_ready")

    def test_buy_sizing_requires_finalize_after_10(self) -> None:
        settings = self._settings()
        status = _a_preselect_sizing_confirmation_status(
            settings,
            date(2026, 4, 27),
            {},
            now=datetime(2026, 4, 27, 10, 1, tzinfo=TAIPEI),
        )

        self.assertFalse(status["ready"])
        self.assertEqual(status["reason"], "sizing_not_confirmed_after_a_preselect_start_time")

    def test_buy_sizing_passes_when_finalize_confirmed_after_10(self) -> None:
        settings = self._settings()
        status = _a_preselect_sizing_confirmation_status(
            settings,
            date(2026, 4, 27),
            {"a_preselect_confirmed_at": "2026-04-27T10:02:00+08:00"},
            now=datetime(2026, 4, 27, 10, 3, tzinfo=TAIPEI),
        )

        self.assertTrue(status["ready"])
        self.assertEqual(status["status"], "a_preselect_confirmed_for_sizing")

    def test_non_ab_provider_does_not_require_confirmation_time(self) -> None:
        status = _a_preselect_confirmation_time_status(
            self._settings(active="manual_csv"),
            date(2026, 4, 27),
            now=datetime(2026, 4, 27, 9, 0, tzinfo=TAIPEI),
        )

        self.assertTrue(status["ready"])
        self.assertFalse(status["required"])


if __name__ == "__main__":
    unittest.main()
