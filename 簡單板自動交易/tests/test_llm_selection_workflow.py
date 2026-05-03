from __future__ import annotations

import json
import shutil
import unittest
import uuid
from datetime import date
from pathlib import Path
from unittest.mock import patch

from sinopac_auto_trading.llm_selection_workflow import (
    build_llm_review_payload,
    load_llm_decision_items,
    write_final_list_csv,
    write_llm_review_bundle,
)
from sinopac_auto_trading.selection_provider import SelectionItem


class LLMSelectionWorkflowTests(unittest.TestCase):
    def _case_dir(self, name: str) -> Path:
        path = Path(__file__).resolve().parent / "_tmp" / f"{name}-{uuid.uuid4().hex}"
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def test_build_review_payload_marks_manual_final_items(self) -> None:
        payload = build_llm_review_payload(
            trade_date=date(2026, 4, 20),
            provider_name="stock_model_vault_export",
            preselect_items=[
                SelectionItem(stock_id="2330", stock_name="TSMC", source="A+B", a_flag=True, b_flag=True),
                SelectionItem(stock_id="2317", stock_name="Hon Hai", source="A", basket_tag="secondary_add"),
            ],
            manual_final_list=[SelectionItem(stock_id="2330", stock_name="TSMC", source="A+B")],
        )
        self.assertEqual(payload["provider_name"], "stock_model_vault_export")
        self.assertEqual(len(payload["candidates"]), 2)
        self.assertTrue(payload["candidates"][0]["manual_final_flag"])
        self.assertFalse(payload["candidates"][1]["manual_final_flag"])
        self.assertEqual(payload["candidates"][1]["basket_tag"], "secondary_add")
        self.assertTrue(any("notes/Obsidian" in item for item in payload["instructions"]))

    def test_load_decisions_and_write_final_list_csv(self) -> None:
        temp_dir = self._case_dir("llm-decisions")
        decisions_path = temp_dir / "llm_selection_decisions.json"
        decisions_path.write_text(
            json.dumps(
                {
                    "trade_date": "2026-04-20",
                    "provider_name": "manual_csv",
                    "decisions": [
                        {
                            "stock_id": "2330",
                            "stock_name": "TSMC",
                            "source": "A+B",
                            "basket_tag": "secondary_add",
                            "source_weight": 2,
                            "selected": True,
                            "llm_reason": "leader and core benchmark name",
                            "target_qty": 2,
                        },
                        {
                            "stock_id": "2317",
                            "stock_name": "Hon Hai",
                            "source": "A",
                            "source_weight": 1,
                            "selected": False,
                            "llm_reason": "removed after review",
                        },
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        items = load_llm_decision_items(decisions_path)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].stock_id, "2330")
        self.assertEqual(items[0].target_qty, 2)
        self.assertEqual(items[0].basket_tag, "secondary_add")
        self.assertIn("leader", items[0].note)

        final_list_path = temp_dir / "auto_trade_final_list.csv"
        write_final_list_csv(final_list_path, items)
        content = final_list_path.read_text(encoding="utf-8-sig")
        self.assertIn("basket_tag", content)
        self.assertIn("secondary_add", content)
        self.assertIn("2330", content)
        self.assertNotIn("2317", content)

    def test_write_llm_review_bundle_seeds_decisions_from_manual_final_list(self) -> None:
        temp_dir = self._case_dir("llm-review-bundle")
        trade_date = date(2026, 4, 20)
        payload_path = temp_dir / "llm_selection_review_payload.json"
        template_path = temp_dir / "llm_selection_decisions.template.json"
        decisions_path = temp_dir / "llm_selection_decisions.json"
        brief_path = temp_dir / "2026-04-20-llm-selection-brief.md"
        with (
            patch("sinopac_auto_trading.llm_selection_workflow.input_dir_for", return_value=temp_dir),
            patch("sinopac_auto_trading.llm_selection_workflow.llm_selection_payload_path", return_value=payload_path),
            patch("sinopac_auto_trading.llm_selection_workflow.llm_selection_template_path", return_value=template_path),
            patch("sinopac_auto_trading.llm_selection_workflow.llm_selection_decisions_path", return_value=decisions_path),
            patch("sinopac_auto_trading.llm_selection_workflow.llm_selection_brief_path", return_value=brief_path),
        ):
            written = write_llm_review_bundle(
                trade_date=trade_date,
                provider_name="manual_csv",
                preselect_items=[SelectionItem(stock_id="2330", stock_name="TSMC", source="A+B")],
                manual_final_list=[SelectionItem(stock_id="2330", stock_name="TSMC", source="A+B")],
            )

        self.assertEqual(written["decisions_path"], decisions_path)
        decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
        self.assertEqual(len(decisions["decisions"]), 1)
        self.assertTrue(decisions["decisions"][0]["selected"])
        self.assertEqual(decisions["decisions"][0]["llm_reason"], "manual_final_list")


if __name__ == "__main__":
    unittest.main()
