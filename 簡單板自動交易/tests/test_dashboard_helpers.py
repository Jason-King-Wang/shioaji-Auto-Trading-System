from __future__ import annotations

import os
import shutil
import unittest
import uuid
import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

from sinopac_auto_trading.calendar import WeekTradePlan
from sinopac_auto_trading.cli import (
    _build_buy_execution_rows,
    _build_daily_report,
    _build_positions_rows,
    _build_weekly_summary,
    _report_mode,
    _resolve_basket_summary,
)
from sinopac_auto_trading.config import AutoTradingConfig, FeeConfig, ProviderConfig, Settings


class DashboardHelperTests(unittest.TestCase):
    def test_report_mode_prefers_buy_loop_mode(self) -> None:
        self.assertEqual(_report_mode({"buy_loop": {"mode": "live"}}), "live")
        self.assertEqual(
            _report_mode({"post_guarded_order_check": {"after_status": "skipped_config_live_disabled"}}),
            "live_guarded",
        )
        self.assertEqual(_report_mode({"guarded_post_check_status": "skipped_config_live_disabled"}), "live_guarded")
        self.assertEqual(_report_mode({"workflow_type": "llm_assisted_selection"}), "llm_assisted_selection")
        self.assertEqual(_report_mode({}), "dry_run")

    def test_buy_execution_rows_use_filled_and_remaining_quantities(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"buy-exec-{uuid.uuid4().hex}"
        run_dir = base / "data" / "auto_trading" / "2026-04-22"
        run_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))

        (run_dir / "orders.csv").write_text(
            "\ufeffstock_id,stock_name,target_price,target_qty,filled_qty,remaining_qty,action,status,order_id,order_price,current_mode,last_price,bid1,ask1,quote_timestamp,buy_submission_gate,note\n"
            "2330,TSMC,1000,3,1,2,place,active,OID001,1000,add,1001,1000,1001,2026-04-22T09:20:00+08:00,quote_fresh,test\n",
            encoding="utf-8",
        )

        with patch("sinopac_auto_trading.cli.auto_trading_dir_for", return_value=run_dir):
            rows = _build_buy_execution_rows(
                trade_date=date(2026, 4, 22),
                selection_rows=[
                    {
                        "stock_id": "2330",
                        "stock_name": "TSMC",
                        "final_flag": True,
                        "target_qty": 3,
                        "estimated_buy_price": 1000,
                    }
                ],
            )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["bought_qty"], 1)
        self.assertEqual(rows[0]["remaining_qty"], 2)
        self.assertEqual(rows[0]["current_mode"], "add")
        self.assertEqual(rows[0]["buy_submission_gate"], "quote_fresh")
        self.assertEqual(rows[0]["quote_timestamp"], "2026-04-22T09:20:00+08:00")

    def test_basket_summary_prefers_saved_sell_loop_state_over_row_level_guess(self) -> None:
        settings = Settings(
            api_key=None,
            secret_key=None,
            person_id=None,
            ca_path=None,
            ca_password=None,
            default_simulation=True,
            allow_live_submit=False,
            default_order_lot="IntradayOdd",
            budget_per_order=100000,
            price_buffer_pct=0.3,
            max_orders=5,
            auto_trading=AutoTradingConfig(weekly_budget=100000, overrun_tolerance=50000),
            fees=FeeConfig(),
            providers=ProviderConfig(),
            project_root=Path.cwd(),
        )
        summary = _resolve_basket_summary(
            state_data={
                "sell_loop": {
                    "basket_recommendation": "hold",
                    "basket_threshold": 3000.0,
                    "loser_loss_ratio": 0.22,
                }
            },
            sell_rows=[
                {
                    "sell_decision": "sell",
                    "conservative_profit": 5000.0,
                    "basket_recommendation": "recommend_exit",
                }
            ],
            settings=settings,
            current_equity=150000.0,
            unrealized=8000.0,
            strategy_return=0.08,
        )
        self.assertEqual(summary["basket_recommendation"], "hold")
        self.assertEqual(summary["basket_threshold"], 3000.0)
        self.assertEqual(summary["loser_loss_ratio"], 0.22)

    def test_basket_summary_marks_mixed_when_multiple_baskets_disagree(self) -> None:
        settings = Settings(
            api_key=None,
            secret_key=None,
            person_id=None,
            ca_path=None,
            ca_password=None,
            default_simulation=True,
            allow_live_submit=False,
            default_order_lot="IntradayOdd",
            budget_per_order=100000,
            price_buffer_pct=0.3,
            max_orders=5,
            auto_trading=AutoTradingConfig(weekly_budget=100000, overrun_tolerance=50000),
            fees=FeeConfig(),
            providers=ProviderConfig(),
            project_root=Path.cwd(),
        )
        summary = _resolve_basket_summary(
            state_data={
                "sell_loop": {
                    "basket_summaries": {
                        "main": {
                            "recommendation": "recommend_exit",
                            "threshold": 3000.0,
                            "loser_loss_ratio": 0.10,
                        },
                        "secondary_add": {
                            "recommendation": "hold",
                            "threshold": 500.0,
                            "loser_loss_ratio": 0.05,
                        },
                    }
                }
            },
            sell_rows=[],
            settings=settings,
            current_equity=150000.0,
            unrealized=8000.0,
            strategy_return=0.08,
        )
        self.assertEqual(summary["basket_recommendation"], "mixed")
        self.assertEqual(summary["basket_threshold"], 3500.0)
        self.assertEqual(summary["basket_scope"], "multi_basket")
        self.assertEqual(summary["basket_tags"], "main,secondary_add")

    def test_daily_report_surfaces_post_guarded_order_check(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"daily-post-guard-{uuid.uuid4().hex}"
        run_dir = base / "2026-04-24"
        run_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        (run_dir / "post_guarded_order_check.json").write_text(
            json.dumps(
                {
                    "trade_date": "2026-04-24",
                    "before_status": "submitted_no_fills_yet",
                    "after_status": "submitted_no_fills_yet",
                    "reconciled": False,
                    "fills_count": 0,
                    "positions_count": 0,
                    "reports_rendered": False,
                    "workflow_status_rendered": False,
                    "recommendation": "run_reconcile_broker_state_after_market_updates",
                }
            ),
            encoding="utf-8",
        )
        settings = Settings(
            api_key=None,
            secret_key=None,
            person_id=None,
            ca_path=None,
            ca_password=None,
            default_simulation=True,
            allow_live_submit=False,
            default_order_lot="IntradayOdd",
            budget_per_order=100000,
            price_buffer_pct=0.3,
            max_orders=5,
            auto_trading=AutoTradingConfig(weekly_budget=100000, overrun_tolerance=50000),
            fees=FeeConfig(),
            providers=ProviderConfig(),
            project_root=Path.cwd(),
        )

        with patch("sinopac_auto_trading.cli.auto_trading_dir_for", return_value=run_dir):
            report = _build_daily_report(settings, date(2026, 4, 24))

        overview = report["overview"]
        self.assertEqual(overview["guarded_post_check_status"], "submitted_no_fills_yet")
        self.assertEqual(overview["guarded_post_check_recommendation"], "run_reconcile_broker_state_after_market_updates")
        self.assertFalse(overview["guarded_post_check_reconciled"])
        self.assertIn("受保護下單後檢查找到送單證據，但還沒有成交", "\n".join(report["warnings"]))

    def test_daily_report_surfaces_historical_guard_skip_with_future_ready_state(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"daily-post-guard-next-ready-{uuid.uuid4().hex}"
        run_dir = base / "2026-04-24"
        run_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        (run_dir / "post_guarded_order_check.json").write_text(
            json.dumps(
                {
                    "trade_date": "2026-04-24",
                    "before_status": "skipped_config_live_disabled",
                    "after_status": "skipped_config_live_disabled",
                    "reconciled": False,
                    "fills_count": 0,
                    "positions_count": 0,
                    "reports_rendered": False,
                    "workflow_status_rendered": False,
                    "recommendation": "enable_live_in_config_before_next_scheduled_run",
                    "next_run_guard_status": "live_guard_ready",
                    "next_run_guard_message": "目前設定、排程 runner 與 Windows 工作排程看起來都已準備好，可等待下一次 guarded 真實執行。",
                }
            ),
            encoding="utf-8",
        )
        settings = Settings(
            api_key=None,
            secret_key=None,
            person_id=None,
            ca_path=None,
            ca_password=None,
            default_simulation=True,
            allow_live_submit=False,
            default_order_lot="IntradayOdd",
            budget_per_order=100000,
            price_buffer_pct=0.3,
            max_orders=5,
            auto_trading=AutoTradingConfig(weekly_budget=100000, overrun_tolerance=50000),
            fees=FeeConfig(),
            providers=ProviderConfig(),
            project_root=Path.cwd(),
        )

        with patch("sinopac_auto_trading.cli.auto_trading_dir_for", return_value=run_dir):
            report = _build_daily_report(settings, date(2026, 4, 24))

        overview = report["overview"]
        self.assertEqual(overview["guarded_post_check_status"], "skipped_config_live_disabled")
        self.assertEqual(
            overview["guarded_post_check_effective_recommendation"],
            "historical_guard_issue_already_fixed_wait_for_next_schedule",
        )
        self.assertEqual(
            overview["guarded_post_check_effective_recommendation_note"],
            "歷史保護條件問題已修好，等待下一次排程；今天不補單。",
        )
        self.assertEqual(overview["guarded_post_check_next_run_guard_status"], "live_guard_ready")
        self.assertIn("可等待下一次受保護下單真實執行", overview["guarded_post_check_next_run_guard_message"])
        warnings_text = "\n".join(report["warnings"])
        self.assertIn("下一次排程現在已就緒", warnings_text)
        self.assertIn("09:10-13:20 視窗仍開著，排程應補跑", warnings_text)

    def test_daily_report_surfaces_guard_config_timing_evidence(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"daily-post-guard-config-timing-{uuid.uuid4().hex}"
        run_dir = base / "2026-04-24"
        run_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        config_timing_message = (
            "設定檔在 2026-04-24T11:18:15+08:00 才改成 auto_trading.live_enabled=true，"
            "晚於本次排程時間 2026-04-24T09:10:00+08:00；所以今天這次受保護下單執行仍被略過。"
        )
        (run_dir / "post_guarded_order_check.json").write_text(
            json.dumps(
                {
                    "trade_date": "2026-04-24",
                    "before_status": "skipped_config_live_disabled",
                    "after_status": "skipped_config_live_disabled",
                    "reconciled": False,
                    "fills_count": 0,
                    "positions_count": 0,
                    "reports_rendered": False,
                    "workflow_status_rendered": False,
                    "recommendation": "enable_live_in_config_before_next_scheduled_run",
                    "next_run_guard_status": "scheduled_task_time_passed",
                    "next_run_guard_message": "guard 設定現在看起來已修好，但 Windows 排程時間 2026-04-24T09:10:00+08:00 已過；錯過的 guarded 單不會自動補單。",
                    "config_timing_status": "live_enabled_fixed_after_scheduled_run",
                    "config_timing_message": config_timing_message,
                    "config_path": "config/auto_trading.yaml",
                    "config_last_modified": "2026-04-24T11:18:15+08:00",
                    "task_recorded_at": "2026-04-24T09:10:00+08:00",
                }
            ),
            encoding="utf-8",
        )
        settings = Settings(
            api_key=None,
            secret_key=None,
            person_id=None,
            ca_path=None,
            ca_password=None,
            default_simulation=True,
            allow_live_submit=False,
            default_order_lot="IntradayOdd",
            budget_per_order=100000,
            price_buffer_pct=0.3,
            max_orders=5,
            auto_trading=AutoTradingConfig(weekly_budget=100000, overrun_tolerance=50000),
            fees=FeeConfig(),
            providers=ProviderConfig(),
            project_root=Path.cwd(),
        )

        with patch("sinopac_auto_trading.cli.auto_trading_dir_for", return_value=run_dir):
            report = _build_daily_report(settings, date(2026, 4, 24))

        overview = report["overview"]
        self.assertEqual(overview["guarded_post_check_config_timing_status"], "live_enabled_fixed_after_scheduled_run")
        self.assertEqual(overview["guarded_post_check_config_last_modified"], "2026-04-24T11:18:15+08:00")
        self.assertEqual(overview["guarded_post_check_task_recorded_at"], "2026-04-24T09:10:00+08:00")
        self.assertIn(config_timing_message, "\n".join(report["warnings"]))

    def test_daily_report_surfaces_sell_loop_readiness(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"daily-sell-readiness-{uuid.uuid4().hex}"
        run_dir = base / "2026-04-24"
        run_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        (run_dir / "sell_loop_readiness.json").write_text(
            json.dumps(
                {
                    "trade_date": "2026-04-24",
                    "last_trade_day": "2026-04-24",
                    "is_last_trade_day": True,
                    "positions_ready": False,
                    "positions_count": 0,
                    "positions_source_date": "",
                    "post_guarded_status": "submitted_no_fills_yet",
                    "post_guarded_recommendation": "run_reconcile_broker_state_after_market_updates",
                    "fills_count": 0,
                    "sell_decisions_count": 0,
                    "blocking_reason": "broker_reconcile_recommended",
                    "next_action": "run_post_guarded_order_check_with_live_reconcile_after_market_updates",
                }
            ),
            encoding="utf-8",
        )
        settings = Settings(
            api_key=None,
            secret_key=None,
            person_id=None,
            ca_path=None,
            ca_password=None,
            default_simulation=True,
            allow_live_submit=False,
            default_order_lot="IntradayOdd",
            budget_per_order=100000,
            price_buffer_pct=0.3,
            max_orders=5,
            auto_trading=AutoTradingConfig(weekly_budget=100000, overrun_tolerance=50000),
            fees=FeeConfig(),
            providers=ProviderConfig(),
            project_root=Path.cwd(),
        )

        with patch("sinopac_auto_trading.cli.auto_trading_dir_for", return_value=run_dir):
            report = _build_daily_report(settings, date(2026, 4, 24))

        overview = report["overview"]
        self.assertEqual(overview["sell_loop_readiness_blocking_reason"], "broker_reconcile_recommended")
        self.assertEqual(
            overview["sell_loop_readiness_next_action"],
            "run_post_guarded_order_check_with_live_reconcile_after_market_updates",
        )
        self.assertEqual(
            overview["sell_loop_readiness_next_action_note"],
            "等市場資料更新後，跑 post_guarded_order_check --live --reconcile 做只讀核對。",
        )
        self.assertFalse(overview["sell_loop_readiness_positions_ready"])
        self.assertIn("唯讀 broker reconciliation", "\n".join(report["warnings"]))

    def test_build_daily_report_next_actions_shift_when_no_auto_new_buy_paths_remain(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"daily-next-actions-{uuid.uuid4().hex}"
        run_dir = base / "2026-04-24"
        source_dir = base / "data" / "ab_llm_preselect"
        run_dir.mkdir(parents=True, exist_ok=True)
        source_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        (source_dir / "2026-04-24.json").write_text("{}", encoding="utf-8")
        (run_dir / "post_guarded_order_check.json").write_text(
            json.dumps(
                {
                    "trade_date": "2026-04-24",
                    "before_status": "skipped_config_live_disabled",
                    "after_status": "skipped_config_live_disabled",
                    "reconciled": False,
                    "fills_count": 0,
                    "positions_count": 0,
                    "recommendation": "enable_live_in_config_before_next_scheduled_run",
                    "effective_recommendation": "historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill",
                    "next_run_guard_status": "scheduled_task_time_passed",
                    "next_run_guard_message": "guard 設定現在看起來已修好，但 Windows 排程時間 2026-04-24T09:10:00+08:00 已過；錯過的 guarded 單不會自動補單。",
                }
            ),
            encoding="utf-8",
        )
        settings = Settings(
            api_key=None,
            secret_key=None,
            person_id=None,
            ca_path=None,
            ca_password=None,
            default_simulation=True,
            allow_live_submit=False,
            default_order_lot="IntradayOdd",
            budget_per_order=100000,
            price_buffer_pct=0.3,
            max_orders=5,
            auto_trading=AutoTradingConfig(weekly_budget=100000, overrun_tolerance=50000),
            fees=FeeConfig(),
            providers=ProviderConfig(
                active="ab_llm_preselect_json",
                definitions={"ab_llm_preselect_json": {"preselect_dir": "data/ab_llm_preselect"}},
            ),
            project_root=base,
        )

        with patch("sinopac_auto_trading.cli.auto_trading_dir_for", return_value=run_dir):
            report = _build_daily_report(settings, date(2026, 4, 24))

        self.assertEqual(
            report["overview"]["today_new_order_submission_status"],
            "no_auto_new_buy_paths_remaining_today",
        )
        self.assertIn("今天不會再有新的自動買單", report["next_actions"][0])
        self.assertIn("同日 A 來源", report["next_actions"][1])

    def test_build_daily_report_next_actions_stop_repeating_settle_week_when_weekly_is_current(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"daily-next-actions-weekly-current-{uuid.uuid4().hex}"
        run_dir = base / "2026-04-24"
        source_dir = base / "data" / "ab_llm_preselect"
        run_dir.mkdir(parents=True, exist_ok=True)
        source_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        (source_dir / "2026-04-24.json").write_text("{}", encoding="utf-8")
        weekly_note = base / "notes" / "weeks" / "2026-04-20_2026-04-24_auto_trading_weekly.md"
        weekly_html = base / "reports" / "auto_trading" / "weeks" / "2026-04-20_2026-04-24.html"
        weekly_snapshot = base / "reports" / "auto_trading" / "data" / "2026-04-20_2026-04-24_weekly_snapshot.json"
        for path in (weekly_note, weekly_html, weekly_snapshot):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("ok", encoding="utf-8")
        (run_dir / "state.json").write_text(
            json.dumps(
                {
                    "weekly_outputs": {
                        "weekly_note": str(weekly_note),
                        "weekly_html": str(weekly_html),
                        "weekly_snapshot_json": str(weekly_snapshot),
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (run_dir / "post_guarded_order_check.json").write_text(
            json.dumps(
                {
                    "trade_date": "2026-04-24",
                    "before_status": "skipped_config_live_disabled",
                    "after_status": "skipped_config_live_disabled",
                    "reconciled": False,
                    "fills_count": 0,
                    "positions_count": 0,
                    "recommendation": "enable_live_in_config_before_next_scheduled_run",
                    "effective_recommendation": "historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill",
                    "next_run_guard_status": "scheduled_task_time_passed",
                    "next_run_guard_message": "guard 設定現在看起來已修好，但 Windows 排程時間 2026-04-24T09:10:00+08:00 已過；錯過的 guarded 單不會自動補單。",
                }
            ),
            encoding="utf-8",
        )
        settings = Settings(
            api_key=None,
            secret_key=None,
            person_id=None,
            ca_path=None,
            ca_password=None,
            default_simulation=True,
            allow_live_submit=False,
            default_order_lot="IntradayOdd",
            budget_per_order=100000,
            price_buffer_pct=0.3,
            max_orders=5,
            auto_trading=AutoTradingConfig(weekly_budget=100000, overrun_tolerance=50000),
            fees=FeeConfig(),
            providers=ProviderConfig(
                active="ab_llm_preselect_json",
                definitions={"ab_llm_preselect_json": {"preselect_dir": "data/ab_llm_preselect"}},
            ),
            project_root=base,
        )

        with patch("sinopac_auto_trading.cli.auto_trading_dir_for", return_value=run_dir):
            report = _build_daily_report(settings, date(2026, 4, 24))

        self.assertEqual(report["overview"]["weekly_settlement_status"], "weekly_settlement_current")
        self.assertIn("本週結算產物已是最新", report["next_actions"][-1])
        self.assertNotIn("settle_week", report["next_actions"][-1])

    def test_build_daily_report_marks_buy_window_closed_when_selection_exists_but_no_new_submissions_remain(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"daily-status-buy-window-closed-{uuid.uuid4().hex}"
        run_dir = base / "data" / "auto_trading" / "2026-04-24"
        input_dir = base / "data" / "inputs" / "2026-04-24"
        source_dir = base / "data" / "ab_llm_preselect"
        run_dir.mkdir(parents=True, exist_ok=True)
        input_dir.mkdir(parents=True, exist_ok=True)
        source_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        source_path = source_dir / "2026-04-24.json"
        source_path.write_text("{}", encoding="utf-8")
        (run_dir / "preselect.csv").write_text(
            "stock_id,stock_name,source,basket_tag,source_weight,provider_name\n2330,TSMC,A,main,1,ab_llm_preselect_json\n",
            encoding="utf-8",
        )
        (input_dir / "auto_trade_final_list.csv").write_text(
            "stock_id,stock_name,basket_tag,include_reason\n2330,TSMC,main,manual_final_list\n",
            encoding="utf-8",
        )
        (run_dir / "sizing.csv").write_text(
            "stock_id,stock_name,basket_tag,target_qty,estimated_buy_price,projected_cost\n2330,TSMC,main,2,2050,4181.8\n",
            encoding="utf-8",
        )
        (run_dir / "post_guarded_order_check.json").write_text(
            json.dumps(
                {
                    "trade_date": "2026-04-24",
                    "before_status": "skipped_config_live_disabled",
                    "after_status": "skipped_config_live_disabled",
                    "reconciled": False,
                    "fills_count": 0,
                    "positions_count": 0,
                    "recommendation": "enable_live_in_config_before_next_scheduled_run",
                    "effective_recommendation": "historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill",
                    "next_run_guard_status": "scheduled_task_time_passed",
                    "next_run_guard_message": "guard 設定現在看起來已修好，但 Windows 排程時間 2026-04-24T09:10:00+08:00 已過；錯過的 guarded 單不會自動補單。",
                }
            ),
            encoding="utf-8",
        )
        settings = Settings(
            api_key=None,
            secret_key=None,
            person_id=None,
            ca_path=None,
            ca_password=None,
            default_simulation=True,
            allow_live_submit=False,
            default_order_lot="IntradayOdd",
            budget_per_order=100000,
            price_buffer_pct=0.3,
            max_orders=5,
            auto_trading=AutoTradingConfig(weekly_budget=100000, overrun_tolerance=50000),
            fees=FeeConfig(),
            providers=ProviderConfig(
                active="ab_llm_preselect_json",
                definitions={"ab_llm_preselect_json": {"preselect_dir": "data/ab_llm_preselect"}},
            ),
            project_root=base,
        )

        with patch("sinopac_auto_trading.cli.auto_trading_dir_for", return_value=run_dir), patch(
            "sinopac_auto_trading.cli.input_dir_for", return_value=input_dir
        ):
            report = _build_daily_report(settings, date(2026, 4, 24))

        self.assertEqual(report["overview"]["today_new_order_submission_status"], "no_auto_new_buy_paths_remaining_today")
        self.assertEqual(report["overview"]["today_status"], "buy_window_closed")
        self.assertIn("選股與 sizing 產物已存在", report["overview"]["today_status_note"])

    def test_build_daily_report_selection_rows_mark_same_day_a_provider_final_list_origin(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"daily-selection-origin-{uuid.uuid4().hex}"
        run_dir = base / "data" / "auto_trading" / "2026-04-24"
        input_dir = base / "data" / "inputs" / "2026-04-24"
        source_dir = base / "data" / "ab_llm_preselect"
        run_dir.mkdir(parents=True, exist_ok=True)
        input_dir.mkdir(parents=True, exist_ok=True)
        source_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        source_path = source_dir / "2026-04-24.json"
        source_path.write_text("{}", encoding="utf-8")
        (run_dir / "preselect.csv").write_text(
            "stock_id,stock_name,source,basket_tag,source_weight,provider_name\n2330,TSMC,A,main,1,ab_llm_preselect_json\n",
            encoding="utf-8",
        )
        (input_dir / "auto_trade_preselect.csv").write_text(
            "stock_id,stock_name,source,basket_tag,source_weight\n2330,TSMC,A,main,1\n",
            encoding="utf-8",
        )
        (input_dir / "auto_trade_final_list.csv").write_text(
            "stock_id,stock_name,source,basket_tag\n2330,TSMC,A,main\n",
            encoding="utf-8",
        )
        (run_dir / "sizing.csv").write_text(
            "stock_id,stock_name,basket_tag,target_qty,estimated_buy_price,projected_cost\n2330,TSMC,main,2,2050,4181.8\n",
            encoding="utf-8",
        )
        settings = Settings(
            api_key=None,
            secret_key=None,
            person_id=None,
            ca_path=None,
            ca_password=None,
            default_simulation=True,
            allow_live_submit=False,
            default_order_lot="IntradayOdd",
            budget_per_order=100000,
            price_buffer_pct=0.3,
            max_orders=5,
            auto_trading=AutoTradingConfig(weekly_budget=100000, overrun_tolerance=50000),
            fees=FeeConfig(),
            providers=ProviderConfig(
                active="ab_llm_preselect_json",
                definitions={"ab_llm_preselect_json": {"preselect_dir": "data/ab_llm_preselect"}},
            ),
            project_root=base,
        )

        with patch("sinopac_auto_trading.cli.auto_trading_dir_for", return_value=run_dir), patch(
            "sinopac_auto_trading.cli.input_dir_for", return_value=input_dir
        ):
            report = _build_daily_report(settings, date(2026, 4, 24))

        self.assertEqual(report["selection_rows"][0]["include_reason"], "same_day_a_preselect_final_list")

    def test_build_daily_report_prefers_current_selection_artifacts_over_missing_state_counts(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"daily-selection-current-{uuid.uuid4().hex}"
        run_dir = base / "data" / "auto_trading" / "2026-04-24"
        input_dir = base / "data" / "inputs" / "2026-04-24"
        source_dir = base / "data" / "ab_llm_preselect"
        run_dir.mkdir(parents=True, exist_ok=True)
        input_dir.mkdir(parents=True, exist_ok=True)
        source_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        source_path = source_dir / "2026-04-24.json"
        source_path.write_text("{}", encoding="utf-8")
        (run_dir / "preselect.csv").write_text(
            "stock_id,stock_name,source,basket_tag,source_weight,provider_name\n2330,TSMC,A,main,1,ab_llm_preselect_json\n",
            encoding="utf-8",
        )
        (input_dir / "auto_trade_preselect.csv").write_text(
            "stock_id,stock_name,source,basket_tag,source_weight\n2330,TSMC,A,main,1\n",
            encoding="utf-8",
        )
        (input_dir / "auto_trade_final_list.csv").write_text(
            "stock_id,stock_name,source,basket_tag\n2330,TSMC,A,main\n",
            encoding="utf-8",
        )
        (run_dir / "sizing.csv").write_text(
            "stock_id,stock_name,basket_tag,target_qty,estimated_buy_price,projected_cost\n2330,TSMC,main,2,2050,4181.8\n",
            encoding="utf-8",
        )
        (run_dir / "state.json").write_text(
            json.dumps(
                {
                    "trade_date": "2026-04-24",
                    "status": "workflow_status_rendered",
                    "provider_name": "ab_llm_preselect_json",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        source_stat = source_path.stat()
        os.utime(source_path, (source_stat.st_atime - 60, source_stat.st_mtime - 60))
        settings = Settings(
            api_key=None,
            secret_key=None,
            person_id=None,
            ca_path=None,
            ca_password=None,
            default_simulation=True,
            allow_live_submit=False,
            default_order_lot="IntradayOdd",
            budget_per_order=100000,
            price_buffer_pct=0.3,
            max_orders=5,
            auto_trading=AutoTradingConfig(weekly_budget=100000, overrun_tolerance=50000),
            fees=FeeConfig(),
            providers=ProviderConfig(
                active="ab_llm_preselect_json",
                definitions={"ab_llm_preselect_json": {"preselect_dir": "data/ab_llm_preselect"}},
            ),
            project_root=base,
        )

        with patch("sinopac_auto_trading.cli.auto_trading_dir_for", return_value=run_dir), patch(
            "sinopac_auto_trading.cli.input_dir_for", return_value=input_dir
        ):
            report = _build_daily_report(settings, date(2026, 4, 24))

        self.assertEqual(report["overview"]["selection_source_status"], "same_day_a_preselect_loaded")
        self.assertEqual(report["overview"]["selection_materialization_status"], "local_materialization_current")
        self.assertEqual(report["overview"]["today_ordering_status"], "basket_a_loaded+basket_buy_window_closed_last_trade_day")

    def test_build_daily_report_recovers_dashboard_refresh_from_event_log_when_state_summary_is_missing(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"daily-refresh-event-{uuid.uuid4().hex}"
        run_dir = base / "data" / "auto_trading" / "2026-04-24"
        input_dir = base / "data" / "inputs" / "2026-04-24"
        source_dir = base / "data" / "ab_llm_preselect"
        run_dir.mkdir(parents=True, exist_ok=True)
        input_dir.mkdir(parents=True, exist_ok=True)
        source_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        source_path = source_dir / "2026-04-24.json"
        source_path.write_text("{}", encoding="utf-8")
        (input_dir / "auto_trade_preselect.csv").write_text(
            "stock_id,stock_name,source,basket_tag,source_weight\n2330,TSMC,A,main,1\n",
            encoding="utf-8",
        )
        (input_dir / "auto_trade_final_list.csv").write_text(
            "stock_id,stock_name,source,basket_tag\n2330,TSMC,A,main\n",
            encoding="utf-8",
        )
        (run_dir / "preselect.csv").write_text(
            "stock_id,stock_name,source,basket_tag,source_weight,provider_name\n2330,TSMC,A,main,1,ab_llm_preselect_json\n",
            encoding="utf-8",
        )
        (run_dir / "sizing.csv").write_text(
            "stock_id,stock_name,basket_tag,target_qty,estimated_buy_price,projected_cost\n2330,TSMC,main,2,2050,4181.8\n",
            encoding="utf-8",
        )
        (run_dir / "state.json").write_text(
            json.dumps(
                {
                    "trade_date": "2026-04-24",
                    "status": "workflow_status_rendered",
                    "provider_name": "ab_llm_preselect_json",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (run_dir / "event_log.jsonl").write_text(
            json.dumps(
                {
                    "timestamp": "2026-04-24T13:16:09.977737+08:00",
                    "level": "INFO",
                    "event_type": "refresh_dashboard",
                    "stock_id": "",
                    "message": "Dashboard refresh completed with 4 steps.",
                    "metadata": {"steps_run": ["prepare_week", "finalize", "render_report", "workflow_status"]},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        source_stat = source_path.stat()
        os.utime(source_path, (source_stat.st_atime - 60, source_stat.st_mtime - 60))
        settings = Settings(
            api_key=None,
            secret_key=None,
            person_id=None,
            ca_path=None,
            ca_password=None,
            default_simulation=True,
            allow_live_submit=False,
            default_order_lot="IntradayOdd",
            budget_per_order=100000,
            price_buffer_pct=0.3,
            max_orders=5,
            auto_trading=AutoTradingConfig(weekly_budget=100000, overrun_tolerance=50000),
            fees=FeeConfig(),
            providers=ProviderConfig(
                active="ab_llm_preselect_json",
                definitions={"ab_llm_preselect_json": {"preselect_dir": "data/ab_llm_preselect"}},
            ),
            project_root=base,
        )

        with patch("sinopac_auto_trading.cli.auto_trading_dir_for", return_value=run_dir), patch(
            "sinopac_auto_trading.cli.input_dir_for", return_value=input_dir
        ):
            report = _build_daily_report(settings, date(2026, 4, 24))

        self.assertEqual(report["overview"]["dashboard_refresh_status"], "materialized_without_buy_loop")
        self.assertEqual(report["overview"]["dashboard_last_materialization_status"], "materialized_without_buy_loop")
        self.assertIn("prepare_week, finalize, render_report, workflow_status", report["overview"]["dashboard_refresh_steps"])

    def test_build_positions_rows_fallback_applies_local_sell_remaining_qty(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"positions-fallback-{uuid.uuid4().hex}"
        run_dir = base / "data" / "auto_trading" / "2026-04-24"
        run_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))

        settings = Settings(
            api_key=None,
            secret_key=None,
            person_id=None,
            ca_path=None,
            ca_password=None,
            default_simulation=True,
            allow_live_submit=False,
            default_order_lot="IntradayOdd",
            budget_per_order=100000,
            price_buffer_pct=0.3,
            max_orders=5,
            auto_trading=AutoTradingConfig(weekly_budget=100000, overrun_tolerance=50000),
            fees=FeeConfig(),
            providers=ProviderConfig(),
            project_root=Path.cwd(),
        )

        with patch("sinopac_auto_trading.cli.auto_trading_dir_for", return_value=run_dir):
            rows = _build_positions_rows(
                trade_date=date(2026, 4, 24),
                fees=settings.fees,
                auto=settings.auto_trading,
                buy_execution_rows=[
                    {
                        "strategy_lot_id": "auto-2026-04-24:2330",
                        "stock_id": "2330",
                        "stock_name": "TSMC",
                        "basket_tag": "main",
                        "bought_qty": 3,
                        "active_order_price": 100.0,
                        "last_price": 120.0,
                    }
                ],
                sell_rows=[
                    {
                        "strategy_lot_id": "auto-2026-04-24:2330",
                        "sold_qty": 1,
                        "remaining_qty": 2,
                    }
                ],
            )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["holding_qty"], 2)
        self.assertEqual(rows[0]["buy_avg_price"], 100.0)
        self.assertEqual(rows[0]["buy_total_cost"], 200.0)
        self.assertEqual(rows[0]["current_price"], 120.0)
        self.assertEqual(rows[0]["status"], "local_sell_fill_fallback")

    def test_build_positions_rows_fallback_uses_latest_week_positions_when_today_csv_missing(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"positions-week-fallback-{uuid.uuid4().hex}"
        run_root = base / "data" / "auto_trading"
        previous_run = run_root / "2026-04-22"
        current_run = run_root / "2026-04-24"
        previous_run.mkdir(parents=True, exist_ok=True)
        current_run.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))

        (previous_run / "positions.csv").write_text(
            "\ufeffstrategy_lot_id,stock_id,stock_name,source,basket_tag,holding_qty,buy_avg_price,buy_total_cost,current_price,status\n"
            "auto-2026-04-22:2330,2330,TSMC,A,main,3,100,300,118,strategy_fill_scoped\n",
            encoding="utf-8",
        )
        (previous_run / "quote_snapshots.csv").write_text(
            "\ufeffstock_id,stock_name,timestamp,last_price,bid1,ask1\n"
            "2330,TSMC,2026-04-22T13:00:00+08:00,121,120.5,121\n",
            encoding="utf-8",
        )
        plan = WeekTradePlan(
            anchor_date=date(2026, 4, 24),
            week_trade_days=[date(2026, 4, 21), date(2026, 4, 22), date(2026, 4, 24)],
            buy_cutoff_day=date(2026, 4, 22),
            last_trade_day=date(2026, 4, 24),
            calendar_missing_warning=False,
            source_path=None,
        )
        settings = Settings(
            api_key=None,
            secret_key=None,
            person_id=None,
            ca_path=None,
            ca_password=None,
            default_simulation=True,
            allow_live_submit=False,
            default_order_lot="IntradayOdd",
            budget_per_order=100000,
            price_buffer_pct=0.3,
            max_orders=5,
            auto_trading=AutoTradingConfig(weekly_budget=100000, overrun_tolerance=50000),
            fees=FeeConfig(),
            providers=ProviderConfig(),
            project_root=Path.cwd(),
        )

        with patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            side_effect=lambda trade_date: run_root / trade_date.isoformat(),
        ), patch(
            "sinopac_auto_trading.cli.resolve_week_trade_plan",
            return_value=plan,
        ):
            rows = _build_positions_rows(
                trade_date=date(2026, 4, 24),
                fees=settings.fees,
                auto=settings.auto_trading,
                buy_execution_rows=[],
                sell_rows=[
                    {
                        "strategy_lot_id": "auto-2026-04-22:2330",
                        "sold_qty": 1,
                        "remaining_qty": 2,
                    }
                ],
            )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["strategy_lot_id"], "auto-2026-04-22:2330")
        self.assertEqual(rows[0]["holding_qty"], 2)
        self.assertEqual(rows[0]["buy_avg_price"], 100.0)
        self.assertEqual(rows[0]["buy_total_cost"], 200.0)
        self.assertEqual(rows[0]["current_price"], 121.0)
        self.assertEqual(rows[0]["status"], "local_sell_fill_fallback")

    def test_build_weekly_summary_tracks_fallback_days_and_lot_counts(self) -> None:
        plan = WeekTradePlan(
            anchor_date=date(2026, 4, 24),
            week_trade_days=[date(2026, 4, 22), date(2026, 4, 24)],
            buy_cutoff_day=date(2026, 4, 22),
            last_trade_day=date(2026, 4, 24),
            calendar_missing_warning=False,
            source_path=None,
        )
        settings = Settings(
            api_key=None,
            secret_key=None,
            person_id=None,
            ca_path=None,
            ca_password=None,
            default_simulation=True,
            allow_live_submit=False,
            default_order_lot="IntradayOdd",
            budget_per_order=100000,
            price_buffer_pct=0.3,
            max_orders=5,
            auto_trading=AutoTradingConfig(weekly_budget=100000, overrun_tolerance=50000),
            fees=FeeConfig(),
            providers=ProviderConfig(),
            project_root=Path.cwd(),
        )
        daily_reports = {
            date(2026, 4, 22): {
                "selection_rows": [],
                "positions_rows": [],
                "excluded_positions_rows": [],
                "broker_underheld_rows": [],
                "ambiguous_fill_rows": [],
                "events": [],
                "comparison_chart": {"series": [{}, {"values": [0.01]}, {"values": [0.02]}]},
                "overview": {
                    "used_cash": 50000.0,
                    "current_equity": 52000.0,
                    "strategy_pnl_after_fee_tax": 2000.0,
                    "strategy_return": 0.04,
                    "position_data_quality": "direct",
                    "fallback_position_lot_count": 0,
                    "ambiguous_fill_guard_count": 0,
                    "excluded_position_guard_count": 0,
                    "broker_underheld_guard_count": 0,
                    "positions_source_date": "",
                },
                "mode": "live",
                "provider_name": "manual_csv",
                "sell_rows": [],
            },
            date(2026, 4, 24): {
                "selection_rows": [],
                "positions_rows": [{"holding_qty": 2}],
                "excluded_positions_rows": [],
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
                "ambiguous_fill_rows": [],
                "events": [],
                "comparison_chart": {"series": [{}, {"values": [0.03]}, {"values": [0.04]}]},
                "overview": {
                    "used_cash": 40000.0,
                    "current_equity": 43000.0,
                    "strategy_pnl_after_fee_tax": 3000.0,
                    "strategy_return": 0.075,
                    "position_data_quality": "fallback",
                    "fallback_position_lot_count": 2,
                    "ambiguous_fill_guard_count": 3,
                    "excluded_position_guard_count": 2,
                    "broker_underheld_guard_count": 2,
                    "positions_source_date": "2026-04-22",
                },
                "mode": "live",
                "provider_name": "manual_csv",
                "sell_rows": [],
            },
        }

        with patch("sinopac_auto_trading.cli.resolve_week_trade_plan", return_value=plan), patch(
            "sinopac_auto_trading.cli._build_daily_report",
            side_effect=lambda _settings, day: daily_reports[day],
        ), patch(
            "sinopac_auto_trading.cli._week_csv_rows",
            return_value=[],
        ), patch(
            "sinopac_auto_trading.cli.load_week_lot_ledger",
            return_value=[],
        ):
            summary = _build_weekly_summary(settings, date(2026, 4, 24))

        self.assertEqual(summary["daily_rows"][-1]["position_data_quality"], "fallback")
        self.assertEqual(summary["daily_rows"][-1]["fallback_lot_count"], 2)
        self.assertEqual(summary["daily_rows"][-1]["ambiguous_fill_guard_count"], 3)
        self.assertEqual(summary["daily_rows"][-1]["excluded_position_guard_count"], 2)
        self.assertEqual(summary["daily_rows"][-1]["broker_underheld_guard_count"], 2)
        self.assertEqual(summary["daily_rows"][-1]["positions_source_date"], "2026-04-22")
        self.assertEqual(summary["broker_underheld_rows"][0]["missing_qty"], 1)
        self.assertEqual(summary["broker_underheld_rows"][0]["date"], "2026-04-24")
        trade_results = {row["label"]: row["value"] for row in summary["trade_results"]}
        self.assertEqual(trade_results["ambiguous_fill_guard_day_count"], 1)
        self.assertEqual(trade_results["ambiguous_fill_guard_lot_count"], 3)
        self.assertEqual(trade_results["excluded_position_guard_day_count"], 1)
        self.assertEqual(trade_results["excluded_position_guard_lot_count"], 2)
        self.assertEqual(trade_results["broker_underheld_guard_day_count"], 1)
        self.assertEqual(trade_results["broker_underheld_guard_lot_count"], 2)
        self.assertEqual(trade_results["fallback_day_count"], 1)
        self.assertEqual(trade_results["fallback_position_lot_count"], 2)
        self.assertIn("fallback-heavy days", " ".join(summary["tuning_suggestions"]))
        self.assertIn("ambiguous-fill guards", " ".join(summary["tuning_suggestions"]))
        self.assertIn("excluded-position guards", " ".join(summary["tuning_suggestions"]))
        self.assertIn("broker-underheld guards", " ".join(summary["tuning_suggestions"]))

    def test_build_weekly_summary_uses_cumulative_realized_and_open_positions_for_totals(self) -> None:
        plan = WeekTradePlan(
            anchor_date=date(2026, 4, 24),
            week_trade_days=[date(2026, 4, 22), date(2026, 4, 24)],
            buy_cutoff_day=date(2026, 4, 22),
            last_trade_day=date(2026, 4, 24),
            calendar_missing_warning=False,
            source_path=None,
        )
        settings = Settings(
            api_key=None,
            secret_key=None,
            person_id=None,
            ca_path=None,
            ca_password=None,
            default_simulation=True,
            allow_live_submit=False,
            default_order_lot="IntradayOdd",
            budget_per_order=100000,
            price_buffer_pct=0.3,
            max_orders=5,
            auto_trading=AutoTradingConfig(weekly_budget=100000, overrun_tolerance=50000),
            fees=FeeConfig(),
            providers=ProviderConfig(),
            project_root=Path.cwd(),
        )
        daily_reports = {
            date(2026, 4, 22): {
                "selection_rows": [],
                "positions_rows": [
                    {
                        "strategy_lot_id": "auto-2026-04-22:2330",
                        "stock_id": "2330",
                        "holding_qty": 5,
                        "buy_total_cost": 500.0,
                        "current_price": 104.0,
                    }
                ],
                "excluded_positions_rows": [],
                "broker_underheld_rows": [],
                "ambiguous_fill_rows": [],
                "events": [],
                "comparison_chart": {"series": [{}, {"values": [0.01]}, {"values": [0.02]}]},
                "overview": {
                    "used_cash": 999.0,
                    "current_equity": 999.0,
                    "strategy_pnl_after_fee_tax": 999.0,
                    "strategy_return": 0.99,
                    "position_data_quality": "direct",
                    "fallback_position_lot_count": 0,
                    "ambiguous_fill_guard_count": 0,
                    "excluded_position_guard_count": 0,
                    "broker_underheld_guard_count": 0,
                    "positions_source_date": "",
                },
                "mode": "live",
                "provider_name": "manual_csv",
                "sell_rows": [],
            },
            date(2026, 4, 24): {
                "selection_rows": [],
                "positions_rows": [
                    {
                        "strategy_lot_id": "auto-2026-04-22:2330",
                        "stock_id": "2330",
                        "holding_qty": 2,
                        "buy_total_cost": 200.0,
                        "current_price": 121.0,
                    }
                ],
                "excluded_positions_rows": [],
                "broker_underheld_rows": [],
                "ambiguous_fill_rows": [],
                "events": [],
                "comparison_chart": {"series": [{}, {"values": [0.03]}, {"values": [0.04]}]},
                "overview": {
                    "used_cash": 888.0,
                    "current_equity": 888.0,
                    "strategy_pnl_after_fee_tax": 888.0,
                    "strategy_return": 0.88,
                    "position_data_quality": "direct",
                    "fallback_position_lot_count": 0,
                    "ambiguous_fill_guard_count": 0,
                    "excluded_position_guard_count": 0,
                    "broker_underheld_guard_count": 0,
                    "positions_source_date": "",
                },
                "mode": "live",
                "provider_name": "manual_csv",
                "sell_rows": [
                    {
                        "strategy_lot_id": "auto-2026-04-22:2330",
                        "allocated_buy_cost": 100.0,
                        "realized_pnl": 19.0,
                    }
                ],
            },
        }

        def _week_rows(_plan: WeekTradePlan, filename: str) -> list[dict[str, object]]:
            if filename == "sell_decisions.csv":
                return [{"allocated_buy_cost": 100.0, "realized_pnl": 19.0}]
            return []

        with patch("sinopac_auto_trading.cli.resolve_week_trade_plan", return_value=plan), patch(
            "sinopac_auto_trading.cli._build_daily_report",
            side_effect=lambda _settings, day: daily_reports[day],
        ), patch(
            "sinopac_auto_trading.cli._week_csv_rows",
            side_effect=_week_rows,
        ), patch(
            "sinopac_auto_trading.cli.load_week_lot_ledger",
            return_value=[],
        ):
            summary = _build_weekly_summary(settings, date(2026, 4, 24))

        trade_results = {row["label"]: row["value"] for row in summary["trade_results"]}
        self.assertEqual(trade_results["used_cash"], 200.0)
        self.assertEqual(trade_results["current_equity"], 242.0)
        self.assertEqual(trade_results["realized_pnl"], 19.0)
        self.assertEqual(trade_results["unrealized_pnl"], 42.0)
        self.assertEqual(trade_results["strategy_pnl_after_fee_tax"], 61.0)
        self.assertAlmostEqual(summary["weekly_totals"]["strategy_return"], 61.0 / 300.0)
        self.assertAlmostEqual(summary["benchmark_summary"]["strategy_excess_vs_twii"], (61.0 / 300.0) - 0.03)
        strategy_series = summary["comparison_chart"]["series"][0]["values"]
        self.assertEqual(len(strategy_series), 2)
        self.assertAlmostEqual(strategy_series[0], 20.0 / 500.0)
        self.assertAlmostEqual(strategy_series[1], 61.0 / 300.0)


if __name__ == "__main__":
    unittest.main()
