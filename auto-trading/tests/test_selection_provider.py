from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
import shutil
import uuid

from sinopac_auto_trading.cli import _estimated_prices_for_finalize
from sinopac_auto_trading.finalizer import finalize_selection
from sinopac_auto_trading.providers import (
    AbLlmPreselectJsonSelectionProvider,
    GenericCsvSelectionProvider,
    ManualCsvSelectionProvider,
    MockSelectionProvider,
    StockModelVaultExportSelectionProvider,
)
from sinopac_auto_trading.selection_provider import SelectionItem


PRESELECT_CSV = """stock_id,stock_name,source,basket_tag,user_priority,note
2330,TSMC,A+B,main,1,Top pick
2317,Hon Hai,B,secondary_add,2,Backup
"""

FINAL_CSV = """stock_id,stock_name,source,basket_tag,target_qty,note
2454,MediaTek,manual,secondary_add,3,Manual override
"""


class SelectionProviderTests(unittest.TestCase):
    def _case_dir(self, name: str) -> Path:
        path = Path(__file__).resolve().parent / "_tmp" / f"{name}-{uuid.uuid4().hex}"
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def test_manual_final_list_has_priority(self) -> None:
        temp_dir = self._case_dir("manual-provider")
        day_dir = temp_dir / "2026-04-20"
        day_dir.mkdir(parents=True, exist_ok=True)
        (day_dir / "auto_trade_preselect.csv").write_text(PRESELECT_CSV, encoding="utf-8")
        (day_dir / "auto_trade_final_list.csv").write_text(FINAL_CSV, encoding="utf-8")

        provider = ManualCsvSelectionProvider(temp_dir)
        preselect = provider.load_preselect(date(2026, 4, 20))
        manual_final = provider.load_final_list(date(2026, 4, 20))

        result = finalize_selection(
            date(2026, 4, 20),
            preselect,
            provider.provider_name(),
            manual_final_list=manual_final,
        )
        self.assertTrue(result.used_manual_final_list)
        self.assertFalse(result.used_provider_final_list)
        self.assertEqual(result.final_list_origin, "manual_final_list")
        self.assertEqual([item.stock_id for item in result.final_items], ["2454"])
        self.assertEqual(result.final_items[0].basket_tag, "secondary_add")

    def test_generic_csv_reads_non_ab_source(self) -> None:
        temp_dir = self._case_dir("generic-provider")
        preselect = temp_dir / "generic.csv"
        preselect.write_text(
            "stock_id,stock_name,source,source_weight\n1101,TCC,model,1.5\n",
            encoding="utf-8",
        )
        provider = GenericCsvSelectionProvider(preselect_path=preselect)
        items = provider.load_preselect(date(2026, 4, 20))
        self.assertEqual(items[0].source, "model")
        self.assertEqual(items[0].source_weight, 1.5)

    def test_stock_model_vault_missing_path_has_clear_error(self) -> None:
        provider = StockModelVaultExportSelectionProvider(export_dir="Z:/missing/exports")
        with self.assertRaises(FileNotFoundError) as context:
            provider.load_preselect(date(2026, 4, 20))
        self.assertIn("manual_csv", str(context.exception))

    def test_ab_llm_preselect_json_provider_reads_same_day_a_list_as_final_list(self) -> None:
        temp_dir = self._case_dir("ab-llm-preselect")
        preselect_dir = temp_dir / "ab_llm_preselect"
        daily_output_dir = temp_dir / "ab_daily_output"
        preselect_dir.mkdir(parents=True, exist_ok=True)
        daily_output_dir.mkdir(parents=True, exist_ok=True)
        (preselect_dir / "2026-04-23.json").write_text(
            """
            {
              "trade_date": "2026-04-23",
              "a_preselect": [
                {"stock_id": "2330", "reason": "core AI leader"},
                {"stock_id": "2317", "reason": "AI assembly"}
              ],
              "b_preselect": [
                {"stock_id": "2330", "reason": "ignored for execution"}
              ]
            }
            """.strip(),
            encoding="utf-8",
        )
        (daily_output_dir / "2026-04-23.json").write_text(
            """
            {
              "rows": [
                {"stock_id": "2330", "stock_name": "TSMC", "theme": "AI", "role_level": "core", "close_price": 995},
                {"stock_id": "2317", "stock_name": "Hon Hai", "theme": "AI", "role_level": "satellite", "close_price": 151}
              ]
            }
            """.strip(),
            encoding="utf-8",
        )

        provider = AbLlmPreselectJsonSelectionProvider(
            preselect_dir=preselect_dir,
            daily_output_dir=daily_output_dir,
        )

        preselect = provider.load_preselect(date(2026, 4, 23))
        manual_final = provider.load_final_list(date(2026, 4, 23))

        self.assertEqual([item.stock_id for item in preselect], ["2330", "2317"])
        self.assertEqual([item.stock_name for item in preselect], ["TSMC", "Hon Hai"])
        self.assertTrue(all(item.source == "A" for item in preselect))
        self.assertTrue(all(item.source_weight == 1.0 for item in preselect))
        self.assertTrue(all(item.a_flag for item in preselect))
        self.assertTrue(all(item.b_flag is False for item in preselect))
        self.assertEqual(preselect[0].theme, "AI")
        self.assertEqual(preselect[0].role_level, "core")
        self.assertEqual(preselect[0].reference_price, 995.0)
        self.assertIn("ab_llm_preselect_source=2026-04-23.json", preselect[0].note)
        self.assertIn("source_trade_date=2026-04-23", preselect[0].note)
        self.assertEqual([item.stock_id for item in manual_final or []], ["2330", "2317"])

        result = finalize_selection(
            date(2026, 4, 23),
            preselect,
            provider.provider_name(),
            manual_final_list=manual_final,
        )
        self.assertFalse(result.used_manual_final_list)
        self.assertTrue(result.used_provider_final_list)
        self.assertEqual(result.final_list_origin, "same_day_a_preselect_final_list")
        self.assertTrue(all(decision.include_reason == "same_day_a_preselect_final_list" for decision in result.decisions))
        self.assertEqual([item.stock_id for item in result.final_items], ["2330", "2317"])

    def test_ab_llm_preselect_json_provider_excludes_b_only_items(self) -> None:
        temp_dir = self._case_dir("ab-llm-preselect-a-only")
        preselect_dir = temp_dir / "ab_llm_preselect"
        daily_output_dir = temp_dir / "ab_daily_output"
        preselect_dir.mkdir(parents=True, exist_ok=True)
        daily_output_dir.mkdir(parents=True, exist_ok=True)
        (preselect_dir / "2026-04-23.json").write_text(
            """
            {
              "trade_date": "2026-04-23",
              "a_preselect": [
                {"stock_id": "2330", "stock_name": "A Only"},
                {"stock_id": "2383", "stock_name": "A And B"}
              ],
              "b_preselect": [
                {"stock_id": "2383", "stock_name": "A And B"},
                {"stock_id": "2317", "stock_name": "B Only"}
              ]
            }
            """.strip(),
            encoding="utf-8",
        )
        (daily_output_dir / "2026-04-23.json").write_text(
            """
            {
              "rows": [
                {"stock_id": "2330", "stock_name": "A Only", "selection_tag": "A", "a_flag": 1, "b_flag": 0},
                {"stock_id": "2383", "stock_name": "A And B", "selection_tag": "AB", "a_flag": 1, "b_flag": 1},
                {"stock_id": "2317", "stock_name": "B Only", "selection_tag": "B", "a_flag": 0, "b_flag": 1}
              ]
            }
            """.strip(),
            encoding="utf-8",
        )

        provider = AbLlmPreselectJsonSelectionProvider(
            preselect_dir=preselect_dir,
            daily_output_dir=daily_output_dir,
        )

        preselect = provider.load_preselect(date(2026, 4, 23))
        final_list = provider.load_final_list(date(2026, 4, 23))

        self.assertEqual([item.stock_id for item in preselect], ["2330", "2383"])
        self.assertEqual([item.stock_id for item in final_list or []], ["2330", "2383"])
        self.assertNotIn("2317", {item.stock_id for item in preselect})
        self.assertTrue(all(item.source == "A" for item in preselect))
        self.assertTrue(all(item.source_weight == 1.0 for item in preselect))
        self.assertTrue(all(item.normalized_source_weight() == 1.0 for item in preselect))
        self.assertTrue(preselect[1].b_flag)

    def test_ab_llm_preselect_json_provider_reads_target_trade_date_a_list(self) -> None:
        temp_dir = self._case_dir("ab-llm-preselect-target-date")
        preselect_dir = temp_dir / "ab_llm_preselect"
        daily_output_dir = temp_dir / "ab_daily_output"
        preselect_dir.mkdir(parents=True, exist_ok=True)
        daily_output_dir.mkdir(parents=True, exist_ok=True)
        (preselect_dir / "2026-04-25.json").write_text(
            """
            {
              "trade_date": "2026-04-25",
              "target_trade_date": "2026-04-27",
              "source": "llm_vault_direct_preselect",
              "a_preselect": [
                {
                  "stock_id": "2454",
                  "stock_name": "MediaTek",
                  "theme": "AI edge",
                  "reason": "A reason",
                  "scores": {"FinalScore": 82},
                  "signals": {"close": 2435}
                },
                {
                  "stock_id": "2383",
                  "stock_name": "Elite Material",
                  "theme": "PCB",
                  "reason": "AB reason",
                  "scores": {"FinalScore": 74},
                  "signals": {"close": 4475}
                }
              ],
              "b_preselect": [
                {"stock_id": "2383", "stock_name": "Elite Material"}
              ]
            }
            """.strip(),
            encoding="utf-8",
        )
        (daily_output_dir / "2026-04-25.json").write_text(
            """
            {
              "trade_date": "2026-04-25",
              "source_target_trade_date": "2026-04-27",
              "preselect_source": "llm_vault_direct_preselect",
              "rows": [
                {
                  "stock_id": "2383",
                  "stock_name": "台光電",
                  "selection_tag": "AB",
                  "a_flag": 1,
                  "b_flag": 1,
                  "theme": "PCB/AI伺服器",
                  "close_price": "NA"
                },
                {
                  "stock_id": "2454",
                  "stock_name": "聯發科",
                  "selection_tag": "A",
                  "a_flag": 1,
                  "b_flag": 0,
                  "theme": "IC設計/邊緣AI",
                  "close_price": "NA"
                }
              ]
            }
            """.strip(),
            encoding="utf-8",
        )

        provider = AbLlmPreselectJsonSelectionProvider(
            preselect_dir=preselect_dir,
            daily_output_dir=daily_output_dir,
        )

        preselect = provider.load_preselect(date(2026, 4, 27))

        self.assertEqual([item.stock_id for item in preselect], ["2454", "2383"])
        self.assertEqual([item.stock_name for item in preselect], ["聯發科", "台光電"])
        self.assertEqual(preselect[0].reference_price, 2435.0)
        self.assertEqual(preselect[0].model_score, 82.0)
        self.assertFalse(preselect[0].b_flag)
        self.assertTrue(preselect[1].b_flag)
        self.assertEqual(preselect[1].source_weight, 1.0)
        self.assertIn("ab_llm_preselect_source=2026-04-25.json", preselect[0].note)
        self.assertIn("target_trade_date=2026-04-27", preselect[0].note)
        self.assertIn("ab_daily_output_source=2026-04-25.json", preselect[0].note)

    def test_ab_llm_preselect_json_provider_passes_when_trade_date_json_is_missing(self) -> None:
        temp_dir = self._case_dir("ab-llm-preselect-missing")
        preselect_dir = temp_dir / "ab_llm_preselect"
        preselect_dir.mkdir(parents=True, exist_ok=True)
        (preselect_dir / "2026-04-23.json").write_text(
            '{"trade_date": "2026-04-23", "a_preselect": [{"stock_id": "2330"}]}',
            encoding="utf-8",
        )
        provider = AbLlmPreselectJsonSelectionProvider(preselect_dir=preselect_dir)
        self.assertEqual(provider.load_preselect(date(2026, 4, 24)), [])
        self.assertEqual(provider.load_final_list(date(2026, 4, 24)), [])

    def test_ab_llm_preselect_json_provider_ignores_exact_file_with_wrong_target_date(self) -> None:
        temp_dir = self._case_dir("ab-llm-preselect-exact-wrong-target")
        preselect_dir = temp_dir / "ab_llm_preselect"
        preselect_dir.mkdir(parents=True, exist_ok=True)
        (preselect_dir / "2026-04-27.json").write_text(
            """
            {
              "trade_date": "2026-04-27",
              "target_trade_date": "2026-04-28",
              "a_preselect": [{"stock_id": "9999", "stock_name": "Wrong Target"}]
            }
            """.strip(),
            encoding="utf-8",
        )
        (preselect_dir / "2026-04-25.json").write_text(
            """
            {
              "trade_date": "2026-04-25",
              "target_trade_date": "2026-04-27",
              "a_preselect": [{"stock_id": "2454", "stock_name": "MediaTek"}]
            }
            """.strip(),
            encoding="utf-8",
        )

        provider = AbLlmPreselectJsonSelectionProvider(preselect_dir=preselect_dir)

        preselect = provider.load_preselect(date(2026, 4, 27))

        self.assertEqual([item.stock_id for item in preselect], ["2454"])
        self.assertIn("ab_llm_preselect_source=2026-04-25.json", preselect[0].note)

    def test_estimated_prices_use_reference_price_before_legacy_fallback(self) -> None:
        class _QuoteProvider:
            @staticmethod
            def get_snapshot(stock_id: str):
                if stock_id == "2330":
                    return type("Snapshot", (), {"last_price": 100.0})()
                return None

        estimated, unresolved = _estimated_prices_for_finalize(
            [
                SelectionItem(stock_id="2330", stock_name="TSMC", source="A", reference_price=995.0),
                SelectionItem(stock_id="9999", stock_name="Unknown", source="A"),
            ],
            quote_provider=_QuoteProvider(),
            prefer_reference_price=True,
        )

        self.assertEqual(estimated["2330"], 995.0)
        self.assertEqual(estimated["9999"], 10.0)
        self.assertEqual(unresolved, ["9999"])

    def test_mock_provider_works_without_stock_model_vault(self) -> None:
        provider = MockSelectionProvider()
        items = provider.load_preselect(date(2026, 4, 20))
        self.assertGreaterEqual(len(items), 1)


if __name__ == "__main__":
    unittest.main()
