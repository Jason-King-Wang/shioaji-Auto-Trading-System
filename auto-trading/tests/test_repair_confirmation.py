from __future__ import annotations

import unittest

from sinopac_auto_trading.repair_confirmation import (
    build_repair_confirmation_rows,
    render_repair_confirmation_markdown,
    summarize_repair_confirmation,
)


class RepairConfirmationTests(unittest.TestCase):
    def test_report_separates_filled_active_and_not_submitted_rows(self) -> None:
        intended_rows = [
            {
                "strategy_lot_id": "2026-04-27-main-2330",
                "stock_id": "2330",
                "stock_name": "TSMC",
                "basket_tag": "main",
                "target_qty": 3,
                "broker_custom_field": "BL001",
            },
            {
                "strategy_lot_id": "2026-04-27-main-2454",
                "stock_id": "2454",
                "stock_name": "MediaTek",
                "basket_tag": "main",
                "target_qty": 2,
                "broker_custom_field": "BL002",
            },
            {
                "strategy_lot_id": "2026-04-27-main-2383",
                "stock_id": "2383",
                "stock_name": "Elite Material",
                "basket_tag": "main",
                "target_qty": 1,
                "broker_custom_field": "BL003",
            },
        ]
        broker_order_rows = [
            {
                "strategy_lot_id": "2026-04-27-main-2330",
                "stock_id": "2330",
                "status": "filled",
                "order_id": "B001",
                "order_qty": 3,
                "filled_qty": 3,
                "active_order_qty": 0,
                "broker_custom_field": "BL001",
            },
            {
                "strategy_lot_id": "2026-04-27-main-2454",
                "stock_id": "2454",
                "status": "active",
                "order_id": "B002",
                "order_qty": 2,
                "filled_qty": 1,
                "active_order_qty": 1,
                "broker_custom_field": "BL002",
            },
        ]

        rows = build_repair_confirmation_rows(
            intended_rows=intended_rows,
            order_rows=[],
            fill_rows=[],
            position_rows=[],
            broker_order_rows=broker_order_rows,
        )
        by_stock = {row["stock_id"]: row for row in rows}

        self.assertEqual(by_stock["2330"]["confirmation_status"], "filled")
        self.assertEqual(by_stock["2330"]["to_submit_qty"], 0)
        self.assertEqual(by_stock["2454"]["confirmation_status"], "sent_waiting_fill")
        self.assertEqual(by_stock["2454"]["missing_qty"], 1)
        self.assertEqual(by_stock["2454"]["to_submit_qty"], 0)
        self.assertEqual(by_stock["2383"]["confirmation_status"], "not_submitted")
        self.assertEqual(by_stock["2383"]["to_submit_qty"], 1)

        summary = summarize_repair_confirmation(rows)
        self.assertEqual(summary["intended_count"], 3)
        self.assertEqual(summary["bought_count"], 2)
        self.assertEqual(summary["active_count"], 1)
        self.assertEqual(summary["to_submit_count"], 1)
        self.assertEqual(summary["to_submit_qty"], 1)
        self.assertTrue(summary["approval_required"])
        self.assertTrue(summary["approval_allowed"])

    def test_ambiguous_fill_blocks_approval_allowed(self) -> None:
        rows = build_repair_confirmation_rows(
            intended_rows=[
                {
                    "strategy_lot_id": "2026-04-27-main-2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "target_qty": 2,
                },
                {
                    "strategy_lot_id": "2026-04-27-main-2454",
                    "stock_id": "2454",
                    "stock_name": "MediaTek",
                    "target_qty": 1,
                },
            ],
            order_rows=[],
            fill_rows=[
                {
                    "strategy_lot_id": "2026-04-27-main-2330",
                    "stock_id": "2330",
                    "side": "Buy",
                    "fill_qty": 1,
                    "fill_assignment_status": "ambiguous_unmapped_fill",
                }
            ],
            position_rows=[],
        )

        summary = summarize_repair_confirmation(rows)

        self.assertEqual(summary["ambiguous_count"], 1)
        self.assertTrue(summary["approval_required"])
        self.assertFalse(summary["approval_allowed"])

    def test_markdown_contains_bought_and_not_submitted_sections(self) -> None:
        rows = build_repair_confirmation_rows(
            intended_rows=[
                {
                    "strategy_lot_id": "2026-04-27-main-2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "target_qty": 1,
                },
                {
                    "strategy_lot_id": "2026-04-27-main-2454",
                    "stock_id": "2454",
                    "stock_name": "MediaTek",
                    "target_qty": 1,
                },
            ],
            order_rows=[],
            fill_rows=[{"strategy_lot_id": "2026-04-27-main-2330", "stock_id": "2330", "side": "Buy", "fill_qty": 1}],
            position_rows=[],
        )
        summary = summarize_repair_confirmation(rows)

        markdown = render_repair_confirmation_markdown(
            trade_date="2026-04-27",
            buy_source_trade_date="2026-04-27",
            generated_at="2026-04-27T10:05:00+08:00",
            email_to="ops@example.com",
            rows=rows,
            summary=summary,
        )

        self.assertIn("買了什麼 / 已成交", markdown)
        self.assertIn("什麼還沒買 / 尚未送出", markdown)
        self.assertIn("回覆方式", markdown)
        self.assertIn("請直接在回信裡寫清楚 2026-04-27 這次要怎麼處理", markdown)
        self.assertIn("我會依照你的回信內容執行", markdown)
        self.assertIn("all_future_live_basket_buy_repairs", markdown)
        self.assertIn("2330 TSMC", markdown)
        self.assertIn("2454 MediaTek", markdown)


if __name__ == "__main__":
    unittest.main()
