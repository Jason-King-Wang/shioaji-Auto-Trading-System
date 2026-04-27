from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path

from sinopac_auto_trading.report_writer import render_daily_report
from tests.mojibake_guard import assert_text_has_no_known_mojibake


class SellModelReportingTests(unittest.TestCase):
    def _case_dir(self, name: str) -> Path:
        path = Path(__file__).resolve().parent / "_tmp" / f"{name}-{uuid.uuid4().hex}"
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def test_daily_report_renders_buy_and_sell_model_gate_columns(self) -> None:
        temp_dir = self._case_dir("sell-model-report")
        markdown = temp_dir / "daily.md"
        html = temp_dir / "daily.html"
        render_daily_report(
            {
                "trade_date": "2026-04-24",
                "week_id": "2026-04-20_2026-04-24",
                "run_id": "auto-2026-04-24",
                "mode": "live",
                "provider_name": "manual_csv",
                "overview": {
                    "weekly_budget": 100000,
                    "hard_budget": 150000,
                    "used_cash": 80000,
                    "remaining_cash": 70000,
                    "current_equity": 86000,
                    "strategy_pnl_after_fee_tax": 6000,
                    "strategy_return": 0.075,
                    "today_status": "selling",
                    "last_update_time": "2026-04-24T13:21:00+08:00",
                },
                "selection_rows": [],
                "buy_execution_rows": [
                    {
                        "stock_id": "2330",
                        "stock_name": "TSMC",
                        "basket_tag": "secondary_add",
                        "target_qty": 10,
                        "bought_qty": 0,
                        "remaining_qty": 10,
                        "active_order_id": "OID001",
                        "active_order_price": 1110.0,
                        "active_order_qty": 10,
                        "order_age": "",
                        "current_mode": "normal",
                        "last_price": 1110.0,
                        "bid1": 1109.0,
                        "ask1": 1110.0,
                        "quote_timestamp": "2026-04-24T13:20:58+08:00",
                        "buy_submission_gate": "quote_fresh",
                        "tick_distance_to_target": 0,
                        "next_check_time": "2026-04-24T13:25:00+08:00",
                        "order_status_summary": "active",
                    }
                ],
                "positions_rows": [],
                "excluded_positions_rows": [],
                "sell_rows": [
                    {
                        "stock_id": "2330",
                        "basket_tag": "secondary_add",
                        "can_sell_flag": True,
                        "conservative_sell_price": 1120.0,
                        "conservative_profit": 4200.0,
                        "sell_decision": "sell",
                        "sell_decision_reason": "passed conservative threshold",
                        "basket_recommendation": "recommend_exit",
                        "basket_threshold": 3000.0,
                        "basket_loser_loss_ratio": 0.12,
                        "quote_timestamp": "2026-04-24T13:20:58+08:00",
                        "sell_submission_gate": "submitted_live",
                        "sell_order_price": 1120.0,
                        "sell_order_status": "Submitted",
                        "actual_fill_avg_price": "",
                        "sold_qty": 0,
                        "remaining_qty": 10,
                        "realized_pnl": "",
                        "sell_pnl_source": "local_sell_order_fallback",
                    }
                ],
                "basket_summary": {
                    "basket_scope": "multi_basket",
                    "basket_tags": "main,secondary_add",
                    "basket_market_value": 86000,
                    "basket_unrealized_pnl": 6000,
                    "basket_unrealized_pnl_pct": 0.075,
                    "basket_conservative_profit": 4200,
                    "basket_threshold": 3000,
                    "basket_recommendation": "recommend_exit",
                    "loser_loss_ratio": 0.12,
                },
                "comparison_chart": {"x_labels": ["13:21"], "series": [{"label": "Strategy", "values": [0.075]}]},
                "capital_chart": {"x_labels": ["13:21"], "series": [{"label": "Cash", "values": [70000]}]},
                "events": [],
                "warnings": [],
                "next_actions": [],
            },
            markdown,
            html,
        )

        markdown_text = markdown.read_text(encoding="utf-8")
        html_text = html.read_text(encoding="utf-8")

        self.assertIn("提交 Gate", markdown_text)
        self.assertIn("PnL 來源", markdown_text)
        self.assertIn("Basket", markdown_text)
        self.assertIn("報價時間", markdown_text)
        self.assertIn("提交 Gate", html_text)
        self.assertIn("整包建議", html_text)
        self.assertIn("報價時間", html_text)
        self.assertIn("normal (一般追價)", markdown_text)
        self.assertIn("active (有效掛單)", markdown_text)
        self.assertIn("一般追價 (normal)", html_text)
        self.assertIn("有效掛單 (active)", html_text)
        for text in (markdown_text, html_text):
            assert_text_has_no_known_mojibake(self, text)


if __name__ == "__main__":
    unittest.main()
