from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path

from sinopac_auto_trading.cli import _write_selection_csv
from sinopac_auto_trading.selection_provider import SelectionItem


class CLISelectionCsvTests(unittest.TestCase):
    def _case_dir(self, name: str) -> Path:
        path = Path(__file__).resolve().parent / "_tmp" / f"{name}-{uuid.uuid4().hex}"
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def test_write_selection_csv_supports_full_selection_item_fields(self) -> None:
        temp_dir = self._case_dir("selection-csv")
        path = temp_dir / "selection.csv"
        _write_selection_csv(
            path,
            [
                SelectionItem(
                    stock_id="2330",
                    stock_name="TSMC",
                    source="A+B",
                    basket_tag="secondary_add",
                    a_flag=True,
                    b_flag=True,
                    catalyst_flag=True,
                    note="full field coverage",
                )
            ],
        )
        content = path.read_text(encoding="utf-8-sig")
        self.assertIn("catalyst_flag", content)
        self.assertIn("basket_tag", content)
        self.assertIn("secondary_add", content)
        self.assertIn("2330", content)


if __name__ == "__main__":
    unittest.main()
