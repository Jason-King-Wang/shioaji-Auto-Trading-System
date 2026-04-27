from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path

from sinopac_auto_trading.report_writer import render_daily_report
from tests.mojibake_guard import assert_text_has_no_known_mojibake


class ReportOutputCleanlinessTests(unittest.TestCase):
    def _case_dir(self, name: str) -> Path:
        path = Path(__file__).resolve().parent / "_tmp" / f"{name}-{uuid.uuid4().hex}"
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def test_daily_report_outputs_do_not_emit_known_mojibake_tokens(self) -> None:
        temp_dir = self._case_dir("report-cleanliness")
        markdown = temp_dir / "daily.md"
        html = temp_dir / "daily.html"
        snapshot = temp_dir / "snapshot.json"
        clean_selection_note = "已找到同日 A 預選 JSON，並完成本地整包產物同步。"
        clean_today_note = "今天已沒有任何自動新買單路徑可送出；2330 受保護下單路徑與整包買窗都已關閉。"
        input_dashboard_note = "最新 refresh 只重跑 render_report 與 workflow_status，不會覆蓋最近一次 materializing refresh。"
        clean_dashboard_note = "最新 refresh 只重跑 輸出日報 (render_report) 與 輸出工作流狀態 (workflow_status)，不會覆蓋最近一次 物化刷新。"
        clean_guard_note = "歷史保護條件問題已修好，但今天 09:10 已過，不會補單。"
        clean_sell_note = "今天的 受保護下單執行已錯過，請等待下一次排程，不會回補今天的單。"

        render_daily_report(
            {
                "trade_date": "2026-04-24",
                "week_id": "2026-04-20_2026-04-24",
                "run_id": "auto-2026-04-24",
                "mode": "live_guarded",
                "provider_name": "ab_llm_preselect_json",
                "overview": {
                    "weekly_budget": 0,
                    "hard_budget": 50000,
                    "used_cash": 0,
                    "remaining_cash": 50000,
                    "current_equity": 0,
                    "strategy_pnl_after_fee_tax": 0,
                    "strategy_return": 0,
                    "today_status": "buy_window_closed",
                    "today_status_note": "今天已沒有任何可送出的自動新買單路徑。",
                    "last_update_time": "2026-04-24T15:55:00+08:00",
                    "selection_source_status": "same_day_a_preselect_loaded",
                    "selection_source_note": clean_selection_note,
                    "dashboard_refresh_status": "report_only_refresh",
                    "dashboard_refresh_note": input_dashboard_note,
                    "today_ordering_status": "guarded_time_passed_no_backfill+basket_buy_window_closed_last_trade_day",
                    "today_ordering_note": clean_today_note,
                    "guarded_post_check_effective_recommendation": "historical_guard_issue_already_fixed_but_scheduled_time_passed_no_backfill",
                    "guarded_post_check_effective_recommendation_note": clean_guard_note,
                    "sell_loop_readiness_blocking_reason": "no_strategy_positions",
                    "sell_loop_readiness_next_action": "today_guarded_run_missed_wait_for_next_guarded_schedule_no_backfill",
                    "sell_loop_readiness_next_action_note": clean_sell_note,
                },
                "selection_rows": [],
                "buy_execution_rows": [],
                "positions_rows": [],
                "broker_underheld_rows": [],
                "ambiguous_fill_rows": [],
                "sell_rows": [],
                "basket_summary": {"basket_recommendation": "hold"},
                "comparison_chart": {"x_labels": ["15:55"], "series": [{"label": "Strategy", "values": [0.0]}]},
                "capital_chart": {"x_labels": ["15:55"], "series": [{"label": "Cash", "values": [50000]}]},
                "events": [],
                "warnings": [],
                "next_actions": ["等待下一個交易日的 fresh A 預選檔。"],
            },
            markdown,
            html,
            snapshot_json_path=snapshot,
        )

        markdown_text = markdown.read_text(encoding="utf-8")
        html_text = html.read_text(encoding="utf-8")
        snapshot_text = snapshot.read_text(encoding="utf-8")

        for text in (markdown_text, html_text, snapshot_text):
            assert_text_has_no_known_mojibake(self, text)

        self.assertIn(clean_selection_note, markdown_text)
        self.assertIn(clean_today_note, markdown_text)
        self.assertIn(clean_dashboard_note, markdown_text)
        self.assertIn(clean_guard_note, markdown_text)
        self.assertIn(clean_sell_note, markdown_text)
        self.assertIn(clean_selection_note, html_text)
        self.assertIn(clean_today_note, html_text)
        self.assertIn(clean_dashboard_note, html_text)
        self.assertIn(clean_guard_note, snapshot_text)
        self.assertIn(clean_sell_note, snapshot_text)


if __name__ == "__main__":
    unittest.main()
