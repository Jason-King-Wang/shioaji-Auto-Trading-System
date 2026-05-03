from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path

from sinopac_auto_trading.state_store import SQLiteStateStore


class StateStoreTests(unittest.TestCase):
    def test_merge_state_json_preserves_existing_fields(self) -> None:
        run_dir = Path(__file__).resolve().parent / "_tmp" / f"state-store-{uuid.uuid4().hex}"
        run_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(run_dir, ignore_errors=True))

        store = SQLiteStateStore(run_dir)
        store.initialize()
        store.write_state_json(
            {
                "status": "prepared",
                "provider_name": "manual_csv",
                "report_outputs": {"daily_html": "daily.html"},
            }
        )

        merged = store.merge_state_json(
            {
                "status": "finalized",
                "report_outputs": {"current_html": "current.html"},
            }
        )

        self.assertEqual(merged["status"], "finalized")
        self.assertEqual(merged["provider_name"], "manual_csv")
        self.assertEqual(merged["report_outputs"]["daily_html"], "daily.html")
        self.assertEqual(merged["report_outputs"]["current_html"], "current.html")


if __name__ == "__main__":
    unittest.main()
