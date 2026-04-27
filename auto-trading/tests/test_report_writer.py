from __future__ import annotations

import json
import unittest
from pathlib import Path
import shutil
import uuid

from sinopac_auto_trading.report_writer import render_daily_report, render_weekly_settlement


class ReportWriterTests(unittest.TestCase):
    def _case_dir(self, name: str) -> Path:
        path = Path(__file__).resolve().parent / "_tmp" / f"{name}-{uuid.uuid4().hex}"
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def test_daily_markdown_and_html_are_generated(self) -> None:
        temp_dir = self._case_dir("daily-report")
        markdown = temp_dir / "daily.md"
        html = temp_dir / "daily.html"
        current_html = temp_dir / "current.html"
        snapshot_json = temp_dir / "snapshot.json"
        render_daily_report(
            {
                "trade_date": "2026-04-20",
                "week_id": "2026-04-20_2026-04-24",
                "run_id": "auto-2026-04-20",
                "mode": "dry_run",
                "provider_name": "manual_csv",
                "overview": {
                    "weekly_budget": 450000,
                    "hard_budget": 500000,
                    "used_cash": 12000,
                    "remaining_cash": 488000,
                    "current_equity": 12100,
                    "strategy_pnl_after_fee_tax": 100,
                    "strategy_return": 0.01,
                    "today_status": "buying",
                    "last_update_time": "2026-04-20T10:30:00+08:00",
                    "position_data_quality": "fallback",
                    "positions_source_date": "2026-04-18",
                    "guarded_post_check_status": "skipped_config_live_disabled",
                    "guarded_post_check_recommendation": "enable_live_in_config_before_next_scheduled_run",
                    "guarded_post_check_effective_recommendation": "historical_guard_issue_already_fixed_wait_for_next_schedule",
                    "guarded_post_check_effective_recommendation_note": "歷史 guard 問題已修好，等待下一次排程；今天不補單。",
                    "guarded_post_check_next_run_guard_status": "live_guard_ready",
                    "guarded_post_check_next_run_guard_message": "目前設定、排程 runner 與 Windows 工作排程看起來都已準備好，可等待下一次 guarded 真實執行。",
                    "sell_loop_readiness_blocking_reason": "no_strategy_positions",
                    "sell_loop_readiness_next_action": "today_guarded_run_missed_wait_for_next_guarded_schedule_no_backfill",
                    "sell_loop_readiness_next_action_note": "今天 guarded 下單沒有建立策略部位，需等下一次 guarded 排程；今天不補單。",
                    "sell_loop_readiness_post_guarded_effective_recommendation": "historical_guard_issue_already_fixed_wait_for_next_schedule",
                    "sell_loop_readiness_post_guarded_effective_recommendation_note": "歷史 guard 問題已修好，等待下一次排程；今天不補單。",
                    "sell_loop_readiness_post_guarded_next_run_guard_status": "live_guard_ready",
                    "ambiguous_fill_guard_count": 2,
                    "excluded_position_guard_count": 1,
                    "broker_underheld_guard_count": 1,
                },
                "selection_rows": [
                    {
                        "stock_id": "2330",
                        "stock_name": "TSMC",
                        "source": "A+B",
                        "source_weight": 2,
                        "preselect_flag": True,
                        "final_flag": True,
                        "include_reason": "manual_final_list",
                        "exclude_reason": "",
                        "provider_name": "manual_csv",
                    }
                ],
                "buy_execution_rows": [
                    {
                        "stock_id": "2330",
                        "stock_name": "TSMC",
                        "target_qty": 1,
                        "bought_qty": 0,
                        "remaining_qty": 1,
                        "active_order_price": 950,
                        "current_mode": "normal",
                        "order_status_summary": "pending",
                        "next_check_time": "2026-04-20T10:35",
                    }
                ],
                "positions_rows": [],
                "broker_underheld_rows": [
                    {
                        "stock_id": "2330",
                        "stock_name": "TSMC",
                        "broker_qty": 4,
                        "strategy_qty": 5,
                        "missing_qty": 1,
                        "reason": "broker_qty_below_strategy_qty",
                    }
                ],
                "ambiguous_fill_rows": [
                    {
                        "stock_id": "2330",
                        "side": "Buy",
                        "fill_qty": 1,
                        "fill_price": 1000,
                        "fill_time": "2026-04-20T10:25:00+08:00",
                        "broker_fill_id": "UNKNOWN-1",
                        "broker_custom_field": "",
                        "fill_assignment_status": "ambiguous_unmapped_fill",
                    }
                ],
                "sell_rows": [],
                "basket_summary": {"basket_recommendation": "hold"},
                "comparison_chart": {"x_labels": ["10:30"], "series": [{"label": "Strategy", "values": [0.01]}]},
                "capital_chart": {"x_labels": ["10:30"], "series": [{"label": "Cash", "values": [488000]}]},
                "events": [
                    {
                        "time": "2026-04-20T10:30:00",
                        "event_type": "finalize",
                        "stock_id": "2330",
                        "action": "finalize",
                        "price": "",
                        "qty": "",
                        "result": "done",
                        "warning_or_error": "provider=manual_csv; after=skipped_config_live_disabled; current_step=historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill; blocking=no_strategy_positions; next_action=align_a_source_timing_or_basket_buy_window_rule",
                    },
                    {
                        "time": "2026-04-20T10:31:00",
                        "event_type": "post_guarded_order_check",
                        "stock_id": "",
                        "action": "post_guarded_order_check",
                        "price": "",
                        "qty": "",
                        "result": "Checked guarded live order artifacts: after=skipped_config_live_disabled, current_step=historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill.",
                        "warning_or_error": "",
                    },
                    {
                        "time": "2026-04-20T10:32:00",
                        "event_type": "sell_loop_readiness",
                        "stock_id": "",
                        "action": "sell_loop_readiness",
                        "price": "",
                        "qty": "",
                        "result": "Checked sell-loop readiness: blocking=no_strategy_positions, next_action=historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill.",
                        "warning_or_error": "",
                    },
                    {
                        "time": "2026-04-20T10:33:00",
                        "event_type": "workflow_status",
                        "stock_id": "",
                        "action": "workflow_status",
                        "price": "",
                        "qty": "",
                        "result": "Rendered workflow status with 17 checklist rows.",
                        "warning_or_error": "",
                    },
                    {
                        "time": "2026-04-20T10:34:00",
                        "event_type": "sell_loop_readiness",
                        "stock_id": "",
                        "action": "sell_loop_readiness",
                        "price": "",
                        "qty": "",
                        "result": "已檢查賣出就緒狀態：no_strategy_positions。",
                        "warning_or_error": "",
                    },
                ],
                "warnings": [],
                "next_actions": ["rerun render_report"],
            },
            markdown,
            html,
            snapshot_json_path=snapshot_json,
            current_html_path=current_html,
        )
        dry_run_label = "\u4e7e\u8dd1\u6a21\u5f0f"
        manual_csv_label = "\u624b\u52d5 CSV"
        fallback_label = "fallback \u5238\u5546\u5feb\u7167"
        normal_label = "\u4e00\u822c\u8ffd\u50f9"
        pending_label = "\u5f85\u8655\u7406"
        buy_execution_label = "\u8cb7\u9032\u57f7\u884c"
        events_label = "\u4e8b\u4ef6\u7d00\u9304"
        finalize_event_label = "\u8a02\u7248\u5b8c\u6210"
        finalize_action_label = "\u5b8c\u6210\u8a02\u7248"

        markdown_text = markdown.read_text(encoding="utf-8")
        html_text = html.read_text(encoding="utf-8")
        self.assertIn(f"模式: dry_run ({dry_run_label})", markdown_text)
        self.assertIn(f"啟用中的選股來源: manual_csv ({manual_csv_label})", markdown_text)
        self.assertIn(f"部位資料品質: fallback ({fallback_label})", markdown_text)
        self.assertIn(f"manual_csv ({manual_csv_label})", markdown_text)
        self.assertIn(f"normal ({normal_label})", markdown_text)
        self.assertIn(f"pending ({pending_label})", markdown_text)
        self.assertIn("\u5f85\u5c0d\u5e33\u6210\u4ea4 Guard \u6b21\u6578: 2", markdown_text)
        self.assertIn("\u6392\u9664\u90e8\u4f4d Guard \u6b21\u6578: 1", markdown_text)
        self.assertIn("\u5238\u5546\u6301\u80a1\u4e0d\u8db3 Guard \u6b21\u6578: 1", markdown_text)
        self.assertIn(
            "| \u80a1\u7968\u4ee3\u865f | \u80a1\u7968\u540d\u7a31 | \u4f86\u6e90 | \u6b0a\u91cd | \u9810\u9078 | \u8a02\u7248 | \u89d2\u8272\u5c64\u7d1a | \u4e3b\u984c | \u6a21\u578b\u5206\u6578 | \u8a02\u7248\u5206\u6578 | \u4fdd\u7559\u539f\u56e0 | \u6392\u9664\u539f\u56e0 | \u8cc7\u6599\u4f86\u6e90 |",
            markdown_text,
        )
        self.assertIn(
            "| \u80a1\u7968\u4ee3\u865f | \u80a1\u7968\u540d\u7a31 | Basket | \u76ee\u6a19\u80a1\u6578 | \u5df2\u8cb7 | \u5269\u9918 | \u59d4\u8a17\u55ae\u865f | \u59d4\u8a17\u50f9\u683c | \u59d4\u8a17\u80a1\u6578 | \u639b\u55ae\u6642\u9593 | \u6a21\u5f0f | \u73fe\u50f9 | \u8cb7\u4e00 | \u8ce3\u4e00 | \u5831\u50f9\u6642\u9593 | \u63d0\u4ea4 Gate | Tick \u5dee\u8ddd | \u4e0b\u6b21\u6aa2\u67e5 | \u59d4\u8a17\u72c0\u614b |",
            markdown_text,
        )
        self.assertIn("受保護下單目前有效建議: historical_guard_issue_already_fixed_wait_for_next_schedule", markdown_text)
        self.assertIn("受保護下單下次排程狀態: live_guard_ready", markdown_text)
        self.assertIn("賣出就緒目前有效建議: historical_guard_issue_already_fixed_wait_for_next_schedule", markdown_text)
        self.assertIn("賣出就緒下次排程狀態: live_guard_ready", markdown_text)
        self.assertIn("\u53d7\u4fdd\u8b77\u4e0b\u55ae\u5f8c\u6aa2\u67e5", markdown_text)
        self.assertIn("\u8ce3\u51fa\u5c31\u7dd2\u6aa2\u67e5", markdown_text)
        self.assertIn("已檢查受保護下單產物：after=skipped_config_live_disabled (因設定未開啟真實下單而略過)，current_step=historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill (保護條件已修好；交易視窗內應補跑)。", markdown_text)
        self.assertIn("已檢查賣出就緒狀態：blocking=no_strategy_positions (沒有策略部位)，next_action=historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill (保護條件已修好；交易視窗內應補跑)。", markdown_text)
        self.assertIn("已檢查賣出就緒狀態：no_strategy_positions (沒有策略部位)。", markdown_text)
        self.assertIn("已輸出工作流狀態，包含 17 筆清單列。", markdown_text)
        self.assertNotIn("Checked guarded live order artifacts", markdown_text)
        self.assertNotIn("Checked sell-loop readiness", markdown_text)
        self.assertNotIn("Rendered workflow status with", markdown_text)
        self.assertIn("\u53d7\u4fdd\u8b77\u4e0b\u55ae\u5f8c\u6aa2\u67e5", html_text)
        self.assertIn("\u53d7\u4fdd\u8b77\u4e0b\u55ae\u5efa\u8b70", html_text)
        self.assertIn("\u53d7\u4fdd\u8b77\u4e0b\u55ae\u76ee\u524d\u5efa\u8b70", html_text)
        self.assertIn("\u53d7\u4fdd\u8b77\u4e0b\u55ae\u6392\u7a0b\u8aaa\u660e", html_text)
        self.assertIn("已輸出工作流狀態，包含 17 筆清單列。", html_text)
        self.assertNotIn("Checked guarded live order artifacts", html_text)
        self.assertNotIn("Checked sell-loop readiness", html_text)
        self.assertNotIn("Rendered workflow status with", html_text)
        snapshot_text = snapshot_json.read_text(encoding="utf-8")
        self.assertIn("已檢查受保護下單產物：after=skipped_config_live_disabled", snapshot_text)
        self.assertIn("已檢查賣出就緒狀態：blocking=no_strategy_positions", snapshot_text)
        self.assertIn("已檢查賣出就緒狀態：no_strategy_positions (沒有策略部位)。", snapshot_text)
        self.assertIn("已輸出工作流狀態，包含 17 筆清單列。", snapshot_text)

    def test_weekly_settlement_contains_provider_and_benchmarks(self) -> None:
        temp_dir = self._case_dir("weekly-report")
        note = temp_dir / "weekly.md"
        html = temp_dir / "weekly.html"
        snapshot_json = temp_dir / "weekly.json"
        render_weekly_settlement(
            {
                "week_id": "2026-04-20_2026-04-24",
                "start_date": "2026-04-20",
                "end_date": "2026-04-24",
                "mode": "dry_run",
                "provider_name": "manual_csv",
                "last_update_time": "2026-04-24T14:00:00+08:00",
                "weekly_totals": {
                    "weekly_budget": 450000,
                    "hard_budget": 500000,
                    "total_buy_cost": 100000,
                    "final_market_value": 101500,
                    "total_profit": 1500,
                    "strategy_return": 0.015,
                },
                "benchmark_summary": {
                    "twii_return": 0.005,
                    "tsmc_return": 0.008,
                    "strategy_excess_vs_twii": 0.01,
                    "strategy_excess_vs_tsmc": 0.007,
                },
                "excluded_positions": [{"item": "legacy 0050"}],
                "broker_underheld_rows": [
                    {
                        "date": "2026-04-20",
                        "stock_id": "2330",
                        "stock_name": "TSMC",
                        "broker_qty": 4,
                        "strategy_qty": 5,
                        "missing_qty": 1,
                        "reason": "broker_qty_below_strategy_qty",
                    }
                ],
                "daily_rows": [{"date": "2026-04-20", "twii": "0.50%", "tsmc": "0.80%", "preselect": 3, "final_list": 2, "weighted_version": 2, "monday_plan": 2, "secondary_add": "N/A", "actual_combined": 1, "position_data_quality": "fallback", "fallback_lot_count": 2, "ambiguous_fill_guard_count": 1, "excluded_position_guard_count": 1, "broker_underheld_guard_count": 1, "positions_source_date": "2026-04-18"}],
                "trade_results": [{"label": "used_cash", "value": 100000}, {"label": "fallback_day_count", "value": 1}, {"label": "ambiguous_fill_guard_lot_count", "value": 1}, {"label": "excluded_position_guard_lot_count", "value": 1}, {"label": "broker_underheld_guard_lot_count", "value": 1}],
                "ambiguous_fill_rows": [
                    {
                        "date": "2026-04-24",
                        "stock_id": "2330",
                        "side": "Sell",
                        "fill_qty": 1,
                        "fill_price": 112,
                        "fill_time": "2026-04-24T13:05:00+08:00",
                        "broker_fill_id": "UNKNOWN-SELL-1",
                        "broker_custom_field": "",
                        "fill_assignment_status": "ambiguous_unmapped_fill",
                    }
                ],
                "lot_ledger_rows": [
                    {
                        "strategy_lot_id": "auto-2026-04-20:2330",
                        "stock_id": "2330",
                        "stock_name": "TSMC",
                        "buy_fill_qty": 1,
                        "sell_fill_qty": 0,
                        "closing_qty": 1,
                        "realized_pnl": 0,
                        "lot_status": "open",
                    }
                ],
                "expired_unfilled": [{"stock_id": "2317", "stop_day": "2026-04-22", "reason": "expired_unfilled"}],
                "tuning_suggestions": ["keep budget guard"],
                "comparison_chart": {"x_labels": ["2026-04-20"], "series": [{"label": "Strategy", "values": [0.015]}]},
            },
            note,
            html_path=html,
            snapshot_json_path=snapshot_json,
        )
        content = note.read_text(encoding="utf-8")
        self.assertIn("provider_name", content)
        self.assertIn("ambiguous_fill_guard_count", content)
        self.assertIn("excluded_position_guard_count", content)
        self.assertIn("broker_underheld_guard_count", content)
        self.assertIn("missing_qty", content)
        self.assertIn("fallback", content)
        self.assertIn("2026-04-18", content)
        self.assertIn("下週調參建議", content)
        self.assertIn("待對帳成交", content)
        html_text = html.read_text(encoding="utf-8")
        self.assertIn("fallback", html_text)
        self.assertIn("2026-04-18", html_text)
        self.assertIn("本週總表", html_text)
        self.assertIn("本週 Lot Ledger", html_text)
        self.assertIn("待對帳成交", html_text)
        self.assertIn("table-search", html_text)
        self.assertIn("page-nav", html_text)
        self.assertIn('"week_id": "2026-04-20_2026-04-24"', snapshot_json.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
