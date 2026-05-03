from __future__ import annotations

import json
import os
import shutil
import unittest
import uuid
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import patch

from sinopac_auto_trading.calendar import resolve_week_trade_plan
from sinopac_auto_trading.cli import (
    _ab_same_day_source_refresh_details,
    _ab_same_day_source_refresh_flags,
    _allowed_live_next_run_guard_summary,
    _allowed_live_task_log_evidence,
    _dashboard_last_materializing_summary,
    _dashboard_refresh_summary,
    _describe_workflow_action,
    _guarded_config_timing_summary,
    _normalize_event_message_for_display,
    _normalize_guarded_schedule_description,
    _resolve_last_materializing_refresh_payload,
    PostGuardedOrderCheckResult,
    SellLoopReadinessResult,
    command_refresh_dashboard,
    command_guarded_order_status,
    command_post_guarded_order_check,
    command_sell_loop_readiness,
    command_workflow_status,
    _guarded_live_order_status_summary,
    _guarded_live_task_warning,
    _next_repetition_run_at,
    _parse_scheduler_datetime,
    _parse_windows_task_duration,
    _post_guarded_order_check,
    _post_guarded_order_check_report_summary,
    _scheduled_task_evidence,
    _selection_source_carry_forward_summary,
    _selection_materialization_summary,
    _selection_source_summary,
    _sell_loop_readiness_report_summary,
    _summarize_scheduler_query_error,
    _today_ordering_summary,
    _today_ordering_conflict_resolution_summary,
    _sell_loop_readiness_summary,
    _workflow_status_markdown,
    _workflow_status_rows,
    command_render_report,
)
from sinopac_auto_trading.report_writer import render_daily_report
from sinopac_auto_trading.time_utils import TAIPEI
from tests.mojibake_guard import (
    BAD_MOJIBAKE_TOKENS,
    assert_text_has_no_known_mojibake,
    assert_text_has_no_legacy_english_status_tokens,
)


class WorkflowStatusTests(unittest.TestCase):
    _BAD_MOJIBAKE_TOKENS = BAD_MOJIBAKE_TOKENS

    def test_command_guarded_order_status_prints_recommendation_note(self) -> None:
        summary = {
            "trade_date": "2026-04-24",
            "stock_id": "2330",
            "status": "scheduled_waiting",
            "recommendation": "wait_for_scheduled_run",
            "recommendation_note": "等待 Windows 排程送出；目前尚未看到實際委託。",
            "schedule_task_name": "\\SinoPac2330BuyIntradayOdd0910",
            "task_json_status": "",
            "task_log_status": "",
            "task_log_exit_code": "",
            "schedule_status": "ready",
            "schedule_state": "Ready",
            "schedule_last_run_time": "",
            "schedule_last_task_result": "",
            "schedule_description": "price cap 2100",
            "schedule_message": "scheduled task query ok",
            "schedule_next_run_time": "2026-04-24T09:10:00+08:00",
            "chase_submitted": False,
            "chase_final_state": "",
            "orders_count": 0,
            "fills_count": 0,
            "positions_count": 0,
            "pnl_snapshots_count": 0,
            "run_dir": "C:/tmp/run",
        }
        stdout = StringIO()
        with patch("sinopac_auto_trading.cli.Settings.load", return_value=SimpleNamespace()), patch(
            "sinopac_auto_trading.cli._guarded_live_order_status_summary",
            return_value=summary,
        ), patch(
            "sinopac_auto_trading.cli._post_guarded_order_check_report_summary",
            return_value={
                "config_timing_status": "live_enabled_fixed_after_scheduled_run",
                "config_timing_message": "設定檔在 2026-04-24T11:18:15+08:00 才改成 auto_trading.live_enabled=true，晚於本次排程時間 2026-04-24T09:10:00+08:00；所以今天這次 guarded 執行仍被略過。",
            },
        ), patch(
            "sinopac_auto_trading.cli._allowed_live_next_run_guard_summary",
            return_value={"status": "live_guard_ready", "message": "目前設定、排程 runner 與 Windows 工作排程看起來都已準備好，可等待下一次 guarded 真實執行。"},
        ), redirect_stdout(stdout):
            exit_code = command_guarded_order_status(SimpleNamespace(trade_date="2026-04-24", json=False))

        output = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("recommendation_note: 等待 Windows 排程送出；目前尚未看到實際委託。", output)
        self.assertIn("current_recommendation: wait_for_scheduled_run", output)
        self.assertIn("current_recommendation_note: 等待 Windows 排程到預定時間自動執行。", output)
        self.assertIn("config_timing_status: live_enabled_fixed_after_scheduled_run", output)
        self.assertIn("config_timing_message: 設定檔在 2026-04-24T11:18:15+08:00 才改成 auto_trading.live_enabled=true", output)
        for token in self._BAD_MOJIBAKE_TOKENS:
            self.assertNotIn(token, output)

    def test_command_guarded_order_status_json_includes_current_recommendation_fields(self) -> None:
        summary = {
            "trade_date": "2026-04-24",
            "stock_id": "2330",
            "status": "scheduled_waiting",
            "recommendation": "wait_for_scheduled_run",
            "recommendation_note": "等待 Windows 排程送出；目前尚未看到實際委託。",
            "schedule_task_name": "\\SinoPac2330BuyIntradayOdd0910",
            "task_json_status": "",
            "task_log_status": "",
            "task_log_exit_code": "",
            "schedule_status": "ready",
            "schedule_state": "Ready",
            "schedule_last_run_time": "",
            "schedule_last_task_result": "",
            "schedule_description": "price cap 2100",
            "schedule_message": "scheduled task query ok",
            "schedule_next_run_time": "2026-04-24T09:10:00+08:00",
            "chase_submitted": False,
            "chase_final_state": "",
            "orders_count": 0,
            "fills_count": 0,
            "positions_count": 0,
            "pnl_snapshots_count": 0,
            "run_dir": "C:/tmp/run",
        }
        stdout = StringIO()
        with patch("sinopac_auto_trading.cli.Settings.load", return_value=SimpleNamespace()), patch(
            "sinopac_auto_trading.cli._guarded_live_order_status_summary",
            return_value=summary,
        ), patch(
            "sinopac_auto_trading.cli._post_guarded_order_check_report_summary",
            return_value={
                "config_timing_status": "live_enabled_fixed_after_scheduled_run",
                "config_timing_message": "設定檔在 2026-04-24T11:18:15+08:00 才改成 auto_trading.live_enabled=true，晚於本次排程時間 2026-04-24T09:10:00+08:00；所以今天這次 guarded 執行仍被略過。",
                "config_last_modified": "2026-04-24T11:18:15+08:00",
                "task_recorded_at": "2026-04-24T09:10:00+08:00",
            },
        ), patch(
            "sinopac_auto_trading.cli._allowed_live_next_run_guard_summary",
            return_value={
                "status": "live_guard_ready",
                "message": "目前設定、排程 runner 與 Windows 工作排程看起來都已準備好，可等待下一次 guarded 真實執行。",
            },
        ), redirect_stdout(stdout):
            exit_code = command_guarded_order_status(SimpleNamespace(trade_date="2026-04-24", json=True))

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["recommendation"], "wait_for_scheduled_run")
        self.assertEqual(payload["recommendation_note"], "等待 Windows 排程送出；目前尚未看到實際委託。")
        self.assertEqual(payload["current_recommendation"], "wait_for_scheduled_run")
        self.assertEqual(payload["current_recommendation_note"], "等待 Windows 排程到預定時間自動執行。")
        self.assertEqual(payload["next_run_guard_status"], "live_guard_ready")
        self.assertEqual(payload["config_timing_status"], "live_enabled_fixed_after_scheduled_run")
        self.assertEqual(payload["config_last_modified"], "2026-04-24T11:18:15+08:00")
        self.assertEqual(payload["task_recorded_at"], "2026-04-24T09:10:00+08:00")
        for token in self._BAD_MOJIBAKE_TOKENS:
            self.assertNotIn(token, json.dumps(payload, ensure_ascii=False))

    def test_command_post_guarded_order_check_prints_note_fields(self) -> None:
        result = SimpleNamespace(
            trade_date="2026-04-24",
            before_status="skipped_config_live_disabled",
            after_status="skipped_config_live_disabled",
            reconciled=False,
            fills_count=0,
            positions_count=0,
            sell_loop_readiness_recorded=True,
            reports_rendered=True,
            workflow_status_rendered=True,
            recommendation="enable_live_in_config_before_next_scheduled_run",
            recommendation_note="需先在設定中啟用 auto_trading.live_enabled。",
            effective_recommendation="historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill",
            effective_recommendation_note="guard 問題已修好，但今天排程時間已過，不會補單。",
        )
        stdout = StringIO()
        with patch("sinopac_auto_trading.cli.Settings.load", return_value=SimpleNamespace()), patch(
            "sinopac_auto_trading.cli._post_guarded_order_check",
            return_value=result,
        ), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            return_value=Path("C:/tmp/run"),
        ), redirect_stdout(stdout):
            exit_code = command_post_guarded_order_check(
                SimpleNamespace(
                    trade_date="2026-04-24",
                    live=False,
                    reconcile=False,
                    sell_loop_readiness=False,
                    render_report=False,
                    workflow_status=False,
                    json=False,
                )
            )

        output = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("recommendation_note: 需先在設定中啟用 auto_trading.live_enabled。", output)
        self.assertIn(
            "effective_recommendation_note: guard 問題已修好，但今天排程時間已過，不會補單。",
            output,
        )
        for token in self._BAD_MOJIBAKE_TOKENS:
            self.assertNotIn(token, output)

    def test_command_post_guarded_order_check_json_output_is_clean(self) -> None:
        result = PostGuardedOrderCheckResult(
            trade_date="2026-04-24",
            before_status="skipped_config_live_disabled",
            after_status="skipped_config_live_disabled",
            reconciled=False,
            fills_count=0,
            positions_count=0,
            sell_loop_readiness_recorded=True,
            reports_rendered=True,
            workflow_status_rendered=True,
            recommendation="enable_live_in_config_before_next_scheduled_run",
            recommendation_note="需先在設定中啟用 auto_trading.live_enabled。",
            effective_recommendation="historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill",
            effective_recommendation_note="guard 問題已修好，但今天排程時間已過，不會補單。",
        )
        stdout = StringIO()
        with patch("sinopac_auto_trading.cli.Settings.load", return_value=SimpleNamespace()), patch(
            "sinopac_auto_trading.cli._post_guarded_order_check",
            return_value=result,
        ), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            return_value=Path("C:/tmp/run"),
        ), redirect_stdout(stdout):
            exit_code = command_post_guarded_order_check(
                SimpleNamespace(
                    trade_date="2026-04-24",
                    live=False,
                    reconcile=False,
                    sell_loop_readiness=False,
                    render_report=False,
                    workflow_status=False,
                    json=True,
                )
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["recommendation_note"], "需先在設定中啟用 auto_trading.live_enabled。")
        self.assertEqual(
            payload["effective_recommendation_note"],
            "guard 問題已修好，但今天排程時間已過，不會補單。",
        )
        for token in self._BAD_MOJIBAKE_TOKENS:
            self.assertNotIn(token, json.dumps(payload, ensure_ascii=False))

    def test_command_sell_loop_readiness_prints_note_fields(self) -> None:
        result = SellLoopReadinessResult(
            trade_date="2026-04-24",
            last_trade_day="2026-04-24",
            is_last_trade_day=True,
            positions_ready=False,
            positions_count=0,
            positions_source_date="",
            post_guarded_status="skipped_config_live_disabled",
            post_guarded_recommendation="enable_live_in_config_before_next_scheduled_run",
            post_guarded_recommendation_note="需先在設定中啟用 auto_trading.live_enabled。",
            post_guarded_effective_recommendation="historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill",
            post_guarded_effective_recommendation_note="guard 問題已修好，但今天排程時間已過，不會補單。",
            post_guarded_next_run_guard_status="scheduled_task_time_passed",
            post_guarded_next_run_guard_message="guard 設定現在看起來已修好，但 Windows 排程時間 2026-04-24T09:10:00+08:00 已過；錯過的 guarded 單不會自動補單。",
            fills_count=0,
            sell_decisions_count=0,
            blocking_reason="no_strategy_positions",
            next_action="historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill",
            next_action_note="guard 問題已修好，但今天排程時間已過，不會補單。",
            post_guarded_config_timing_status="live_enabled_fixed_after_scheduled_run",
            post_guarded_config_timing_message="設定檔在 2026-04-24T11:18:15+08:00 才改成 auto_trading.live_enabled=true，晚於本次排程時間 2026-04-24T09:10:00+08:00；所以今天這次 guarded 執行仍被略過。",
            post_guarded_config_path="config/auto_trading.yaml",
            post_guarded_config_last_modified="2026-04-24T11:18:15+08:00",
            post_guarded_task_recorded_at="2026-04-24T09:10:00+08:00",
        )
        stdout = StringIO()
        with patch("sinopac_auto_trading.cli._write_sell_loop_readiness", return_value=result), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            return_value=Path("C:/tmp/run"),
        ), redirect_stdout(stdout):
            exit_code = command_sell_loop_readiness(
                SimpleNamespace(trade_date="2026-04-24", json=False)
            )

        output = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("post_guarded_recommendation_note: 需先在設定中啟用 auto_trading.live_enabled。", output)
        self.assertIn(
            "post_guarded_effective_recommendation_note: guard 問題已修好，但今天排程時間已過，不會補單。",
            output,
        )
        self.assertIn(
            "post_guarded_config_timing_status: live_enabled_fixed_after_scheduled_run",
            output,
        )
        self.assertIn(
            "post_guarded_config_last_modified: 2026-04-24T11:18:15+08:00",
            output,
        )
        self.assertIn("next_action_note: guard 問題已修好，但今天排程時間已過，不會補單。", output)
        for token in self._BAD_MOJIBAKE_TOKENS:
            self.assertNotIn(token, output)

    def test_command_sell_loop_readiness_json_output_is_clean(self) -> None:
        result = SellLoopReadinessResult(
            trade_date="2026-04-24",
            last_trade_day="2026-04-24",
            is_last_trade_day=True,
            positions_ready=False,
            positions_count=0,
            positions_source_date="",
            post_guarded_status="skipped_config_live_disabled",
            post_guarded_recommendation="enable_live_in_config_before_next_scheduled_run",
            post_guarded_recommendation_note="需先在設定中啟用 auto_trading.live_enabled。",
            post_guarded_effective_recommendation="historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill",
            post_guarded_effective_recommendation_note="guard 問題已修好，但今天排程時間已過，不會補單。",
            post_guarded_next_run_guard_status="scheduled_task_time_passed",
            post_guarded_next_run_guard_message="guard 設定現在看起來已修好，但 Windows 排程時間 2026-04-24T09:10:00+08:00 已過；錯過的 guarded 單不會自動補單。",
            fills_count=0,
            sell_decisions_count=0,
            blocking_reason="no_strategy_positions",
            next_action="historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill",
            next_action_note="guard 問題已修好，但今天排程時間已過，不會補單。",
            post_guarded_config_timing_status="live_enabled_fixed_after_scheduled_run",
            post_guarded_config_timing_message="設定檔在 2026-04-24T11:18:15+08:00 才改成 auto_trading.live_enabled=true，晚於本次排程時間 2026-04-24T09:10:00+08:00；所以今天這次 guarded 執行仍被略過。",
            post_guarded_config_path="config/auto_trading.yaml",
            post_guarded_config_last_modified="2026-04-24T11:18:15+08:00",
            post_guarded_task_recorded_at="2026-04-24T09:10:00+08:00",
        )
        stdout = StringIO()
        with patch("sinopac_auto_trading.cli._write_sell_loop_readiness", return_value=result), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            return_value=Path("C:/tmp/run"),
        ), redirect_stdout(stdout):
            exit_code = command_sell_loop_readiness(SimpleNamespace(trade_date="2026-04-24", json=True))

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["post_guarded_recommendation_note"], "需先在設定中啟用 auto_trading.live_enabled。")
        self.assertEqual(
            payload["post_guarded_effective_recommendation_note"],
            "guard 問題已修好，但今天排程時間已過，不會補單。",
        )
        self.assertEqual(
            payload["post_guarded_config_timing_status"],
            "live_enabled_fixed_after_scheduled_run",
        )
        self.assertEqual(
            payload["post_guarded_config_last_modified"],
            "2026-04-24T11:18:15+08:00",
        )
        self.assertEqual(payload["next_action_note"], "guard 問題已修好，但今天排程時間已過，不會補單。")
        for token in self._BAD_MOJIBAKE_TOKENS:
            self.assertNotIn(token, json.dumps(payload, ensure_ascii=False))

    def test_workflow_rows_detect_existing_artifacts(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"workflow-status-{uuid.uuid4().hex}"
        input_dir = base / "inputs"
        run_dir = base / "run"
        input_dir.mkdir(parents=True, exist_ok=True)
        run_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))

        (input_dir / "auto_trade_preselect.csv").write_text("stock_id\n2330\n", encoding="utf-8")
        (input_dir / "llm_selection_review_payload.json").write_text("{}", encoding="utf-8")
        (run_dir / "sizing.csv").write_text("stock_id\n2330\n", encoding="utf-8")

        trade_date = date(2026, 4, 20)
        rows = _workflow_status_rows(
            trade_date=trade_date,
            run_dir=run_dir,
            input_dir=input_dir,
            plan=resolve_week_trade_plan(trade_date),
        )

        statuses = {row["step"]: row["status"] for row in rows}
        self.assertEqual(statuses["prepare_week"], "done")
        self.assertEqual(statuses["prepare_llm_selection"], "done")
        self.assertEqual(statuses["finalize"], "done")
        self.assertEqual(statuses["buy_loop"], "pending")
        self.assertEqual(statuses["fills"], "pending")
        self.assertEqual(statuses["excluded_positions"], "pending")
        self.assertEqual(statuses["post_guarded_order_check"], "pending")

    def test_workflow_rows_skip_llm_handoff_for_direct_a_preselect_provider(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"workflow-status-direct-provider-{uuid.uuid4().hex}"
        input_dir = base / "inputs"
        run_dir = base / "run"
        input_dir.mkdir(parents=True, exist_ok=True)
        run_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))

        (input_dir / "auto_trade_preselect.csv").write_text("stock_id\n2330\n", encoding="utf-8")

        rows = _workflow_status_rows(
            trade_date=date(2026, 4, 24),
            run_dir=run_dir,
            input_dir=input_dir,
            plan=resolve_week_trade_plan(date(2026, 4, 24)),
            state_data={"provider_name": "ab_llm_preselect_json"},
        )

        checks = {row["step"]: row for row in rows}
        self.assertEqual(checks["prepare_llm_selection"]["status"], "done")
        self.assertEqual(checks["llm_decisions"]["status"], "done")
        self.assertIn("direct A 預選 provider", checks["prepare_llm_selection"]["check"])
        self.assertIn("final_list artifact 已就緒", checks["apply_llm_selection"]["check"])

    def test_workflow_rows_skip_llm_handoff_for_direct_a_preselect_provider_from_settings_when_state_missing(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"workflow-status-direct-provider-settings-{uuid.uuid4().hex}"
        input_dir = base / "inputs"
        run_dir = base / "run"
        input_dir.mkdir(parents=True, exist_ok=True)
        run_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))

        (input_dir / "auto_trade_preselect.csv").write_text("stock_id\n2330\n", encoding="utf-8")

        rows = _workflow_status_rows(
            trade_date=date(2026, 4, 24),
            run_dir=run_dir,
            input_dir=input_dir,
            plan=resolve_week_trade_plan(date(2026, 4, 24)),
            settings=SimpleNamespace(
                providers=SimpleNamespace(
                    active="ab_llm_preselect_json",
                    options=lambda _name: {"preselect_dir": "data/ab_llm_preselect"},
                ),
                project_root=base,
            ),
        )

        checks = {row["step"]: row for row in rows}
        self.assertEqual(checks["prepare_llm_selection"]["status"], "done")
        self.assertEqual(checks["llm_decisions"]["status"], "done")
        self.assertIn("direct A 預選 provider", checks["prepare_llm_selection"]["check"])

    def test_workflow_rows_mark_stale_direct_provider_artifacts_pending(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"workflow-status-direct-provider-stale-{uuid.uuid4().hex}"
        input_dir = base / "inputs"
        run_dir = base / "run"
        source_dir = base / "data" / "ab_llm_preselect"
        input_dir.mkdir(parents=True, exist_ok=True)
        run_dir.mkdir(parents=True, exist_ok=True)
        source_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))

        source_path = source_dir / "2026-04-24.json"
        source_path.write_text("{}", encoding="utf-8")
        for artifact in (
            input_dir / "auto_trade_preselect.csv",
            run_dir / "preselect.csv",
            run_dir / "sizing.csv",
        ):
            artifact.write_text("stale", encoding="utf-8")
            stale_time = source_path.stat().st_mtime - 60
            os.utime(artifact, (stale_time, stale_time))

        settings = SimpleNamespace(
            project_root=base,
            providers=SimpleNamespace(active="ab_llm_preselect_json", options=lambda _name: {"preselect_dir": "data/ab_llm_preselect"}),
        )

        rows = _workflow_status_rows(
            trade_date=date(2026, 4, 24),
            run_dir=run_dir,
            input_dir=input_dir,
            plan=resolve_week_trade_plan(date(2026, 4, 24)),
            state_data={"provider_name": "ab_llm_preselect_json"},
            settings=settings,
        )

        checks = {row["step"]: row for row in rows}
        self.assertEqual(checks["prepare_week"]["status"], "pending")
        self.assertIn("同日 A 來源比本地預選產物更新", checks["prepare_week"]["check"])
        self.assertEqual(checks["finalize"]["status"], "pending")
        self.assertIn("finalize 前需要先重跑 prepare_week", checks["finalize"]["check"])

    def test_workflow_rows_close_buy_artifacts_when_no_auto_new_buy_paths_remain_today(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"workflow-status-closed-buy-paths-{uuid.uuid4().hex}"
        input_dir = base / "inputs"
        run_dir = base / "run"
        input_dir.mkdir(parents=True, exist_ok=True)
        run_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))

        rows = _workflow_status_rows(
            trade_date=date(2026, 4, 24),
            run_dir=run_dir,
            input_dir=input_dir,
            plan=resolve_week_trade_plan(date(2026, 4, 24)),
            state_data={
                "today_new_order_submission_status": "no_auto_new_buy_paths_remaining_today",
                "sell_loop_readiness_blocking_reason": "no_strategy_positions",
                "sell_loop_readiness_positions_count": 0,
            },
        )

        checks = {row["step"]: row for row in rows}
        self.assertEqual(checks["track_until_final"]["status"], "closed")
        self.assertEqual(checks["buy_loop"]["status"], "closed")
        self.assertEqual(checks["fills"]["status"], "closed")
        self.assertEqual(checks["positions"]["status"], "closed")
        self.assertEqual(checks["pnl_snapshots"]["status"], "closed")
        self.assertIn("不再有實際作用", checks["track_until_final"]["check"])
        self.assertIn("視窗已關閉", checks["buy_loop"]["check"])
        self.assertIn("不會出現", checks["positions"]["check"])

    def test_workflow_markdown_lists_pending_steps(self) -> None:
        content = _workflow_status_markdown(
            trade_date=date(2026, 4, 20),
            state_data={
                "status": "prepared",
                "provider_name": "ab_llm_preselect_json",
                "preselect_count": 0,
                "final_list_count": 0,
            },
            rows=[
                {"step": "prepare_week", "status": "done", "check": "ok", "path": "a.csv"},
                {"step": "buy_loop", "status": "pending", "check": "missing", "path": "orders.csv"},
            ],
            live_task_evidence={
                "status": "failed",
                "exit_code": "2",
                "message": "can't open file",
                "path": "task.log",
            },
            scheduled_task_evidence={
                "status": "ready",
                "task_name": "SinoPac2330BuyIntradayOdd0910",
                "state": "Ready",
                "next_run_time": "2026-04-24T09:10:00+08:00",
                "last_run_time": "2026-04-23T09:10:00+08:00",
                "last_task_result": "2",
                "description": "price cap 2100",
                "message": "已讀到 Windows 工作排程狀態",
            },
            post_guarded_check={
                "after_status": "skipped_config_live_disabled",
                "recommendation": "enable_live_in_config_before_next_scheduled_run",
                "reconciled": False,
                "fills_count": 0,
                "positions_count": 0,
                "next_run_guard_status": "live_guard_ready",
                "next_run_guard_message": "目前設定、排程 runner 與 Windows 工作排程看起來都已準備好，可等待下一次 guarded 真實執行。",
            },
            sell_loop_readiness={
                "blocking_reason": "no_strategy_positions",
                "post_guarded_recommendation": "enable_live_in_config_before_next_scheduled_run",
                "next_action": "historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill",
                "positions_ready": False,
                "positions_count": 0,
                "post_guarded_effective_recommendation": "historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill",
                "post_guarded_next_run_guard_status": "scheduled_task_time_passed",
                "post_guarded_next_run_guard_message": "guard 設定現在看起來已修好，但 Windows 排程時間 2026-04-24T09:10:00+08:00 已過；錯過的 guarded 單不會自動補單。",
                "post_guarded_config_timing_status": "live_enabled_fixed_after_scheduled_run",
                "post_guarded_config_timing_message": "設定檔在 2026-04-24T11:18:15+08:00 才改成 auto_trading.live_enabled=true，晚於本次排程時間 2026-04-24T09:10:00+08:00；所以今天這次 guarded 執行仍被略過。",
                "post_guarded_config_path": "config/auto_trading.yaml",
                "post_guarded_config_last_modified": "2026-04-24T11:18:15+08:00",
                "post_guarded_task_recorded_at": "2026-04-24T09:10:00+08:00",
            },
        )
        self.assertIn("工作流狀態", content)
        self.assertIn("| 步驟 | 狀態 | 檢查 | 路徑 |", content)
        self.assertIn("prepare_week", content)
        self.assertIn("| 買進迴圈 (buy_loop) | 待處理 (pending) |", content)
        self.assertIn("- 買進迴圈 (buy_loop)", content)
        self.assertIn("選股來源狀態", content)
        self.assertIn("same_day_a_preselect_missing_pass", content)
        self.assertIn("同日 A 預選缺失並直接 pass", content)
        self.assertIn("data/ab_llm_preselect/2026-04-20.json", content)
        self.assertIn("受保護下單任務證據", content)
        self.assertIn("can't open file", content)
        self.assertIn("受保護下單排程證據", content)
        self.assertIn("受保護下單後檢查", content)
        self.assertIn("下次受保護下單排程狀態", content)
        self.assertIn("live_guard_ready", content)
        self.assertIn("下次受保護下單排程狀態: `live_guard_ready (下次受保護下單排程已就緒)`", content)
        self.assertIn("price cap 2100", content)
        self.assertIn("2026-04-24T09:10:00+08:00", content)
        self.assertIn("賣出就緒狀態", content)
        self.assertIn("下一步說明", content)
        self.assertIn("保護條件問題已修好；交易視窗內應補跑送單，視窗關閉後才不補。", content)
        self.assertIn("受保護下單後設定時序狀態", content)
        self.assertIn("live_enabled_fixed_after_scheduled_run", content)
        self.assertIn(
            "受保護下單後設定時序狀態: `live_enabled_fixed_after_scheduled_run (live_enabled 參數在排程後才修正)`",
            content,
        )
        self.assertIn("受保護下單後設定檔更新時間", content)
        self.assertIn("2026-04-24T11:18:15+08:00", content)
        self.assertIn(
            "建議動作: `enable_live_in_config_before_next_scheduled_run (下次排程前先啟用 auto_trading.live_enabled)`",
            content,
        )
        self.assertIn(
            "受保護下單後建議動作: `enable_live_in_config_before_next_scheduled_run (下次排程前先啟用 auto_trading.live_enabled)`",
            content,
        )
        self.assertIn("阻塞原因: `no_strategy_positions (沒有策略部位)`", content)
        self.assertIn(
            "受保護下單後目前有效建議: `historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill (保護條件已修好；交易視窗內應補跑)`",
            content,
        )

    def test_workflow_markdown_lists_today_ordering_summary_when_present(self) -> None:
        content = _workflow_status_markdown(
            trade_date=date(2026, 4, 24),
            state_data={
                "status": "report_rendered",
                "provider_name": "ab_llm_preselect_json",
                "preselect_count": 0,
                "final_list_count": 0,
                "today_ordering_status": "guarded_time_passed_no_backfill+basket_a_same_day_json_missing_pass",
                "today_ordering_note": "2330 受保護下單路徑的保護條件問題雖已修好，但今天 09:10 排程時間已過，不會補單。 今天沒有找到同日 A 預選 JSON，所以整包 A 主線安全略過，不回退前一天。",
                "today_ordering_conflict_status": "same_day_a_source_arrived_after_basket_buy_window_closed",
                "today_ordering_conflict_note": "AB 每日預選屬於獨立專案輸出；非買進日的每日預選只作呈現與觀察。",
                "today_ordering_conflict_resolution_status": "strategy_scope_clarified",
                "today_ordering_conflict_resolution_action": "no_materialization_required_for_non_buy_day_daily_preselect",
                "today_ordering_conflict_resolution_note": "永豐自動交易只在每週買進日依已訂版整包建立部位。",
                "today_new_order_submission_open": False,
                "today_new_order_submission_status": "no_auto_new_buy_paths_remaining_today",
                "today_new_order_submission_note": "今天已沒有任何自動新買單路徑可送出；2330 受保護下單路徑與整包買入路徑都已關閉。",
                "weekly_settlement_open": False,
                "weekly_settlement_status": "weekly_settlement_current",
                "weekly_settlement_artifacts": "weekly.md, weekly.html, weekly.json",
                "weekly_settlement_note": "本週結算產物已齊備，涵蓋區間 2026-04-20 to 2026-04-24。",
                "weekly_settlement_next_action": "weekly_settlement_current_no_action_required",
                "weekly_settlement_next_action_note": "本週結算產物已是最新；除非你想重新產出週報，否則不需要額外的週結算動作。",
            },
            rows=[
                {"step": "prepare_week", "status": "done", "check": "ok", "path": "a.csv"},
            ],
        )

        self.assertIn("今日下單狀態", content)
        self.assertIn("guarded_time_passed_no_backfill+basket_a_same_day_json_missing_pass", content)
        self.assertIn("受保護下單補跑視窗已關閉 + 整包 A 同日 JSON 缺失並直接略過", content)
        self.assertIn("2330 受保護下單路徑的保護條件問題雖已修好", content)
        self.assertIn("策略範圍狀態", content)
        self.assertIn("same_day_a_source_arrived_after_basket_buy_window_closed", content)
        self.assertIn("非買進日每日預選已觀察", content)
        self.assertIn(
            "策略範圍處理動作: `no_materialization_required_for_non_buy_day_daily_preselect (非買進日每日預選不需展開成新買單)`",
            content,
        )
        self.assertIn("今日新單送出狀態", content)
        self.assertIn("no_auto_new_buy_paths_remaining_today", content)
        self.assertIn("今天已無自動新買單路徑", content)
        self.assertIn("週結算狀態", content)
        self.assertIn("weekly_settlement_current", content)
        self.assertIn("週結算已是最新", content)
        self.assertIn(
            "週結算下一步: `weekly_settlement_current_no_action_required (週結算已是最新，今天不需額外動作)`",
            content,
        )
        self.assertIn("本週結算產物已齊備", content)
        for token in self._BAD_MOJIBAKE_TOKENS:
            self.assertNotIn(token, content)

    def test_workflow_markdown_lists_closed_today_steps_separately(self) -> None:
        content = _workflow_status_markdown(
            trade_date=date(2026, 4, 24),
            state_data={
                "status": "workflow_status_rendered",
                "provider_name": "ab_llm_preselect_json",
                "today_new_order_submission_status": "no_auto_new_buy_paths_remaining_today",
                "workflow_status": {"completed_steps": 12, "pending_steps": 0, "closed_steps": 5},
            },
            rows=[
                {"step": "prepare_week", "status": "done", "check": "ok", "path": "a.csv"},
                {
                    "step": "track_until_final",
                    "status": "closed",
                    "check": "no automatic buy path or strategy position remains today, so quote tracking is no longer actionable",
                    "path": "quotes.csv",
                },
                {
                    "step": "buy_loop",
                    "status": "closed",
                    "check": "automatic new buy submission window already closed for today",
                    "path": "orders.csv",
                },
            ],
        )

        self.assertIn("## 待處理", content)
        self.assertIn("- 已完成步數: `12`", content)
        self.assertIn("- 待處理步數: `0`", content)
        self.assertIn("- 今日關閉步數: `5`", content)
        self.assertIn("- 無", content)
        self.assertIn("| 追蹤到收盤 (track_until_final) | 今日關閉 (closed) |", content)
        self.assertIn("| 買進迴圈 (buy_loop) | 今日關閉 (closed) |", content)
        self.assertIn("## 今日關閉", content)
        self.assertIn("- 追蹤到收盤 (track_until_final)", content)
        self.assertIn("- 買進迴圈 (buy_loop)", content)

    def test_selection_source_summary_prefers_actual_same_day_json_over_zero_counts(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"selection-source-{uuid.uuid4().hex}"
        source_dir = base / "data" / "ab_llm_preselect"
        source_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        (source_dir / "2026-04-24.json").write_text("{}", encoding="utf-8")

        settings = SimpleNamespace(
            project_root=base,
            providers=SimpleNamespace(options=lambda _name: {"preselect_dir": "data/ab_llm_preselect"}),
        )

        summary = _selection_source_summary(
            trade_date=date(2026, 4, 24),
            provider_name="ab_llm_preselect_json",
            preselect_count=0,
            final_list_count=0,
            settings=settings,
        )

        self.assertEqual(
            summary["selection_source_status"],
            "same_day_a_preselect_available_pending_materialization",
        )
        self.assertEqual(summary["selection_source_path"], "data/ab_llm_preselect/2026-04-24.json")
        self.assertTrue(summary["selection_source_last_modified"])
        self.assertIn("data/ab_llm_preselect/2026-04-24.json", summary["selection_source_note"])
        carry_forward = _selection_source_carry_forward_summary(
            selection_source_status=summary["selection_source_status"],
            trade_date=date(2026, 4, 24),
        )
        self.assertFalse(carry_forward["selection_source_carry_forward_open"])
        self.assertEqual(carry_forward["selection_source_carry_forward_next_trade_day"], "2026-04-27")
        with patch("sinopac_auto_trading.cli.input_dir_for", return_value=base / "inputs"), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            return_value=base / "run",
        ):
            materialization = _selection_materialization_summary(
                trade_date=date(2026, 4, 24),
                selection_source_status=summary["selection_source_status"],
                buy_cutoff_day=date(2026, 4, 23),
                last_trade_day=date(2026, 4, 24),
            )
        self.assertEqual(
            materialization["selection_materialization_status"],
            "daily_preselect_observed_no_auto_materialization_required",
        )
        self.assertEqual(
            materialization["selection_materialization_missing_artifacts"],
            "",
        )
        self.assertEqual(
            materialization["selection_materialization_next_action"],
            "no_materialization_required_for_non_buy_day_daily_preselect",
        )
        self.assertIn("下一個週一買進流程", materialization["selection_materialization_next_action_note"])

    def test_normalize_event_message_for_display_translates_historical_messages(self) -> None:
        self.assertEqual(
            _normalize_event_message_for_display(
                "prepare_week",
                "Loaded 10 preselect items from ab_llm_preselect_json",
            ),
            "已載入 10 筆預選名單，來源提供者=ab_llm_preselect_json。",
        )
        self.assertEqual(
            _normalize_event_message_for_display(
                "finalize",
                "Finalized 10 symbols.",
            ),
            "已完成 finalize，產出 10 檔訂版股票。",
        )
        self.assertEqual(
            _normalize_event_message_for_display(
                "workflow_status",
                "Rendered workflow status with 17 checklist rows.",
            ),
            "已輸出工作流狀態，包含 17 筆清單列。",
        )
        self.assertEqual(
            _normalize_event_message_for_display(
                "refresh_dashboard",
                "Dashboard refresh completed with 4 steps.",
            ),
            "已完成儀表板刷新，共 4 個步驟。",
        )
        self.assertEqual(
            _normalize_event_message_for_display(
                "allowed_live_order_task",
                "Skipped allowed live order task: Live submit is blocked because auto_trading.live_enabled is false in config.",
            ),
            "已略過受保護下單任務：因為設定中的 auto_trading.live_enabled=false，真實下單被擋下。",
        )
        self.assertEqual(
            _normalize_event_message_for_display(
                "post_guarded_order_check",
                "Checked guarded live order artifacts: after=skipped_config_live_disabled, current_step=historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill.",
            ),
            "已檢查受保護下單產物：after=skipped_config_live_disabled, current_step=historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill。",
        )
        self.assertEqual(
            _normalize_event_message_for_display(
                "post_guarded_order_check",
                "Checked guarded live order artifacts.",
            ),
            "已檢查受保護下單產物。",
        )
        self.assertEqual(
            _normalize_event_message_for_display(
                "sell_loop_readiness",
                "Checked sell-loop readiness: blocking=no_strategy_positions, next_action=historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill.",
            ),
            "已檢查賣出就緒狀態：blocking=no_strategy_positions, next_action=historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill。",
        )
        self.assertEqual(
            _normalize_event_message_for_display(
                "sell_loop_readiness",
                "Checked sell-loop readiness: no_strategy_positions.",
            ),
            "已檢查賣出就緒狀態：no_strategy_positions。",
        )
        self.assertEqual(
            _normalize_event_message_for_display(
                "sell_loop",
                "No strategy positions were available for sell-loop evaluation.",
            ),
            "目前沒有可用的策略部位可進行賣出迴圈評估。",
        )

    def test_normalize_guarded_schedule_description_translates_allowed_live_order_text(self) -> None:
        self.assertEqual(
            _normalize_guarded_schedule_description(
                "Run the only allowed SinoPac live order automation on 2026-04-24: 2330 Buy IntradayOdd 1 share at 09:10 with price cap 2100, with duplicate-order guard."
            ),
            "只允許的永豐真實下單自動化：2026-04-24，2330 買進盤中零股 1 股，09:10，價格上限 2100，含重複單保護。",
        )
        self.assertEqual(
            _normalize_guarded_schedule_description(
                "Run the only allowed SinoPac live order automation on 2026-04-24: 2330 Buy IntradayOdd 1 share from 09:10 to 13:20 with price cap 2100, retry every 5 minutes, with duplicate-order guard."
            ),
            "只允許的永豐真實下單自動化：2026-04-24，2330 買進盤中零股 1 股，09:10-13:20，價格上限 2100，每 5 分鐘重試，含重複單保護。",
        )

    def test_selection_source_summary_marks_stale_local_artifacts_as_pending_refresh(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"selection-source-stale-{uuid.uuid4().hex}"
        source_dir = base / "data" / "ab_llm_preselect"
        run_dir = base / "run"
        input_dir = base / "inputs"
        source_dir.mkdir(parents=True, exist_ok=True)
        run_dir.mkdir(parents=True, exist_ok=True)
        input_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        source_path = source_dir / "2026-04-24.json"
        source_path.write_text("{}", encoding="utf-8")

        for artifact in (
            run_dir / "preselect.csv",
            input_dir / "auto_trade_preselect.csv",
            input_dir / "auto_trade_final_list.csv",
            run_dir / "sizing.csv",
        ):
            artifact.write_text("stale", encoding="utf-8")
            stale_time = source_path.stat().st_mtime - 60
            os.utime(artifact, (stale_time, stale_time))

        settings = SimpleNamespace(
            project_root=base,
            providers=SimpleNamespace(active="ab_llm_preselect_json", options=lambda _name: {"preselect_dir": "data/ab_llm_preselect"}),
        )

        with patch("sinopac_auto_trading.cli.input_dir_for", return_value=input_dir), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            return_value=run_dir,
        ):
            details = _ab_same_day_source_refresh_details(
                trade_date=date(2026, 4, 24),
                settings=settings,
                run_dir=run_dir,
                input_dir=input_dir,
            )
            flags = _ab_same_day_source_refresh_flags(
                trade_date=date(2026, 4, 24),
                settings=settings,
                run_dir=run_dir,
                input_dir=input_dir,
            )
            summary = _selection_source_summary(
                trade_date=date(2026, 4, 24),
                provider_name="ab_llm_preselect_json",
                preselect_count=10,
                final_list_count=10,
                settings=settings,
            )

        self.assertTrue(flags["prepare_week"])
        self.assertTrue(flags["finalize"])
        self.assertEqual(
            details["trigger_status"],
            "same_day_source_newer_than_local_preselect_artifacts",
        )
        self.assertEqual(details["trigger_artifacts"], "preselect.csv, auto_trade_preselect.csv")
        self.assertIn("refresh_dashboard 需重跑 prepare_week", details["trigger_note"])
        self.assertEqual(
            summary["selection_source_status"],
            "same_day_a_preselect_available_pending_materialization",
        )
        self.assertIn("尚未同步到最新來源", summary["selection_source_note"])

    def test_selection_materialization_summary_reports_current_when_same_day_source_is_synced(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"selection-materialization-current-{uuid.uuid4().hex}"
        run_dir = base / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        (run_dir / "sizing.csv").write_text("current", encoding="utf-8")

        with patch("sinopac_auto_trading.cli.auto_trading_dir_for", return_value=run_dir):
            summary = _selection_materialization_summary(
                trade_date=date(2026, 4, 24),
                selection_source_status="same_day_a_preselect_loaded",
                buy_cutoff_day=date(2026, 4, 23),
                last_trade_day=date(2026, 4, 24),
            )

        self.assertEqual(summary["selection_materialization_status"], "local_materialization_current")
        self.assertEqual(summary["selection_materialization_next_action"], "materialization_current_no_action_required")
        self.assertIn("同日 A 預選來源已同步成目前本地整包產物", summary["selection_materialization_note"])

    def test_dashboard_refresh_summary_backfills_historical_reason_when_missing(self) -> None:
        summary = _dashboard_refresh_summary(
            {
                "dashboard_refresh": {
                    "steps_run": ["prepare_week", "finalize", "render_report", "workflow_status"],
                    "live": False,
                    "confirm_live": False,
                }
            }
        )

        self.assertEqual(summary["dashboard_refresh_status"], "materialized_without_buy_loop")
        self.assertEqual(
            summary["dashboard_refresh_trigger_status"],
            "historical_materialization_reason_not_recorded",
        )
        self.assertIn("沒有在當時被記錄下來", summary["dashboard_refresh_trigger_note"])

    def test_dashboard_last_materializing_summary_falls_back_to_current_materializing_refresh(self) -> None:
        summary = _dashboard_last_materializing_summary(
            {
                "dashboard_refresh": {
                    "steps_run": ["prepare_week", "finalize", "render_report", "workflow_status"],
                    "live": False,
                    "confirm_live": False,
                }
            }
        )

        self.assertEqual(summary["dashboard_last_materialization_status"], "materialized_without_buy_loop")
        self.assertEqual(
            summary["dashboard_last_materialization_trigger_status"],
            "historical_materialization_reason_not_recorded",
        )

    def test_dashboard_refresh_summary_reads_flattened_fields_when_nested_refresh_is_missing(self) -> None:
        summary = _dashboard_refresh_summary(
            {
                "dashboard_refresh_status": "materialized_without_buy_loop",
                "dashboard_refresh_steps": "prepare_week, finalize, render_report, workflow_status",
                "dashboard_refresh_note": "最近一次 refresh_dashboard 執行了 prepare_week, finalize, render_report, workflow_status；已重跑 prepare_week / finalize，但沒有進入 buy_loop。",
                "dashboard_refresh_trigger_status": "historical_materialization_reason_not_recorded",
                "dashboard_refresh_trigger_artifacts": "",
                "dashboard_refresh_trigger_note": "這次 materializing refresh 的 trigger reason 沒有在當時被記錄下來，因此只能從事件紀錄回推。",
            }
        )

        self.assertEqual(summary["dashboard_refresh_status"], "materialized_without_buy_loop")
        self.assertEqual(
            summary["dashboard_refresh_trigger_status"],
            "historical_materialization_reason_not_recorded",
        )
        self.assertIn("prepare_week, finalize, render_report, workflow_status", summary["dashboard_refresh_steps"])

    def test_command_workflow_status_refreshes_today_ordering_from_current_selection_source(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"workflow-status-current-ordering-{uuid.uuid4().hex}"
        run_dir = base / "run"
        input_dir = base / "inputs"
        source_dir = base / "data" / "ab_llm_preselect"
        note_path = base / "2026-04-24_workflow_status.md"
        run_dir.mkdir(parents=True, exist_ok=True)
        input_dir.mkdir(parents=True, exist_ok=True)
        source_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        (source_dir / "2026-04-24.json").write_text("{}", encoding="utf-8")
        (run_dir / "state.json").write_text(
            json.dumps(
                {
                    "trade_date": "2026-04-24",
                    "status": "report_rendered",
                    "provider_name": "ab_llm_preselect_json",
                    "dashboard_refresh": {
                        "steps_run": ["prepare_week", "finalize", "render_report", "workflow_status"],
                        "live": False,
                        "confirm_live": False,
                        "source_refresh_trigger_status": "same_day_source_newer_than_local_preselect_artifacts",
                        "source_refresh_trigger_artifacts": "preselect.csv, auto_trade_preselect.csv",
                        "source_refresh_trigger_note": "同日 A 預選來源比本地 preselect artifacts 更新，因此 refresh_dashboard 需要先重跑 prepare_week，再視情況重跑 finalize；涉及 preselect.csv, auto_trade_preselect.csv。",
                    },
                    "preselect_count": 0,
                    "final_list_count": 0,
                    "today_ordering_status": "stale_value",
                    "today_ordering_note": "stale note",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        settings = SimpleNamespace(
            providers=SimpleNamespace(
                active="ab_llm_preselect_json",
                options=lambda _name: {"preselect_dir": "data/ab_llm_preselect"},
            ),
            project_root=base,
        )

        with patch("sinopac_auto_trading.cli.Settings.load", return_value=settings), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            return_value=run_dir,
        ), patch(
            "sinopac_auto_trading.cli.input_dir_for",
            return_value=input_dir,
        ), patch(
            "sinopac_auto_trading.cli._post_guarded_order_check_report_summary",
            return_value={},
        ), patch(
            "sinopac_auto_trading.cli._sell_loop_readiness_report_summary",
            return_value={},
        ), patch(
            "sinopac_auto_trading.cli._workflow_status_note_path",
            return_value=note_path,
        ):
            exit_code = command_workflow_status(SimpleNamespace(trade_date="2026-04-24"))

        self.assertEqual(exit_code, 0)
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(
            state["selection_source_status"],
            "same_day_a_preselect_available_pending_materialization",
        )
        self.assertEqual(state["selection_source_path"], "data/ab_llm_preselect/2026-04-24.json")
        self.assertTrue(state["selection_source_last_modified"])
        self.assertEqual(
            state["today_ordering_status"],
            "basket_a_source_ready_pending_local_materialization+basket_buy_window_closed_last_trade_day",
        )
        self.assertIn("還沒補齊 preselect / finalize 產物", state["today_ordering_note"])
        self.assertIn("今天是本週最後交易日（2026-04-24）", state["today_ordering_note"])
        self.assertNotIn("today_ordering_conflict_status", state)
        self.assertNotIn("today_ordering_conflict_resolution_status", state)
        self.assertEqual(state["selection_source_carry_forward_next_trade_day"], "2026-04-27")
        self.assertIn("下一個週一買進流程", state["selection_source_carry_forward_note"])
        self.assertEqual(
            state["selection_materialization_status"],
            "daily_preselect_observed_no_auto_materialization_required",
        )
        self.assertEqual(
            state["selection_materialization_missing_artifacts"],
            "",
        )
        self.assertEqual(
            state["selection_materialization_next_action"],
            "no_materialization_required_for_non_buy_day_daily_preselect",
        )
        self.assertIn("下一個週一買進流程", state["selection_materialization_next_action_note"])
        state_text = (run_dir / "state.json").read_text(encoding="utf-8")
        assert_text_has_no_known_mojibake(self, state_text)
        assert_text_has_no_legacy_english_status_tokens(self, state_text)
        self.assertNotIn("today_new_order_submission_status", state)
        self.assertTrue(note_path.exists())
        note_text = note_path.read_text(encoding="utf-8")
        self.assertIn("儀表板刷新狀態", note_text)
        self.assertIn("materialized_without_buy_loop", note_text)
        self.assertIn(
            "載入預選 (prepare_week), 完成訂版 (finalize), 輸出日報 (render_report), 輸出工作流狀態 (workflow_status)",
            note_text,
        )
        self.assertIn("儀表板刷新觸發狀態", note_text)
        self.assertIn("same_day_source_newer_than_local_preselect_artifacts", note_text)
        for token in self._BAD_MOJIBAKE_TOKENS:
            self.assertNotIn(token, note_text)
        self.assertEqual(state["dashboard_refresh_status"], "materialized_without_buy_loop")
        self.assertEqual(
            state["dashboard_refresh_steps"],
            "prepare_week, finalize, render_report, workflow_status",
        )
        self.assertEqual(
            state["dashboard_refresh_trigger_status"],
            "same_day_source_newer_than_local_preselect_artifacts",
        )
        self.assertEqual(
            state["dashboard_last_materialization_status"],
            "materialized_without_buy_loop",
        )
        self.assertEqual(
            state["dashboard_last_materialization_trigger_status"],
            "same_day_source_newer_than_local_preselect_artifacts",
        )
        self.assertIsInstance(state["dashboard_refresh_last_materializing"], dict)

    def test_command_workflow_status_recovers_current_selection_state_from_local_artifacts(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"workflow-status-current-materialized-{uuid.uuid4().hex}"
        run_dir = base / "run"
        input_dir = base / "inputs"
        source_dir = base / "data" / "ab_llm_preselect"
        note_path = base / "2026-04-24_workflow_status.md"
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
                    "status": "report_rendered",
                    "provider_name": "ab_llm_preselect_json",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        source_stat = source_path.stat()
        os.utime(source_path, (source_stat.st_atime - 60, source_stat.st_mtime - 60))

        settings = SimpleNamespace(
            providers=SimpleNamespace(
                active="ab_llm_preselect_json",
                options=lambda _name: {"preselect_dir": "data/ab_llm_preselect"},
            ),
            project_root=base,
        )

        with patch("sinopac_auto_trading.cli.Settings.load", return_value=settings), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            return_value=run_dir,
        ), patch(
            "sinopac_auto_trading.cli.input_dir_for",
            return_value=input_dir,
        ), patch(
            "sinopac_auto_trading.cli._post_guarded_order_check_report_summary",
            return_value={},
        ), patch(
            "sinopac_auto_trading.cli._sell_loop_readiness_report_summary",
            return_value={},
        ), patch(
            "sinopac_auto_trading.cli._workflow_status_note_path",
            return_value=note_path,
        ):
            exit_code = command_workflow_status(SimpleNamespace(trade_date="2026-04-24"))

        self.assertEqual(exit_code, 0)
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["preselect_count"], 1)
        self.assertEqual(state["final_list_count"], 1)
        self.assertEqual(state["selection_source_status"], "same_day_a_preselect_loaded")
        self.assertEqual(state["selection_materialization_status"], "local_materialization_current")
        self.assertEqual(
            state["today_ordering_status"],
            "basket_a_loaded+basket_buy_window_closed_last_trade_day",
        )
        self.assertNotIn("today_new_order_submission_status", state)
        note_text = note_path.read_text(encoding="utf-8")
        self.assertIn("same_day_a_preselect_loaded", note_text)
        self.assertIn("local_materialization_current", note_text)

    def test_command_workflow_status_persists_provider_and_calendar_fields_when_missing_from_state(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"workflow-status-provider-fallback-{uuid.uuid4().hex}"
        run_dir = base / "run"
        input_dir = base / "inputs"
        source_dir = base / "data" / "ab_llm_preselect"
        note_path = base / "2026-04-24_workflow_status.md"
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
                    "status": "report_rendered",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        source_stat = source_path.stat()
        os.utime(source_path, (source_stat.st_atime - 60, source_stat.st_mtime - 60))

        settings = SimpleNamespace(
            providers=SimpleNamespace(
                active="ab_llm_preselect_json",
                options=lambda _name: {"preselect_dir": "data/ab_llm_preselect"},
            ),
            project_root=base,
        )

        with patch("sinopac_auto_trading.cli.Settings.load", return_value=settings), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            return_value=run_dir,
        ), patch(
            "sinopac_auto_trading.cli.input_dir_for",
            return_value=input_dir,
        ), patch(
            "sinopac_auto_trading.cli._post_guarded_order_check_report_summary",
            return_value={},
        ), patch(
            "sinopac_auto_trading.cli._sell_loop_readiness_report_summary",
            return_value={},
        ), patch(
            "sinopac_auto_trading.cli._workflow_status_note_path",
            return_value=note_path,
        ):
            exit_code = command_workflow_status(SimpleNamespace(trade_date="2026-04-24"))

        self.assertEqual(exit_code, 0)
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        plan = resolve_week_trade_plan(date(2026, 4, 24))
        self.assertEqual(state["provider_name"], "ab_llm_preselect_json")
        self.assertEqual(state["buy_cutoff_day"], plan.buy_cutoff_day.isoformat())
        self.assertEqual(state["last_trade_day"], plan.last_trade_day.isoformat())
        note_text = note_path.read_text(encoding="utf-8")
        self.assertIn("- 來源提供者: `ab_llm_preselect_json (同日 A 預選 JSON)`", note_text)
        self.assertIn(f"- 買進截止日: `{plan.buy_cutoff_day.isoformat()}`", note_text)
        self.assertIn(f"- 最後交易日: `{plan.last_trade_day.isoformat()}`", note_text)
        self.assertIn("| 準備 LLM 審核 (prepare_llm_selection) | 已完成 (done) |", note_text)
        self.assertIn("| LLM 決策檔 (llm_decisions) | 已完成 (done) |", note_text)
        self.assertIn("已記錄受保護下單後檢查", note_text)
        self.assertIn("已記錄賣出就緒狀態", note_text)
        self.assertIn("已產出每日 HTML 儀表板", note_text)
        self.assertIn("已產出工作流筆記", note_text)

    def test_command_workflow_status_recovers_dashboard_refresh_from_event_log_when_state_summary_is_missing(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"workflow-status-refresh-event-{uuid.uuid4().hex}"
        run_dir = base / "run"
        input_dir = base / "inputs"
        source_dir = base / "data" / "ab_llm_preselect"
        note_path = base / "2026-04-24_workflow_status.md"
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

        settings = SimpleNamespace(
            providers=SimpleNamespace(
                active="ab_llm_preselect_json",
                options=lambda _name: {"preselect_dir": "data/ab_llm_preselect"},
            ),
            project_root=base,
        )

        with patch("sinopac_auto_trading.cli.Settings.load", return_value=settings), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            return_value=run_dir,
        ), patch(
            "sinopac_auto_trading.cli.input_dir_for",
            return_value=input_dir,
        ), patch(
            "sinopac_auto_trading.cli._post_guarded_order_check_report_summary",
            return_value={},
        ), patch(
            "sinopac_auto_trading.cli._sell_loop_readiness_report_summary",
            return_value={},
        ), patch(
            "sinopac_auto_trading.cli._workflow_status_note_path",
            return_value=note_path,
        ):
            exit_code = command_workflow_status(SimpleNamespace(trade_date="2026-04-24"))

        self.assertEqual(exit_code, 0)
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["dashboard_refresh_status"], "materialized_without_buy_loop")
        self.assertEqual(
            state["dashboard_last_materialization_status"],
            "materialized_without_buy_loop",
        )
        self.assertEqual(
            state["dashboard_refresh_trigger_status"],
            "historical_materialization_reason_not_recorded",
        )
        self.assertIsInstance(state["dashboard_refresh_last_materializing"], dict)
        self.assertEqual(
            state["dashboard_refresh_last_materializing"]["steps_run"],
            ["prepare_week", "finalize", "render_report", "workflow_status"],
        )
        note_text = note_path.read_text(encoding="utf-8")
        self.assertIn("materialized_without_buy_loop", note_text)

    def test_resolve_last_materializing_refresh_payload_recovers_from_flat_summary(self) -> None:
        payload = _resolve_last_materializing_refresh_payload(
            date(2026, 4, 24),
            {
                "dashboard_refresh_status": "materialized_without_buy_loop",
                "dashboard_refresh_steps": "prepare_week, finalize, render_report, workflow_status",
                "dashboard_refresh_note": "latest refresh materialized selection artifacts",
                "dashboard_refresh_trigger_status": "historical_materialization_reason_not_recorded",
                "dashboard_refresh_trigger_artifacts": "",
                "dashboard_refresh_trigger_note": "legacy materializing refresh did not record a trigger reason",
            },
        )

        self.assertIsInstance(payload, dict)
        assert payload is not None
        self.assertEqual(
            payload["steps_run"],
            ["prepare_week", "finalize", "render_report", "workflow_status"],
        )
        self.assertEqual(
            payload["source_refresh_trigger_status"],
            "historical_materialization_reason_not_recorded",
        )

    def test_command_workflow_status_persists_guarded_flat_fields_for_report_mode(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"workflow-status-guarded-flat-{uuid.uuid4().hex}"
        run_dir = base / "run"
        input_dir = base / "inputs"
        source_dir = base / "data" / "ab_llm_preselect"
        note_path = base / "2026-04-24_workflow_status.md"
        run_dir.mkdir(parents=True, exist_ok=True)
        input_dir.mkdir(parents=True, exist_ok=True)
        source_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        (source_dir / "2026-04-24.json").write_text("{}", encoding="utf-8")
        (run_dir / "state.json").write_text(
            json.dumps(
                {
                    "trade_date": "2026-04-24",
                    "status": "report_rendered",
                    "provider_name": "ab_llm_preselect_json",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        settings = SimpleNamespace(
            providers=SimpleNamespace(
                active="ab_llm_preselect_json",
                options=lambda _name: {"preselect_dir": "data/ab_llm_preselect"},
            ),
            project_root=base,
        )

        post_guarded = {
            "after_status": "skipped_config_live_disabled",
            "recommendation": "enable_live_in_config_before_next_scheduled_run",
            "reconciled": False,
            "fills_count": 0,
            "positions_count": 0,
            "schedule_description": "Run the only allowed SinoPac live order automation on 2026-04-24: 2330 Buy IntradayOdd 1 share at 09:10 with price cap 2100, with duplicate-order guard.",
            "next_run_guard_status": "scheduled_task_time_passed",
            "next_run_guard_message": "Guard settings look fixed now, but the scheduled time already passed.",
            "config_timing_status": "live_enabled_fixed_after_scheduled_run",
            "config_timing_message": "設定檔在 2026-04-24T11:18:15+08:00 才改成 auto_trading.live_enabled=true，晚於本次排程時間 2026-04-24T09:10:00+08:00；所以今天這次 guarded 執行仍被略過。",
            "config_path": "config/auto_trading.yaml",
            "config_last_modified": "2026-04-24T11:18:15+08:00",
            "task_recorded_at": "2026-04-24T09:10:00+08:00",
        }
        sell_loop = {
            "blocking_reason": "no_strategy_positions",
            "next_action": "historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill",
            "positions_ready": False,
            "positions_count": 0,
            "positions_source_date": "",
            "post_guarded_effective_recommendation": "historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill",
            "post_guarded_next_run_guard_status": "scheduled_task_time_passed",
            "post_guarded_config_timing_status": "live_enabled_fixed_after_scheduled_run",
            "post_guarded_config_timing_message": "設定檔在 2026-04-24T11:18:15+08:00 才改成 auto_trading.live_enabled=true，晚於本次排程時間 2026-04-24T09:10:00+08:00；所以今天這次 guarded 執行仍被略過。",
            "post_guarded_config_path": "config/auto_trading.yaml",
            "post_guarded_config_last_modified": "2026-04-24T11:18:15+08:00",
            "post_guarded_task_recorded_at": "2026-04-24T09:10:00+08:00",
        }

        with patch("sinopac_auto_trading.cli.Settings.load", return_value=settings), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            return_value=run_dir,
        ), patch(
            "sinopac_auto_trading.cli.input_dir_for",
            return_value=input_dir,
        ), patch(
            "sinopac_auto_trading.cli._post_guarded_order_check_report_summary",
            return_value=post_guarded,
        ), patch(
            "sinopac_auto_trading.cli._sell_loop_readiness_report_summary",
            return_value=sell_loop,
        ), patch(
            "sinopac_auto_trading.cli._workflow_status_note_path",
            return_value=note_path,
        ):
            exit_code = command_workflow_status(SimpleNamespace(trade_date="2026-04-24"))

        self.assertEqual(exit_code, 0)
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["guarded_post_check_status"], "skipped_config_live_disabled")
        self.assertEqual(
            state["guarded_post_check_effective_recommendation"],
            "historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill",
        )
        self.assertEqual(
            state["post_guarded_order_check"]["schedule_description"],
            "只允許的永豐真實下單自動化：2026-04-24，2330 買進盤中零股 1 股，09:10，價格上限 2100，含重複單保護。",
        )
        self.assertEqual(state["sell_loop_readiness_blocking_reason"], "no_strategy_positions")
        self.assertEqual(
            state["sell_loop_readiness_post_guarded_config_timing_status"],
            "live_enabled_fixed_after_scheduled_run",
        )
        self.assertEqual(
            state["sell_loop_readiness_post_guarded_config_last_modified"],
            "2026-04-24T11:18:15+08:00",
        )
        self.assertEqual(
            state["sell_loop_readiness_post_guarded_task_recorded_at"],
            "2026-04-24T09:10:00+08:00",
        )

    def test_command_workflow_status_normalizes_guarded_schedule_description_in_note(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"workflow-status-guarded-schedule-desc-{uuid.uuid4().hex}"
        run_dir = base / "run"
        input_dir = base / "inputs"
        source_dir = base / "data" / "ab_llm_preselect"
        note_path = base / "2026-04-24_workflow_status.md"
        run_dir.mkdir(parents=True, exist_ok=True)
        input_dir.mkdir(parents=True, exist_ok=True)
        source_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        (source_dir / "2026-04-24.json").write_text("{}", encoding="utf-8")
        (run_dir / "state.json").write_text(
            json.dumps(
                {
                    "trade_date": "2026-04-24",
                    "status": "report_rendered",
                    "provider_name": "ab_llm_preselect_json",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        settings = SimpleNamespace(
            providers=SimpleNamespace(
                active="ab_llm_preselect_json",
                options=lambda _name: {"preselect_dir": "data/ab_llm_preselect"},
            ),
            project_root=base,
        )
        schedule_evidence = {
            "status": "ready",
            "task_name": "\\SinoPac2330BuyIntradayOdd0910",
            "state": "Ready",
            "next_run_time": "2026-04-24T09:10:00+08:00",
            "last_run_time": "2026-04-23T09:10:00+08:00",
            "last_task_result": "2",
            "description": "Run the only allowed SinoPac live order automation on 2026-04-24: 2330 Buy IntradayOdd 1 share at 09:10 with price cap 2100, with duplicate-order guard.",
            "message": "排程查詢成功。",
        }

        with patch("sinopac_auto_trading.cli.Settings.load", return_value=settings), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            return_value=run_dir,
        ), patch(
            "sinopac_auto_trading.cli.input_dir_for",
            return_value=input_dir,
        ), patch(
            "sinopac_auto_trading.cli._post_guarded_order_check_report_summary",
            return_value={},
        ), patch(
            "sinopac_auto_trading.cli._sell_loop_readiness_report_summary",
            return_value={},
        ), patch(
            "sinopac_auto_trading.cli._scheduled_task_evidence",
            return_value=schedule_evidence,
        ), patch(
            "sinopac_auto_trading.cli._workflow_status_note_path",
            return_value=note_path,
        ):
            exit_code = command_workflow_status(SimpleNamespace(trade_date="2026-04-24"))

        self.assertEqual(exit_code, 0)
        note_text = note_path.read_text(encoding="utf-8")
        self.assertIn("## 受保護下單排程證據", note_text)
        self.assertIn(
            "- 說明: 只允許的永豐真實下單自動化：2026-04-24，2330 買進盤中零股 1 股，09:10，價格上限 2100，含重複單保護。",
            note_text,
        )
        self.assertNotIn(
            "Run the only allowed SinoPac live order automation on 2026-04-24",
            note_text,
        )
        assert_text_has_no_known_mojibake(self, note_text)

    def test_allowed_live_task_log_evidence_reads_latest_failed_log(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"workflow-log-{uuid.uuid4().hex}"
        log_dir = base / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))

        (log_dir / "2026-04-23_091001.log").write_text(
            "\n".join(
                [
                    "[2026-04-23T09:10:01] starting task",
                    "python.exe: can't open file 'C:\\\\Users\\\\User\\\\Documents\\\\New'",
                    "[2026-04-23T09:10:02] exit_code=2",
                ]
            ),
            encoding="utf-8",
        )

        evidence = _allowed_live_task_log_evidence(date(2026, 4, 23), log_dir=log_dir)

        self.assertIsNotNone(evidence)
        assert evidence is not None
        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["exit_code"], "2")
        self.assertIn("can't open file", evidence["message"])

    def test_allowed_live_next_run_guard_summary_reports_ready(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"workflow-next-guard-{uuid.uuid4().hex}"
        scripts_dir = base / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        (scripts_dir / "run_allowed_2330_live_order_task.ps1").write_text(
            '$env:AUTO_TRADE_LIVE = "1"\npython run.py run_allowed_live_order\n',
            encoding="utf-8",
        )

        settings = SimpleNamespace(
            project_root=base,
            allow_live_submit=True,
            auto_trading=SimpleNamespace(live_enabled=True),
        )

        summary = _allowed_live_next_run_guard_summary(
            settings,
            scheduled_task_evidence={
                "status": "ready",
                "state": "Ready",
                "next_run_time": "2099-04-25T09:10:00+08:00",
                "message": "已讀到 Windows 工作排程狀態",
            },
        )

        self.assertEqual(summary["status"], "live_guard_ready")
        self.assertTrue(summary["runner_sets_auto_trade_live"])
        self.assertTrue(summary["allow_live_submit"])
        self.assertTrue(summary["live_enabled"])
        self.assertIn("2099-04-25T09:10:00+08:00", summary["message"])

    def test_allowed_live_next_run_guard_summary_requires_enabled_scheduled_task(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"workflow-next-guard-disabled-{uuid.uuid4().hex}"
        scripts_dir = base / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        (scripts_dir / "run_allowed_2330_live_order_task.ps1").write_text(
            '$env:AUTO_TRADE_LIVE = "1"\npython run.py run_allowed_live_order\n',
            encoding="utf-8",
        )

        settings = SimpleNamespace(
            project_root=base,
            allow_live_submit=True,
            auto_trading=SimpleNamespace(live_enabled=True),
        )

        summary = _allowed_live_next_run_guard_summary(
            settings,
            scheduled_task_evidence={
                "status": "disabled",
                "state": "Disabled",
                "next_run_time": "",
                "message": "已透過 task xml 讀到排程狀態",
            },
        )

        self.assertEqual(summary["status"], "scheduled_task_disabled")
        self.assertIn("Windows 工作排程被停用", summary["message"])

    def test_allowed_live_next_run_guard_summary_marks_past_schedule_as_no_backfill(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"workflow-next-guard-past-{uuid.uuid4().hex}"
        scripts_dir = base / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        (scripts_dir / "run_allowed_2330_live_order_task.ps1").write_text(
            '$env:AUTO_TRADE_LIVE = "1"\npython run.py run_allowed_live_order\n',
            encoding="utf-8",
        )

        settings = SimpleNamespace(
            project_root=base,
            allow_live_submit=True,
            auto_trading=SimpleNamespace(live_enabled=True),
        )

        summary = _allowed_live_next_run_guard_summary(
            settings,
            scheduled_task_evidence={
                "status": "ready",
                "state": "Ready",
                "next_run_time": "2000-01-01T09:10:00+08:00",
                "message": "已讀到 Windows 工作排程狀態",
            },
        )

        self.assertEqual(summary["status"], "scheduled_task_time_passed")
        self.assertIn("補跑視窗已關閉", summary["message"])

    def test_guarded_config_timing_summary_reports_fix_after_scheduled_run(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"workflow-guard-config-timing-{uuid.uuid4().hex}"
        config_dir = base / "config"
        run_dir = base / "2026-04-24"
        config_dir.mkdir(parents=True, exist_ok=True)
        run_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))

        config_path = config_dir / "auto_trading.yaml"
        task_path = run_dir / "allowed_live_order_2330_task.json"
        config_path.write_text("live_enabled: true\n", encoding="utf-8")
        task_path.write_text(
            json.dumps({"status": "skipped_config_live_disabled"}, ensure_ascii=False),
            encoding="utf-8",
        )
        task_recorded_at = datetime(2026, 4, 24, 9, 10, 0, tzinfo=TAIPEI).timestamp()
        config_modified_at = datetime(2026, 4, 24, 11, 18, 15, tzinfo=TAIPEI).timestamp()
        os.utime(task_path, (task_recorded_at, task_recorded_at))
        os.utime(config_path, (config_modified_at, config_modified_at))

        settings = SimpleNamespace(
            project_root=base,
            auto_trading=SimpleNamespace(live_enabled=True),
        )

        with patch("sinopac_auto_trading.cli.auto_trading_dir_for", return_value=run_dir):
            summary = _guarded_config_timing_summary(
                settings=settings,
                trade_date=date(2026, 4, 24),
                guarded_status="skipped_config_live_disabled",
                schedule_next_run_time="2026-04-24T09:10:00+08:00",
            )

        self.assertEqual(summary["status"], "live_enabled_fixed_after_scheduled_run")
        self.assertEqual(summary["config_last_modified"], "2026-04-24T11:18:15+08:00")
        self.assertEqual(summary["task_recorded_at"], "2026-04-24T09:10:00+08:00")
        self.assertTrue(summary["config_fixed_after_task_recorded"])
        self.assertTrue(summary["config_fixed_after_scheduled_run"])
        self.assertIn("晚於本次排程時間 2026-04-24T09:10:00+08:00", summary["message"])

    def test_post_guarded_order_check_report_summary_normalizes_historical_next_run_message(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"workflow-post-guarded-normalize-{uuid.uuid4().hex}"
        run_dir = base / "2026-04-24"
        run_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        (run_dir / "post_guarded_order_check.json").write_text(
            json.dumps(
                {
                    "trade_date": "2026-04-24",
                    "next_run_guard_status": "scheduled_task_time_passed",
                    "next_run_guard_message": (
                        "Guard settings look fixed now, but the Windows scheduled task time already passed at "
                        "2026-04-24T09:10:00+08:00; missed guarded orders do not backfill automatically."
                    ),
                }
            ),
            encoding="utf-8",
        )

        with patch("sinopac_auto_trading.cli.auto_trading_dir_for", return_value=run_dir):
            summary = _post_guarded_order_check_report_summary(date(2026, 4, 24))

        self.assertEqual(
            summary["next_run_guard_message"],
            "保護條件設定現在看起來已修好，但 Windows 排程時間 2026-04-24T09:10:00+08:00 已過，且補跑視窗已關閉；今天不再送這筆受保護下單。",
        )
        self.assertIn(
            "補跑視窗已關閉",
            (run_dir / "post_guarded_order_check.json").read_text(encoding="utf-8"),
        )

    def test_post_guarded_order_check_report_summary_normalizes_historical_messages(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"workflow-post-guarded-message-normalize-{uuid.uuid4().hex}"
        run_dir = base / "2026-04-24"
        run_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        (run_dir / "post_guarded_order_check.json").write_text(
            json.dumps(
                {
                    "trade_date": "2026-04-24",
                    "task_log_message": "task log found",
                    "schedule_message": "scheduled task query ok via task xml (powershell fallback: permission_denied; schtasks fallback failed: path_not_found)",
                }
            ),
            encoding="utf-8",
        )

        with patch("sinopac_auto_trading.cli.auto_trading_dir_for", return_value=run_dir):
            summary = _post_guarded_order_check_report_summary(date(2026, 4, 24))

        self.assertEqual(summary["task_log_message"], "已找到任務日誌")
        self.assertEqual(
            summary["schedule_message"],
            "已透過 task xml 讀到排程狀態 (powershell fallback: permission_denied; schtasks fallback failed: path_not_found)",
        )
        normalized_text = (run_dir / "post_guarded_order_check.json").read_text(encoding="utf-8")
        self.assertIn("已找到任務日誌", normalized_text)
        self.assertIn("已透過 task xml 讀到排程狀態", normalized_text)

    def test_post_guarded_order_check_report_summary_normalizes_historical_schedule_description(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"workflow-post-guarded-description-normalize-{uuid.uuid4().hex}"
        run_dir = base / "2026-04-24"
        run_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        (run_dir / "post_guarded_order_check.json").write_text(
            json.dumps(
                {
                    "trade_date": "2026-04-24",
                    "schedule_description": "Run the only allowed SinoPac live order automation on 2026-04-24: 2330 Buy IntradayOdd 1 share at 09:10 with price cap 2100, with duplicate-order guard.",
                }
            ),
            encoding="utf-8",
        )

        with patch("sinopac_auto_trading.cli.auto_trading_dir_for", return_value=run_dir):
            summary = _post_guarded_order_check_report_summary(date(2026, 4, 24))

        self.assertEqual(
            summary["schedule_description"],
            "只允許的永豐真實下單自動化：2026-04-24，2330 買進盤中零股 1 股，09:10，價格上限 2100，含重複單保護。",
        )
        self.assertIn(
            "只允許的永豐真實下單自動化",
            (run_dir / "post_guarded_order_check.json").read_text(encoding="utf-8"),
        )

    def test_sell_loop_readiness_report_summary_normalizes_historical_next_run_message(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"workflow-sell-readiness-normalize-{uuid.uuid4().hex}"
        run_dir = base / "2026-04-24"
        run_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        (run_dir / "sell_loop_readiness.json").write_text(
            json.dumps(
                {
                    "trade_date": "2026-04-24",
                    "post_guarded_next_run_guard_status": "live_guard_ready",
                    "post_guarded_next_run_guard_message": "Current config and scheduled runner look ready for the next guarded live run.",
                }
            ),
            encoding="utf-8",
        )

        with patch("sinopac_auto_trading.cli.auto_trading_dir_for", return_value=run_dir):
            summary = _sell_loop_readiness_report_summary(date(2026, 4, 24))

        self.assertEqual(
            summary["post_guarded_next_run_guard_message"],
            "目前設定、排程 runner 與 Windows 工作排程看起來都已準備好，可等待下一次受保護下單真實執行。",
        )
        self.assertIn(
            "可等待下一次受保護下單真實執行",
            (run_dir / "sell_loop_readiness.json").read_text(encoding="utf-8"),
        )

    def test_scheduled_task_evidence_falls_back_to_schtasks_when_powershell_query_fails(self) -> None:
        runner = Mock()
        runner.side_effect = [
            SimpleNamespace(returncode=1, stdout="", stderr="Get-ScheduledTask is unavailable"),
            SimpleNamespace(
                returncode=0,
                stdout="\n".join(
                    [
                        "TaskName: \\SinoPac2330BuyIntradayOdd0910",
                        "Next Run Time: 2026/4/24 銝? 09:10:00",
                        "Status: Ready",
                        "Last Run Time: 2026/4/23 銝? 09:10:00",
                        "Last Result: 2",
                        "Comment: Run the only allowed SinoPac live order automation on 2026-04-24: 2330 Buy IntradayOdd 1 share at 09:10 with price cap 2100, with duplicate-order guard.",
                    ]
                ),
                stderr="",
            ),
        ]

        evidence = _scheduled_task_evidence(task_name="SinoPac2330BuyIntradayOdd0910", runner=runner)

        self.assertEqual(evidence["status"], "ready")
        self.assertEqual(evidence["task_name"], "\\SinoPac2330BuyIntradayOdd0910")
        self.assertEqual(evidence["state"], "Ready")
        self.assertEqual(evidence["next_run_time"], "2026/4/24 銝? 09:10:00")
        self.assertEqual(evidence["last_run_time"], "2026/4/23 銝? 09:10:00")
        self.assertEqual(evidence["last_task_result"], "2")
        self.assertIn("price cap 2100", evidence["description"])
        self.assertIn("透過 schtasks", evidence["message"])

    def test_scheduled_task_evidence_falls_back_to_task_xml_when_queries_fail(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"workflow-task-xml-{uuid.uuid4().hex}"
        task_root = base / "Windows" / "System32" / "Tasks"
        task_root.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        (task_root / "SinoPac2330BuyIntradayOdd0910").write_text(
            """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Run the only allowed SinoPac live order automation on 2026-04-24: 2330 Buy IntradayOdd 1 share at 09:10 with price cap 2100.</Description>
    <URI>\\SinoPac2330BuyIntradayOdd0910</URI>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <StartBoundary>2026-04-24T01:10:00Z</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Settings>
    <Enabled>true</Enabled>
  </Settings>
</Task>
""",
            encoding="utf-16",
        )
        runner = Mock()
        runner.side_effect = [
            SimpleNamespace(returncode=1, stdout="", stderr="Get-ScheduledTask access denied"),
            SimpleNamespace(returncode=1, stdout="", stderr="ERROR: The system cannot find the path specified."),
        ]

        with patch.dict("sinopac_auto_trading.cli.os.environ", {"WINDIR": str(base / "Windows")}):
            evidence = _scheduled_task_evidence(task_name="SinoPac2330BuyIntradayOdd0910", runner=runner)

        self.assertEqual(evidence["status"], "ready")
        self.assertEqual(evidence["task_name"], "\\SinoPac2330BuyIntradayOdd0910")
        self.assertEqual(evidence["state"], "Ready")
        self.assertEqual(evidence["next_run_time"], "2026-04-24T09:10:00+08:00")
        self.assertEqual(evidence["last_run_time"], "")
        self.assertEqual(evidence["last_task_result"], "")
        self.assertIn("price cap 2100", evidence["description"])
        self.assertIn("透過 task xml", evidence["message"])
        self.assertIn("permission_denied", evidence["message"])
        self.assertIn("path_not_found", evidence["message"])

    def test_scheduled_task_xml_fallback_respects_repetition_window(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"workflow-task-xml-repeat-{uuid.uuid4().hex}"
        task_root = base / "Windows" / "System32" / "Tasks"
        task_root.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        (task_root / "SinoPac2330BuyIntradayOdd0910").write_text(
            """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Run the only allowed SinoPac live order automation on 2026-04-24: 2330 Buy IntradayOdd 1 share from 09:10 to 13:20 with price cap 2100, retry every 5 minutes, with duplicate-order guard.</Description>
    <URI>\\SinoPac2330BuyIntradayOdd0910</URI>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <StartBoundary>2026-04-24T09:10:00+08:00</StartBoundary>
      <Repetition>
        <Interval>PT5M</Interval>
        <Duration>PT4H10M</Duration>
      </Repetition>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Settings>
    <Enabled>true</Enabled>
  </Settings>
</Task>
""",
            encoding="utf-16",
        )
        runner = Mock()
        runner.side_effect = [
            SimpleNamespace(returncode=1, stdout="", stderr="Get-ScheduledTask access denied"),
            SimpleNamespace(returncode=1, stdout="", stderr="ERROR: The system cannot find the path specified."),
        ]
        expected_next = datetime(2026, 4, 24, 11, 5, tzinfo=TAIPEI)

        with patch.dict("sinopac_auto_trading.cli.os.environ", {"WINDIR": str(base / "Windows")}), patch(
            "sinopac_auto_trading.cli._next_repetition_run_at",
            return_value=expected_next,
        ) as next_repetition:
            evidence = _scheduled_task_evidence(task_name="SinoPac2330BuyIntradayOdd0910", runner=runner)

        self.assertEqual(evidence["next_run_time"], "2026-04-24T11:05:00+08:00")
        self.assertEqual(next_repetition.call_args.kwargs["interval"], timedelta(minutes=5))
        self.assertEqual(next_repetition.call_args.kwargs["duration"], timedelta(hours=4, minutes=10))

    def test_scheduled_task_evidence_parses_successful_query(self) -> None:
        def fake_runner(*args, **kwargs):
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    '{"task_name":"SinoPac2330BuyIntradayOdd0910","state":"Ready",'
                    '"next_run_time":"2026/4/24 銝? 09:10:00",'
                    '"last_run_time":"2026/4/23 銝? 09:10:00",'
                    '"last_task_result":"2","description":"price cap 2100"}'
                ),
                stderr="",
            )

        evidence = _scheduled_task_evidence(runner=fake_runner)

        self.assertEqual(evidence["status"], "ready")
        self.assertEqual(evidence["state"], "Ready")
        self.assertIn("09:10:00", evidence["next_run_time"])
        self.assertIn("price cap 2100", evidence["description"])

    def test_parse_scheduler_datetime_accepts_chinese_am_pm(self) -> None:
        morning = _parse_scheduler_datetime("2026/4/24 上午 9:10:00")
        afternoon = _parse_scheduler_datetime("2026/4/24 下午 1:10:00")

        self.assertEqual(morning.isoformat(), "2026-04-24T09:10:00+08:00")
        self.assertEqual(afternoon.isoformat(), "2026-04-24T13:10:00+08:00")

    def test_parse_scheduler_datetime_accepts_english_am_pm(self) -> None:
        morning = _parse_scheduler_datetime("2026/4/24 AM 9:10:00")
        afternoon = _parse_scheduler_datetime("2026/4/24 PM 1:10:00")

        self.assertEqual(morning.isoformat(), "2026-04-24T09:10:00+08:00")
        self.assertEqual(afternoon.isoformat(), "2026-04-24T13:10:00+08:00")

    def test_next_repetition_run_at_keeps_order_catch_up_window_open(self) -> None:
        start_at = datetime(2026, 4, 24, 9, 10, tzinfo=TAIPEI)
        now = datetime(2026, 4, 24, 11, 0, tzinfo=TAIPEI)

        next_at = _next_repetition_run_at(
            start_at=start_at,
            interval=_parse_windows_task_duration("PT5M"),
            duration=_parse_windows_task_duration("PT4H10M"),
            now=now,
        )

        self.assertEqual(next_at.isoformat(), "2026-04-24T11:05:00+08:00")

    def test_summarize_scheduler_query_error_uses_stable_english_signals(self) -> None:
        self.assertEqual(
            _summarize_scheduler_query_error("Access is denied"),
            "permission_denied",
        )
        self.assertEqual(
            _summarize_scheduler_query_error("HRESULT 0x80041003 while querying task"),
            "permission_denied",
        )
        self.assertEqual(
            _summarize_scheduler_query_error("ERROR: The system cannot find the path specified."),
            "path_not_found",
        )
        self.assertEqual(
            _summarize_scheduler_query_error("scheduled task query returned non-json output"),
            "non_json_output",
        )

    def test_clean_note_helpers_do_not_emit_known_mojibake_tokens(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"note-cleanliness-{uuid.uuid4().hex}"
        source_dir = base / "data" / "ab_llm_preselect"
        source_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        (source_dir / "2026-04-24.json").write_text("{}", encoding="utf-8")

        settings = SimpleNamespace(
            project_root=base,
            providers=SimpleNamespace(options=lambda _name: {"preselect_dir": "data/ab_llm_preselect"}),
        )
        summary_missing = _selection_source_summary(
            trade_date=date(2026, 4, 24),
            provider_name="ab_llm_preselect_json",
            preselect_count=0,
            final_list_count=0,
            settings=SimpleNamespace(
                project_root=Path(r"C:\Users\User\Documents\New project"),
                providers=SimpleNamespace(options=lambda _name: {"preselect_dir": "data/ab_llm_preselect"}),
            ),
        )
        summary_pending = _selection_source_summary(
            trade_date=date(2026, 4, 24),
            provider_name="ab_llm_preselect_json",
            preselect_count=0,
            final_list_count=0,
            settings=settings,
        )
        summary_loaded = _selection_source_summary(
            trade_date=date(2026, 4, 24),
            provider_name="ab_llm_preselect_json",
            preselect_count=10,
            final_list_count=10,
            settings=settings,
        )
        today_missing = _today_ordering_summary(
            guarded_effective_recommendation="historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill",
            selection_source_status="same_day_a_preselect_missing_pass",
            trade_date=date(2026, 4, 24),
            buy_cutoff_day=date(2026, 4, 23),
            last_trade_day=date(2026, 4, 24),
        )
        today_loaded = _today_ordering_summary(
            guarded_effective_recommendation="historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill",
            selection_source_status="same_day_a_preselect_loaded",
            trade_date=date(2026, 4, 24),
            buy_cutoff_day=date(2026, 4, 23),
            last_trade_day=date(2026, 4, 24),
        )
        conflict_resolution = _today_ordering_conflict_resolution_summary(
            today_ordering_conflict_status="same_day_a_source_arrived_after_basket_buy_window_closed",
            trade_date=date(2026, 4, 24),
            next_trade_day=date(2026, 4, 27),
        )
        samples = [
            _describe_workflow_action("wait_for_scheduled_run"),
            _describe_workflow_action("historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill"),
            _describe_workflow_action("align_a_source_timing_or_basket_buy_window_rule"),
            summary_missing["selection_source_note"],
            summary_pending["selection_source_note"],
            summary_loaded["selection_source_note"],
            today_missing["today_ordering_note"],
            today_loaded["today_ordering_note"],
            conflict_resolution["today_ordering_conflict_resolution_note"],
        ]

        for text in samples:
            for token in self._BAD_MOJIBAKE_TOKENS:
                self.assertNotIn(token, text)

    def test_scheduled_task_evidence_reports_query_failure_without_order_failure(self) -> None:
        def fake_runner(*args, **kwargs):
            return SimpleNamespace(returncode=1, stdout="", stderr="Access is denied")

        base = Path(__file__).resolve().parent / "_tmp" / f"workflow-task-query-failed-{uuid.uuid4().hex}"
        base.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))

        with patch.dict("sinopac_auto_trading.cli.os.environ", {"WINDIR": str(base)}):
            evidence = _scheduled_task_evidence(runner=fake_runner)

        self.assertEqual(evidence["status"], "query_failed")
        self.assertIn("permission_denied", evidence["message"])

    def test_guarded_live_order_status_summary_reports_scheduled_waiting(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"guarded-status-waiting-{uuid.uuid4().hex}"
        run_dir = base / "2026-04-24"
        run_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        next_run_time = (datetime.now(TAIPEI) + timedelta(minutes=10)).isoformat(timespec="seconds")

        with patch("sinopac_auto_trading.cli.auto_trading_dir_for", return_value=run_dir):
            summary = _guarded_live_order_status_summary(
                date(2026, 4, 24),
                live_task_evidence=None,
                scheduled_task_evidence={
                    "status": "ready",
                    "state": "Ready",
                    "next_run_time": next_run_time,
                    "last_run_time": "",
                    "last_task_result": "",
                    "message": "已讀到 Windows 工作排程狀態",
                },
            )

        self.assertEqual(summary["status"], "scheduled_waiting")
        self.assertEqual(summary["recommendation"], "wait_for_scheduled_run")

    def test_guarded_live_order_status_summary_reports_passed_schedule_without_artifacts(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"guarded-status-passed-{uuid.uuid4().hex}"
        run_dir = base / "2026-04-24"
        run_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        next_run_time = (datetime.now(TAIPEI) - timedelta(minutes=10)).isoformat(timespec="seconds")

        with patch("sinopac_auto_trading.cli.auto_trading_dir_for", return_value=run_dir):
            summary = _guarded_live_order_status_summary(
                date(2026, 4, 24),
                live_task_evidence=None,
                scheduled_task_evidence={
                    "status": "ready",
                    "state": "Ready",
                    "next_run_time": next_run_time,
                    "last_run_time": "",
                    "last_task_result": "",
                    "message": "已透過 task xml 讀到排程狀態",
                },
            )

        self.assertEqual(summary["status"], "scheduled_time_passed_without_artifacts")
        self.assertEqual(summary["recommendation"], "inspect_task_log_and_run_read_only_reconcile")
        self.assertEqual(summary["fills_count"], 0)

    def test_guarded_live_order_status_summary_reports_reconciled_fills(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"guarded-status-filled-{uuid.uuid4().hex}"
        run_dir = base / "2026-04-24"
        run_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        (run_dir / "fills.csv").write_text("stock_id,fill_qty,fill_price\n2330,1,2090\n", encoding="utf-8")
        (run_dir / "positions.csv").write_text("stock_id,holding_qty,buy_avg_price\n2330,1,2090\n", encoding="utf-8")

        with patch("sinopac_auto_trading.cli.auto_trading_dir_for", return_value=run_dir):
            summary = _guarded_live_order_status_summary(
                date(2026, 4, 24),
                live_task_evidence={"status": "success", "exit_code": "0", "message": "ok", "path": "task.log"},
                scheduled_task_evidence={"status": "ready", "message": "已讀到 Windows 工作排程狀態"},
            )

        self.assertEqual(summary["status"], "reconciled_with_fills")
        self.assertEqual(summary["fills_count"], 1)
        self.assertEqual(summary["positions_count"], 1)

    def test_guarded_live_order_status_summary_reports_skipped_live_guard(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"guarded-status-skipped-{uuid.uuid4().hex}"
        run_dir = base / "2026-04-24"
        run_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        (run_dir / "allowed_live_order_2330_task.json").write_text(
            json.dumps(
                {
                    "status": "skipped_config_live_disabled",
                    "message": "Skipped allowed live order task: Live submit is blocked because auto_trading.live_enabled is false in config.",
                }
            ),
            encoding="utf-8",
        )

        with patch("sinopac_auto_trading.cli.auto_trading_dir_for", return_value=run_dir):
            summary = _guarded_live_order_status_summary(
                date(2026, 4, 24),
                live_task_evidence={"status": "success", "exit_code": "0", "message": "已找到 task log", "path": "task.log"},
                scheduled_task_evidence={"status": "ready", "state": "Ready", "next_run_time": "", "message": "已讀到 Windows 工作排程狀態"},
            )

        self.assertEqual(summary["status"], "skipped_config_live_disabled")
        self.assertEqual(summary["recommendation"], "enable_live_in_config_before_next_scheduled_run")

    def test_sell_loop_readiness_treats_historical_skip_as_fixed_when_next_run_is_ready(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"sell-readiness-next-guard-{uuid.uuid4().hex}"
        run_dir = base / "2026-04-24"
        run_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))

        with patch("sinopac_auto_trading.cli.auto_trading_dir_for", return_value=run_dir), patch(
            "sinopac_auto_trading.cli._load_strategy_positions_for_sell_loop",
            return_value=([], None, []),
        ), patch(
            "sinopac_auto_trading.cli._post_guarded_order_check_report_summary",
            return_value={
                "after_status": "skipped_config_live_disabled",
                "recommendation": "enable_live_in_config_before_next_scheduled_run",
                "next_run_guard_status": "live_guard_ready",
                "next_run_guard_message": "目前設定、排程 runner 與 Windows 工作排程看起來都已準備好，可等待下一次 guarded 真實執行。",
                "config_timing_status": "live_enabled_fixed_after_scheduled_run",
                "config_timing_message": "設定檔在 2026-04-24T11:18:15+08:00 才改成 auto_trading.live_enabled=true，晚於本次排程時間 2026-04-24T09:10:00+08:00；所以今天這次 guarded 執行仍被略過。",
                "config_path": "config/auto_trading.yaml",
                "config_last_modified": "2026-04-24T11:18:15+08:00",
                "task_recorded_at": "2026-04-24T09:10:00+08:00",
            },
        ):
            result = _sell_loop_readiness_summary(date(2026, 4, 24))

        self.assertEqual(result.blocking_reason, "no_strategy_positions")
        self.assertEqual(
            result.next_action,
            "today_guarded_run_missed_wait_for_next_guarded_schedule_no_backfill",
        )
        self.assertEqual(
            result.post_guarded_effective_recommendation,
            "historical_guard_issue_already_fixed_wait_for_next_schedule",
        )
        self.assertEqual(
            result.post_guarded_recommendation_note,
            "下次排程前，先在設定中啟用 auto_trading.live_enabled。",
        )
        self.assertEqual(
            result.post_guarded_effective_recommendation_note,
            "歷史保護條件問題已修好，等待下一次排程；今天不補單。",
        )
        self.assertEqual(
            result.next_action_note,
            "今天的受保護下單排程已錯過；若仍在 09:10-13:20 內應補跑，視窗關閉後才等下一次排程。",
        )
        self.assertEqual(result.post_guarded_next_run_guard_status, "live_guard_ready")
        self.assertIn("可等待下一次 guarded 真實執行", result.post_guarded_next_run_guard_message)
        self.assertEqual(result.post_guarded_config_timing_status, "live_enabled_fixed_after_scheduled_run")
        self.assertEqual(result.post_guarded_config_last_modified, "2026-04-24T11:18:15+08:00")

    def test_sell_loop_readiness_uses_current_guard_fix_when_next_run_is_not_ready(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"sell-readiness-next-guard-disabled-{uuid.uuid4().hex}"
        run_dir = base / "2026-04-24"
        run_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))

        with patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            return_value=run_dir,
        ), patch(
            "sinopac_auto_trading.cli._load_strategy_positions_for_sell_loop",
            return_value=([], None, []),
        ), patch(
            "sinopac_auto_trading.cli._post_guarded_order_check_report_summary",
            return_value={
                "after_status": "skipped_config_live_disabled",
                "recommendation": "enable_live_in_config_before_next_scheduled_run",
                "next_run_guard_status": "scheduled_task_disabled",
                "next_run_guard_message": "下一次 guarded 真實執行仍被擋住，因為 Windows 工作排程被停用。",
                "config_timing_status": "live_enabled_fixed_after_scheduled_run",
                "config_timing_message": "設定檔在 2026-04-24T11:18:15+08:00 才改成 auto_trading.live_enabled=true，晚於本次排程時間 2026-04-24T09:10:00+08:00；所以今天這次 guarded 執行仍被略過。",
                "config_path": "config/auto_trading.yaml",
                "config_last_modified": "2026-04-24T11:18:15+08:00",
                "task_recorded_at": "2026-04-24T09:10:00+08:00",
            },
        ):
            result = _sell_loop_readiness_summary(date(2026, 4, 24))

        self.assertEqual(result.blocking_reason, "no_strategy_positions")
        self.assertEqual(
            result.next_action,
            "enable_windows_scheduled_task_before_next_scheduled_run",
        )
        self.assertEqual(
            result.post_guarded_effective_recommendation,
            "enable_windows_scheduled_task_before_next_scheduled_run",
        )
        self.assertEqual(
            result.post_guarded_recommendation_note,
            "下次排程前，先在設定中啟用 auto_trading.live_enabled。",
        )
        self.assertEqual(
            result.post_guarded_effective_recommendation_note,
            "下次排程前，先重新啟用 Windows 工作排程。",
        )
        self.assertEqual(result.next_action_note, "下次排程前，先重新啟用 Windows 工作排程。")
        self.assertEqual(result.post_guarded_next_run_guard_status, "scheduled_task_disabled")
        self.assertEqual(result.post_guarded_config_timing_status, "live_enabled_fixed_after_scheduled_run")

    def test_sell_loop_readiness_marks_missed_window_as_no_backfill(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"sell-readiness-next-guard-past-{uuid.uuid4().hex}"
        run_dir = base / "2026-04-24"
        run_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))

        with patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            return_value=run_dir,
        ), patch(
            "sinopac_auto_trading.cli._load_strategy_positions_for_sell_loop",
            return_value=([], None, []),
        ), patch(
            "sinopac_auto_trading.cli._post_guarded_order_check_report_summary",
            return_value={
                "after_status": "skipped_config_live_disabled",
                "recommendation": "enable_live_in_config_before_next_scheduled_run",
                "next_run_guard_status": "scheduled_task_time_passed",
                "next_run_guard_message": "guard 設定現在看起來已修好，但 Windows 排程時間 2026-04-24T09:10:00+08:00 已過；錯過的 guarded 單不會自動補單。",
                "config_timing_status": "live_enabled_fixed_after_scheduled_run",
                "config_timing_message": "設定檔在 2026-04-24T11:18:15+08:00 才改成 auto_trading.live_enabled=true，晚於本次排程時間 2026-04-24T09:10:00+08:00；所以今天這次 guarded 執行仍被略過。",
                "config_path": "config/auto_trading.yaml",
                "config_last_modified": "2026-04-24T11:18:15+08:00",
                "task_recorded_at": "2026-04-24T09:10:00+08:00",
            },
        ):
            result = _sell_loop_readiness_summary(date(2026, 4, 24))

        self.assertEqual(result.blocking_reason, "no_strategy_positions")
        self.assertEqual(
            result.next_action,
            "historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill",
        )
        self.assertEqual(
            result.post_guarded_effective_recommendation,
            "historical_guard_issue_fixed_but_scheduled_time_passed_no_backfill",
        )
        self.assertEqual(
            result.post_guarded_recommendation_note,
            "下次排程前，先在設定中啟用 auto_trading.live_enabled。",
        )
        self.assertEqual(
            result.post_guarded_effective_recommendation_note,
            "保護條件問題已修好；交易視窗內應補跑送單，視窗關閉後才不補。",
        )
        self.assertEqual(
            result.next_action_note,
            "保護條件問題已修好；交易視窗內應補跑送單，視窗關閉後才不補。",
        )
        self.assertEqual(result.post_guarded_next_run_guard_status, "scheduled_task_time_passed")
        self.assertEqual(result.post_guarded_config_timing_status, "live_enabled_fixed_after_scheduled_run")
        self.assertEqual(result.post_guarded_task_recorded_at, "2026-04-24T09:10:00+08:00")

    def test_guarded_live_task_warning_formats_failed_evidence(self) -> None:
        warning = _guarded_live_task_warning(
            date(2026, 4, 23),
            {
                "status": "failed",
                "exit_code": "2",
                "message": "can't open file",
                "path": "task.log",
            },
        )

        self.assertIn("Guarded live task failed", warning)
        self.assertIn("exit_code=2", warning)
        self.assertIn("can't open file", warning)

    def test_guarded_live_task_warning_ignores_success(self) -> None:
        warning = _guarded_live_task_warning(
            date(2026, 4, 23),
            {
                "status": "success",
                "exit_code": "0",
                "message": "ok",
                "path": "task.log",
            },
        )

        self.assertEqual(warning, "")

    def test_allowed_live_task_log_evidence_reads_powershell_utf16_log(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"workflow-utf16-log-{uuid.uuid4().hex}"
        log_dir = base / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))

        (log_dir / "2026-04-23_091001.log").write_text(
            "\n".join(
                [
                    "[2026-04-23T09:10:01] starting task",
                    "python.exe: can't open file 'C:\\\\Users\\\\User\\\\Documents\\\\New'",
                    "[2026-04-23T09:10:02] exit_code=2",
                ]
            ),
            encoding="utf-16",
        )

        evidence = _allowed_live_task_log_evidence(date(2026, 4, 23), log_dir=log_dir)

        self.assertIsNotNone(evidence)
        assert evidence is not None
        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["exit_code"], "2")
        self.assertIn("can't open file", evidence["message"])

    def test_post_guarded_order_check_status_only_records_state(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"post-guard-status-{uuid.uuid4().hex}"
        run_dir = base / "2026-04-24"
        run_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))

        summaries = [
            {
                "status": "scheduled_waiting",
                "fills_count": 0,
                "positions_count": 0,
                "recommendation": "wait_for_scheduled_run",
                "task_log_status": "",
                "task_log_exit_code": "",
                "task_log_message": "",
                "task_log_path": "",
                "schedule_task_name": "\\SinoPac2330BuyIntradayOdd0910",
                "schedule_status": "ready",
                "schedule_state": "Ready",
                "schedule_next_run_time": "2026-04-24T09:10:00+08:00",
                "schedule_last_run_time": "",
                "schedule_last_task_result": "",
                "schedule_description": "price cap 2100",
                "schedule_message": "scheduled task query ok",
            },
            {
                "status": "scheduled_waiting",
                "fills_count": 0,
                "positions_count": 0,
                "recommendation": "wait_for_scheduled_run",
                "task_log_status": "",
                "task_log_exit_code": "",
                "task_log_message": "",
                "task_log_path": "",
                "schedule_task_name": "\\SinoPac2330BuyIntradayOdd0910",
                "schedule_status": "ready",
                "schedule_state": "Ready",
                "schedule_next_run_time": "2026-04-24T09:10:00+08:00",
                "schedule_last_run_time": "",
                "schedule_last_task_result": "",
                "schedule_description": "price cap 2100",
                "schedule_message": "scheduled task query ok",
            },
        ]

        with patch("sinopac_auto_trading.cli.auto_trading_dir_for", return_value=run_dir), patch(
            "sinopac_auto_trading.cli._guarded_live_order_status_summary",
            side_effect=summaries,
        ), patch(
            "sinopac_auto_trading.cli._allowed_live_next_run_guard_summary",
            return_value={
                "status": "live_guard_ready",
                "message": "目前設定、排程 runner 與 Windows 工作排程看起來都已準備好，可等待下一次 guarded 真實執行。",
                "runner_script_path": "scripts/run_allowed_2330_live_order_task.ps1",
                "runner_sets_auto_trade_live": True,
                "allow_live_submit": True,
                "live_enabled": True,
            },
        ), patch(
            "sinopac_auto_trading.cli._guarded_config_timing_summary",
            return_value={
                "status": "live_enabled_fixed_after_scheduled_run",
                "message": (
                    "設定檔在 2026-04-24T11:18:15+08:00 才改成 auto_trading.live_enabled=true，"
                    "晚於本次排程時間 2026-04-24T09:10:00+08:00；所以今天這次 guarded 執行仍被略過。"
                ),
                "config_path": "config/auto_trading.yaml",
                "config_last_modified": "2026-04-24T11:18:15+08:00",
                "task_recorded_at": "2026-04-24T09:10:00+08:00",
                "config_fixed_after_task_recorded": True,
                "config_fixed_after_scheduled_run": True,
            },
        ):
            result = _post_guarded_order_check(
                settings=SimpleNamespace(),
                trade_date=date(2026, 4, 24),
            )

        self.assertEqual(result.before_status, "scheduled_waiting")
        self.assertEqual(result.after_status, "scheduled_waiting")
        self.assertFalse(result.reconciled)
        self.assertFalse(result.sell_loop_readiness_recorded)

        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "post_guarded_order_checked")
        self.assertEqual(state["post_guarded_order_check"]["recommendation"], "wait_for_scheduled_run")
        self.assertEqual(
            state["post_guarded_order_check"]["recommendation_note"],
            "等待 Windows 排程到預定時間自動執行。",
        )
        self.assertEqual(state["post_guarded_order_check"]["effective_recommendation"], "wait_for_scheduled_run")
        self.assertEqual(
            state["post_guarded_order_check"]["effective_recommendation_note"],
            "等待 Windows 排程到預定時間自動執行。",
        )
        self.assertEqual(
            state["post_guarded_order_check"]["config_timing_status"],
            "live_enabled_fixed_after_scheduled_run",
        )
        self.assertEqual(
            state["post_guarded_order_check"]["config_last_modified"],
            "2026-04-24T11:18:15+08:00",
        )

        artifact = json.loads((run_dir / "post_guarded_order_check.json").read_text(encoding="utf-8"))
        self.assertEqual(artifact["after_status"], "scheduled_waiting")
        self.assertEqual(artifact["recommendation_note"], "等待 Windows 排程到預定時間自動執行。")
        self.assertEqual(artifact["effective_recommendation"], "wait_for_scheduled_run")
        self.assertEqual(artifact["effective_recommendation_note"], "等待 Windows 排程到預定時間自動執行。")
        self.assertEqual(artifact["schedule_task_name"], "\\SinoPac2330BuyIntradayOdd0910")
        self.assertEqual(artifact["schedule_next_run_time"], "2026-04-24T09:10:00+08:00")
        self.assertEqual(artifact["schedule_description"], "price cap 2100")
        self.assertEqual(artifact["next_run_guard_status"], "live_guard_ready")
        self.assertTrue(artifact["next_run_guard_runner_sets_auto_trade_live"])
        self.assertEqual(artifact["config_timing_status"], "live_enabled_fixed_after_scheduled_run")
        self.assertEqual(artifact["config_path"], "config/auto_trading.yaml")
        self.assertEqual(artifact["config_last_modified"], "2026-04-24T11:18:15+08:00")
        self.assertEqual(artifact["task_recorded_at"], "2026-04-24T09:10:00+08:00")
        self.assertTrue(artifact["config_fixed_after_scheduled_run"])
        event_lines = (run_dir / "event_log.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertTrue(event_lines)
        last_event = json.loads(event_lines[-1])
        self.assertEqual(last_event["event_type"], "post_guarded_order_check")
        self.assertIn("after=scheduled_waiting", last_event["message"])
        self.assertIn("current_step=wait_for_scheduled_run", last_event["message"])
        rows = _workflow_status_rows(
            trade_date=date(2026, 4, 24),
            run_dir=run_dir,
            input_dir=base / "inputs",
            plan=resolve_week_trade_plan(date(2026, 4, 24)),
        )
        statuses = {row["step"]: row["status"] for row in rows}
        self.assertEqual(statuses["post_guarded_order_check"], "done")

    def test_post_guarded_order_check_reconcile_requires_live(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "--live is required"):
            _post_guarded_order_check(
                settings=SimpleNamespace(),
                trade_date=date(2026, 4, 24),
                reconcile=True,
                live=False,
            )

    def test_post_guarded_order_check_writes_artifacts_before_report_and_workflow_status(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"post-guard-workflow-{uuid.uuid4().hex}"
        run_dir = base / "2026-04-24"
        run_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))

        summaries = [
            {
                "status": "scheduled_waiting",
                "fills_count": 0,
                "positions_count": 0,
                "recommendation": "wait_for_scheduled_run",
                "schedule_task_name": "\\SinoPac2330BuyIntradayOdd0910",
                "schedule_next_run_time": "2026-04-24T09:10:00+08:00",
                "schedule_description": "price cap 2100",
            },
            {
                "status": "scheduled_waiting",
                "fills_count": 0,
                "positions_count": 0,
                "recommendation": "wait_for_scheduled_run",
                "schedule_task_name": "\\SinoPac2330BuyIntradayOdd0910",
                "schedule_next_run_time": "2026-04-24T09:10:00+08:00",
                "schedule_description": "price cap 2100",
            },
        ]

        def assert_handoff_artifacts_exist() -> dict[str, object]:
            post_artifact = run_dir / "post_guarded_order_check.json"
            readiness_artifact = run_dir / "sell_loop_readiness.json"
            self.assertTrue(post_artifact.exists())
            self.assertTrue(readiness_artifact.exists())
            payload = json.loads(post_artifact.read_text(encoding="utf-8"))
            self.assertEqual(payload["after_status"], "scheduled_waiting")
            self.assertEqual(payload["schedule_task_name"], "\\SinoPac2330BuyIntradayOdd0910")
            readiness = json.loads(readiness_artifact.read_text(encoding="utf-8"))
            self.assertEqual(readiness["blocking_reason"], "no_strategy_positions")
            self.assertEqual(readiness["post_guarded_recommendation_note"], "等待 Windows 排程到預定時間自動執行。")
            self.assertEqual(readiness["next_action_note"], "等成交存在後，做只讀 broker reconcile。")
            rows = _workflow_status_rows(
                trade_date=date(2026, 4, 24),
                run_dir=run_dir,
                input_dir=base / "inputs",
                plan=resolve_week_trade_plan(date(2026, 4, 24)),
            )
            statuses = {row["step"]: row["status"] for row in rows}
            self.assertEqual(statuses["post_guarded_order_check"], "done")
            self.assertEqual(statuses["sell_loop_readiness"], "done")
            return payload

        def assert_artifact_before_report(args):
            assert_handoff_artifacts_exist()
            return 0

        def assert_artifact_before_workflow(args):
            assert_handoff_artifacts_exist()
            return 0

        with patch("sinopac_auto_trading.cli.auto_trading_dir_for", return_value=run_dir), patch(
            "sinopac_auto_trading.cli._guarded_live_order_status_summary",
            side_effect=summaries,
        ), patch(
            "sinopac_auto_trading.cli._allowed_live_next_run_guard_summary",
            return_value={
                "status": "live_guard_ready",
                "message": "目前設定、排程 runner 與 Windows 工作排程看起來都已準備好，可等待下一次 guarded 真實執行。",
                "runner_script_path": "scripts/run_allowed_2330_live_order_task.ps1",
                "runner_sets_auto_trade_live": True,
                "allow_live_submit": True,
                "live_enabled": True,
            },
        ), patch(
            "sinopac_auto_trading.cli.command_render_report",
            side_effect=assert_artifact_before_report,
        ), patch(
            "sinopac_auto_trading.cli.command_workflow_status",
            side_effect=assert_artifact_before_workflow,
        ) as workflow, patch(
            "sinopac_auto_trading.cli._best_effort_obsidian_sync",
        ) as obsidian_sync:
            result = _post_guarded_order_check(
                settings=SimpleNamespace(),
                trade_date=date(2026, 4, 24),
                sell_loop_readiness=True,
                render_report=True,
                workflow_status=True,
            )

        self.assertTrue(result.sell_loop_readiness_recorded)
        self.assertTrue(result.reports_rendered)
        self.assertTrue(result.workflow_status_rendered)
        workflow.assert_called_once()
        self.assertFalse(obsidian_sync.call_args.kwargs["include_live_status"])
        event_lines = (run_dir / "event_log.jsonl").read_text(encoding="utf-8").splitlines()
        readiness_events = [json.loads(line) for line in event_lines if json.loads(line)["event_type"] == "sell_loop_readiness"]
        self.assertTrue(readiness_events)
        self.assertIn("blocking=no_strategy_positions", readiness_events[-1]["message"])
        self.assertIn("next_action=", readiness_events[-1]["message"])

    def test_post_guarded_order_check_reconcile_uses_guarded_target(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"post-guard-reconcile-{uuid.uuid4().hex}"
        run_dir = base / "2026-04-24"
        run_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))

        class _Broker:
            def get_account_summary(self):
                return SimpleNamespace(signed=True)

        summaries = [
            {
                "status": "submitted_no_fills_yet",
                "fills_count": 0,
                "positions_count": 0,
                "recommendation": "run_reconcile_broker_state_after_market_updates",
            },
            {
                "status": "reconciled_with_fills",
                "fills_count": 1,
                "positions_count": 1,
                "recommendation": "fills_found_review_positions_and_sell_loop",
            },
        ]

        with patch("sinopac_auto_trading.cli.auto_trading_dir_for", return_value=run_dir), patch(
            "sinopac_auto_trading.cli._guarded_live_order_status_summary",
            side_effect=summaries,
        ), patch(
            "sinopac_auto_trading.cli._reconcile_target_stock_ids",
            return_value={"2330"},
        ) as target_ids, patch(
            "sinopac_auto_trading.cli._reconcile_broker_state",
            return_value=SimpleNamespace(fills_count=1, positions_count=1),
        ) as reconcile, patch(
            "sinopac_auto_trading.cli._best_effort_obsidian_sync",
        ):
            result = _post_guarded_order_check(
                settings=SimpleNamespace(),
                trade_date=date(2026, 4, 24),
                live=True,
                reconcile=True,
                broker=_Broker(),
            )

        target_ids.assert_called_once_with(date(2026, 4, 24), ["2330"])
        reconcile.assert_called_once()
        self.assertEqual(reconcile.call_args.kwargs["target_stock_ids"], {"2330"})
        self.assertTrue(result.reconciled)
        self.assertEqual(result.after_status, "reconciled_with_fills")
        self.assertEqual(result.fills_count, 1)
        self.assertFalse(result.sell_loop_readiness_recorded)

    def test_command_refresh_dashboard_preserves_workflow_status_and_emits_full_refresh_metadata(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"refresh-dashboard-payload-{uuid.uuid4().hex}"
        run_dir = base / "run"
        input_dir = base / "inputs"
        run_dir.mkdir(parents=True, exist_ok=True)
        input_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))

        (run_dir / "preselect.csv").write_text("stock_id\n2330\n", encoding="utf-8")
        (run_dir / "sizing.csv").write_text("stock_id\n2330\n", encoding="utf-8")

        settings = SimpleNamespace(
            providers=SimpleNamespace(
                active="ab_llm_preselect_json",
                options=lambda _name: {"preselect_dir": "data/ab_llm_preselect"},
            ),
            project_root=base,
        )

        def fake_workflow(_args: SimpleNamespace) -> int:
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
            return 0

        args = SimpleNamespace(
            trade_date="2026-04-24",
            prepare_week=False,
            track_until_final=False,
            auto_track=False,
            prepare_llm_selection=False,
            apply_llm_selection=False,
            finalize=False,
            buy_loop=False,
            auto_buy_loop=False,
            live=False,
            confirm_live=False,
            reprice_threshold_ticks=5,
            settle_week=False,
            auto_settle_week=False,
            max_names=10,
        )

        with patch("sinopac_auto_trading.cli.Settings.load", return_value=settings), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            return_value=run_dir,
        ), patch(
            "sinopac_auto_trading.cli.input_dir_for",
            return_value=input_dir,
        ), patch(
            "sinopac_auto_trading.cli._ab_same_day_source_refresh_details",
            return_value={
                "prepare_week": False,
                "finalize": False,
                "trigger_status": "same_day_source_newer_than_local_preselect_artifacts",
                "trigger_artifacts": "../data/ab_llm_preselect/2026-04-24.json",
                "trigger_note": "same-day source reached the workspace after the previous local artifacts",
            },
        ), patch(
            "sinopac_auto_trading.cli.command_render_report",
            return_value=0,
        ), patch(
            "sinopac_auto_trading.cli.command_workflow_status",
            side_effect=fake_workflow,
        ):
            exit_code = command_refresh_dashboard(args)

        self.assertEqual(exit_code, 0)
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "workflow_status_rendered")
        self.assertEqual(state["dashboard_refresh"]["steps_run"], ["render_report", "workflow_status"])
        self.assertEqual(state["dashboard_refresh_status"], "report_only_refresh")
        self.assertEqual(state["dashboard_refresh_steps"], "render_report, workflow_status")
        self.assertIn("render_report, workflow_status", state["dashboard_refresh_note"])
        self.assertEqual(
            state["dashboard_refresh"]["source_refresh_trigger_status"],
            "same_day_source_newer_than_local_preselect_artifacts",
        )
        self.assertEqual(
            state["dashboard_refresh_trigger_status"],
            "same_day_source_newer_than_local_preselect_artifacts",
        )
        events = [
            json.loads(line)
            for line in (run_dir / "event_log.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        refresh_event = events[-1]
        self.assertEqual(refresh_event["event_type"], "refresh_dashboard")
        self.assertEqual(refresh_event["metadata"]["steps_run"], ["render_report", "workflow_status"])
        self.assertFalse(refresh_event["metadata"]["live"])
        self.assertFalse(refresh_event["metadata"]["confirm_live"])
        self.assertEqual(
            refresh_event["metadata"]["source_refresh_trigger_status"],
            "same_day_source_newer_than_local_preselect_artifacts",
        )

    def test_command_refresh_dashboard_preserves_last_materialization_summary_during_report_only_refresh(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"refresh-dashboard-last-materialization-{uuid.uuid4().hex}"
        run_dir = base / "run"
        input_dir = base / "inputs"
        run_dir.mkdir(parents=True, exist_ok=True)
        input_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))

        (input_dir / "auto_trade_preselect.csv").write_text("stock_id\n2330\n", encoding="utf-8")
        (run_dir / "preselect.csv").write_text("stock_id\n2330\n", encoding="utf-8")
        (run_dir / "sizing.csv").write_text("stock_id\n2330\n", encoding="utf-8")

        (run_dir / "state.json").write_text(
            json.dumps(
                {
                    "trade_date": "2026-04-24",
                    "status": "workflow_status_rendered",
                    "provider_name": "ab_llm_preselect_json",
                    "dashboard_last_materialization_status": "materialized_without_buy_loop",
                    "dashboard_last_materialization_steps": "prepare_week, finalize, render_report, workflow_status",
                    "dashboard_last_materialization_note": "latest materializing refresh ran prepare_week/finalize without buy_loop",
                    "dashboard_last_materialization_trigger_status": "same_day_source_newer_than_local_preselect_artifacts",
                    "dashboard_last_materialization_trigger_artifacts": "../data/ab_llm_preselect/2026-04-24.json",
                    "dashboard_last_materialization_trigger_note": "same-day source arrived after stale local artifacts",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        settings = SimpleNamespace(
            providers=SimpleNamespace(
                active="ab_llm_preselect_json",
                options=lambda _name: {"preselect_dir": "data/ab_llm_preselect"},
            ),
            project_root=base,
        )

        def fake_workflow(_args: SimpleNamespace) -> int:
            return 0

        args = SimpleNamespace(
            trade_date="2026-04-24",
            prepare_week=False,
            track_until_final=False,
            auto_track=False,
            prepare_llm_selection=False,
            apply_llm_selection=False,
            finalize=False,
            buy_loop=False,
            auto_buy_loop=False,
            live=False,
            confirm_live=False,
            reprice_threshold_ticks=5,
            settle_week=False,
            auto_settle_week=False,
            max_names=10,
        )

        with patch("sinopac_auto_trading.cli.Settings.load", return_value=settings), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            return_value=run_dir,
        ), patch(
            "sinopac_auto_trading.cli.input_dir_for",
            return_value=input_dir,
        ), patch(
            "sinopac_auto_trading.cli._ab_same_day_source_refresh_details",
            return_value={
                "prepare_week": False,
                "finalize": False,
                "trigger_status": "selection_source_already_current",
                "trigger_artifacts": "../data/ab_llm_preselect/2026-04-24.json",
                "trigger_note": "same-day source already matches the local materialized basket artifacts",
            },
        ), patch(
            "sinopac_auto_trading.cli.command_render_report",
            return_value=0,
        ), patch(
            "sinopac_auto_trading.cli.command_workflow_status",
            side_effect=fake_workflow,
        ):
            exit_code = command_refresh_dashboard(args)

        self.assertEqual(exit_code, 0)
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["dashboard_refresh_status"], "report_only_refresh")
        self.assertEqual(state["dashboard_refresh_steps"], "render_report, workflow_status")
        self.assertEqual(state["dashboard_last_materialization_status"], "materialized_without_buy_loop")
        self.assertEqual(
            state["dashboard_last_materialization_steps"],
            "prepare_week, finalize, render_report, workflow_status",
        )
        self.assertEqual(
            state["dashboard_last_materialization_trigger_status"],
            "same_day_source_newer_than_local_preselect_artifacts",
        )

    def test_render_daily_report_flattens_overview_fields_into_snapshot_json(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"render-daily-report-snapshot-{uuid.uuid4().hex}"
        base.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))

        markdown_path = base / "daily.md"
        html_path = base / "daily.html"
        snapshot_path = base / "snapshot.json"
        current_snapshot_path = base / "current_snapshot.json"

        report = {
            "trade_date": "2026-04-24",
            "week_id": "2026-04-20_2026-04-24",
            "mode": "live_guarded",
            "provider_name": "ab_llm_preselect_json",
            "status": "workflow_status_rendered",
            "today_status": "",
            "today_ordering_status": "",
            "selection_source_status": "",
            "dashboard_refresh_status": "",
            "dashboard_refresh_steps": "",
            "dashboard_last_materialization_status": "",
            "dashboard_last_materialization_steps": "",
            "dashboard_refresh": {
                "steps_run": ["render_report", "workflow_status"],
                "live": False,
                "confirm_live": False,
                "source_refresh_trigger_status": "selection_source_already_current",
            },
            "dashboard_refresh_last_materializing": {
                "steps_run": ["prepare_week", "finalize", "render_report", "workflow_status"],
                "live": False,
                "confirm_live": False,
                "source_refresh_trigger_status": "historical_materialization_reason_not_recorded",
            },
            "overview": {
                "today_status": "buy_window_closed",
                "workflow_completed_steps": 12,
                "workflow_pending_steps": 0,
                "workflow_closed_steps": 5,
                "today_ordering_status": "guarded_time_passed_no_backfill+basket_a_loaded+basket_buy_window_closed_last_trade_day",
                "selection_source_status": "same_day_a_preselect_loaded",
                "dashboard_refresh_status": "report_only_refresh",
                "dashboard_refresh_steps": "render_report, workflow_status",
                "dashboard_last_materialization_status": "materialized_without_buy_loop",
                "dashboard_last_materialization_steps": "prepare_week, finalize, render_report, workflow_status",
            },
            "selection_rows": [],
            "buy_orders": [],
            "sell_decisions": [],
            "fills": [],
            "positions": [],
            "excluded_positions": [],
            "pnl_rows": [],
            "event_rows": [],
            "next_actions": [],
            "metrics": [],
            "charts": {},
        }

        render_daily_report(
            report,
            markdown_path,
            html_path,
            snapshot_json_path=snapshot_path,
            current_snapshot_path=current_snapshot_path,
        )

        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        current_snapshot = json.loads(current_snapshot_path.read_text(encoding="utf-8"))
        for payload in (snapshot, current_snapshot):
            self.assertEqual(payload["overview"]["dashboard_refresh_status"], "report_only_refresh")
            self.assertEqual(payload["today_status"], "buy_window_closed")
            self.assertEqual(
                payload["today_ordering_status"],
                "guarded_time_passed_no_backfill+basket_a_loaded+basket_buy_window_closed_last_trade_day",
            )
            self.assertEqual(payload["status"], "workflow_status_rendered")
            self.assertEqual(payload["selection_source_status"], "same_day_a_preselect_loaded")
            self.assertEqual(payload["dashboard_refresh_status"], "report_only_refresh")
            self.assertEqual(payload["dashboard_refresh_steps"], "render_report, workflow_status")
            self.assertIn("dashboard_refresh_trigger_status", payload)
            self.assertEqual(payload["dashboard_refresh_trigger_status"], "")
            self.assertIn("dashboard_refresh_trigger_artifacts", payload)
            self.assertEqual(payload["dashboard_refresh_trigger_artifacts"], "")
            self.assertIn("dashboard_refresh_trigger_note", payload)
            self.assertEqual(payload["dashboard_refresh_trigger_note"], "")
            self.assertEqual(payload["dashboard_last_materialization_status"], "materialized_without_buy_loop")
            self.assertEqual(
                payload["dashboard_last_materialization_steps"],
                "prepare_week, finalize, render_report, workflow_status",
            )
            self.assertEqual(payload["dashboard_refresh"]["steps_run"], ["render_report", "workflow_status"])
            self.assertEqual(
                payload["dashboard_refresh_last_materializing"]["steps_run"],
                ["prepare_week", "finalize", "render_report", "workflow_status"],
            )
        markdown = markdown_path.read_text(encoding="utf-8")
        self.assertIn("- 已完成步驟數: 12", markdown)
        self.assertIn("- 待處理步驟數: 0", markdown)
        self.assertIn("- 今日關閉步驟數: 5", markdown)

    def test_command_render_report_preserves_existing_workflow_status(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"command-render-report-status-{uuid.uuid4().hex}"
        run_dir = base / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))

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

        report = {
            "trade_date": "2026-04-24",
            "week_id": "2026-04-20_2026-04-24",
            "mode": "live_guarded",
            "provider_name": "ab_llm_preselect_json",
            "overview": {},
            "selection_rows": [],
            "buy_orders": [],
            "sell_decisions": [],
            "fills": [],
            "positions": [],
            "excluded_positions": [],
            "pnl_rows": [],
            "event_rows": [],
            "next_actions": [],
            "metrics": [],
            "charts": {},
        }

        settings = SimpleNamespace(
            providers=SimpleNamespace(
                active="ab_llm_preselect_json",
                options=lambda _name: {"preselect_dir": "data/ab_llm_preselect"},
            ),
            project_root=base,
        )

        with patch("sinopac_auto_trading.cli.Settings.load", return_value=settings), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            return_value=run_dir,
        ), patch(
            "sinopac_auto_trading.cli._build_daily_report",
            return_value=report,
        ), patch(
            "sinopac_auto_trading.cli.render_daily_report",
            return_value=None,
        ), patch(
            "sinopac_auto_trading.cli._best_effort_obsidian_sync",
            return_value=None,
        ):
            exit_code = command_render_report(SimpleNamespace(trade_date="2026-04-24"))

        self.assertEqual(exit_code, 0)
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "workflow_status_rendered")

    def test_command_render_report_writes_calendar_and_selection_counts_into_snapshot(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"command-render-report-snapshot-fields-{uuid.uuid4().hex}"
        run_dir = base / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))

        note_path = base / "daily.md"
        html_path = base / "daily.html"
        current_html_path = base / "current.html"
        snapshot_path = base / "snapshot.json"
        current_snapshot_path = base / "current_snapshot.json"

        report = {
            "trade_date": "2026-04-24",
            "week_id": "2026-04-20_2026-04-24",
            "run_id": "auto-2026-04-24",
            "mode": "live_guarded",
            "provider_name": "ab_llm_preselect_json",
            "buy_cutoff_day": "2026-04-22",
            "last_trade_day": "2026-04-24",
            "preselect_count": 10,
            "final_list_count": 10,
            "workflow_status": {"completed_steps": 12, "pending_steps": 0, "closed_steps": 5},
            "workflow_completed_steps": 12,
            "workflow_pending_steps": 0,
            "workflow_closed_steps": 5,
            "overview": {
                "today_status": "buy_window_closed",
                "workflow_completed_steps": 12,
                "workflow_pending_steps": 0,
                "workflow_closed_steps": 5,
            },
            "dashboard_refresh": {
                "steps_run": ["render_report", "workflow_status"],
            },
            "dashboard_refresh_last_materializing": {
                "steps_run": ["prepare_week", "finalize", "render_report", "workflow_status"],
            },
            "selection_rows": [],
            "buy_execution_rows": [],
            "positions_rows": [],
            "excluded_positions_rows": [],
            "broker_underheld_rows": [],
            "ambiguous_fill_rows": [],
            "sell_rows": [],
            "guarded_live_task_evidence": {},
            "post_guarded_order_check": {
                "schedule_description": "Run the only allowed SinoPac live order automation on 2026-04-24: 2330 Buy IntradayOdd 1 share at 09:10 with price cap 2100, with duplicate-order guard.",
            },
            "sell_loop_readiness": {},
            "basket_summary": {},
            "comparison_chart": {},
            "capital_chart": {},
            "events": [],
            "warnings": [],
            "next_actions": [],
        }

        settings = SimpleNamespace(
            providers=SimpleNamespace(
                active="ab_llm_preselect_json",
                options=lambda _name: {"preselect_dir": "data/ab_llm_preselect"},
            ),
            project_root=base,
        )

        with patch("sinopac_auto_trading.cli.Settings.load", return_value=settings), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            return_value=run_dir,
        ), patch(
            "sinopac_auto_trading.cli._build_daily_report",
            return_value=report,
        ), patch(
            "sinopac_auto_trading.cli.daily_note_path",
            return_value=note_path,
        ), patch(
            "sinopac_auto_trading.cli.daily_html_report_path",
            return_value=html_path,
        ), patch(
            "sinopac_auto_trading.cli.current_html_report_path",
            return_value=current_html_path,
        ), patch(
            "sinopac_auto_trading.cli.dated_snapshot_json_path",
            return_value=snapshot_path,
        ), patch(
            "sinopac_auto_trading.cli.current_snapshot_json_path",
            return_value=current_snapshot_path,
        ), patch(
            "sinopac_auto_trading.cli._best_effort_obsidian_sync",
            return_value=None,
        ):
            exit_code = command_render_report(SimpleNamespace(trade_date="2026-04-24"))

        self.assertEqual(exit_code, 0)
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        current_snapshot = json.loads(current_snapshot_path.read_text(encoding="utf-8"))
        for payload in (snapshot, current_snapshot):
            self.assertEqual(payload["buy_cutoff_day"], "2026-04-22")
            self.assertEqual(payload["last_trade_day"], "2026-04-24")
            self.assertEqual(payload["preselect_count"], 10)
            self.assertEqual(payload["final_list_count"], 10)
            self.assertEqual(payload["workflow_status"]["completed_steps"], 12)
            self.assertEqual(payload["workflow_completed_steps"], 12)
            self.assertEqual(payload["workflow_pending_steps"], 0)
            self.assertEqual(payload["workflow_closed_steps"], 5)
            self.assertEqual(
                payload["post_guarded_order_check"]["schedule_description"],
                "只允許的永豐真實下單自動化：2026-04-24，2330 買進盤中零股 1 股，09:10，價格上限 2100，含重複單保護。",
            )

    def test_command_render_report_outputs_do_not_emit_known_mojibake_tokens(self) -> None:
        base = Path(__file__).resolve().parent / "_tmp" / f"command-render-report-cleanliness-{uuid.uuid4().hex}"
        run_dir = base / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))

        note_path = base / "daily.md"
        html_path = base / "daily.html"
        current_html_path = base / "current.html"
        snapshot_path = base / "snapshot.json"
        current_snapshot_path = base / "current_snapshot.json"

        report = {
            "trade_date": "2026-04-24",
            "week_id": "2026-04-20_2026-04-24",
            "run_id": "auto-2026-04-24",
            "mode": "live_guarded",
            "provider_name": "ab_llm_preselect_json",
            "buy_cutoff_day": "2026-04-22",
            "last_trade_day": "2026-04-24",
            "preselect_count": 10,
            "final_list_count": 10,
            "workflow_status": {"completed_steps": 12, "pending_steps": 0, "closed_steps": 5},
            "overview": {
                "today_status": "buy_window_closed",
                "today_status_note": "今天已沒有任何可送出的自動新買單路徑。",
                "today_ordering_status": "guarded_time_passed_no_backfill+basket_a_loaded+basket_buy_window_closed_last_trade_day",
                "today_ordering_note": "2330 受保護下單路徑的保護條件問題雖已修好，但今天 09:10 排程時間已過，不會補單。 同日 A 預選來源已同步成本地整包產物，但今天已是最後交易日，不會再開新買單。",
                "selection_source_status": "same_day_a_preselect_loaded",
                "selection_source_note": "同日 A 預選來源已同步成目前本地整包產物；來源檔：data/ab_llm_preselect/2026-04-24.json。",
                "dashboard_refresh_status": "report_only_refresh",
                "dashboard_refresh_note": "最新 refresh 只重跑 render_report 與 workflow_status，不會覆蓋最近一次 materializing refresh。",
                "guarded_post_check_effective_recommendation_note": "保護條件問題已修好，但今天排程時間已過，不會補單。",
                "sell_loop_readiness_next_action_note": "今天的受保護下單執行已錯過，請等待下一次排程，不會回補今天的單。",
                "workflow_completed_steps": 12,
                "workflow_pending_steps": 0,
                "workflow_closed_steps": 5,
            },
            "dashboard_refresh": {"steps_run": ["render_report", "workflow_status"]},
            "dashboard_refresh_last_materializing": {
                "steps_run": ["prepare_week", "finalize", "render_report", "workflow_status"]
            },
            "selection_rows": [],
            "buy_execution_rows": [],
            "positions_rows": [],
            "excluded_positions_rows": [],
            "broker_underheld_rows": [],
            "ambiguous_fill_rows": [],
            "sell_rows": [],
            "guarded_live_task_evidence": {},
            "post_guarded_order_check": {
                "schedule_description": "Run the only allowed SinoPac live order automation on 2026-04-24: 2330 Buy IntradayOdd 1 share at 09:10 with price cap 2100, with duplicate-order guard.",
            },
            "sell_loop_readiness": {},
            "basket_summary": {},
            "comparison_chart": {},
            "capital_chart": {},
            "events": [],
            "warnings": [],
            "next_actions": ["等待下一個交易日的 fresh A 預選檔。"],
        }

        settings = SimpleNamespace(
            providers=SimpleNamespace(
                active="ab_llm_preselect_json",
                options=lambda _name: {"preselect_dir": "data/ab_llm_preselect"},
            ),
            project_root=base,
        )

        with patch("sinopac_auto_trading.cli.Settings.load", return_value=settings), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            return_value=run_dir,
        ), patch(
            "sinopac_auto_trading.cli._build_daily_report",
            return_value=report,
        ), patch(
            "sinopac_auto_trading.cli.daily_note_path",
            return_value=note_path,
        ), patch(
            "sinopac_auto_trading.cli.daily_html_report_path",
            return_value=html_path,
        ), patch(
            "sinopac_auto_trading.cli.current_html_report_path",
            return_value=current_html_path,
        ), patch(
            "sinopac_auto_trading.cli.dated_snapshot_json_path",
            return_value=snapshot_path,
        ), patch(
            "sinopac_auto_trading.cli.current_snapshot_json_path",
            return_value=current_snapshot_path,
        ), patch(
            "sinopac_auto_trading.cli._best_effort_obsidian_sync",
            return_value=None,
        ):
            exit_code = command_render_report(SimpleNamespace(trade_date="2026-04-24"))

        self.assertEqual(exit_code, 0)
        contents = [
            note_path.read_text(encoding="utf-8"),
            html_path.read_text(encoding="utf-8"),
            current_html_path.read_text(encoding="utf-8"),
            snapshot_path.read_text(encoding="utf-8"),
            current_snapshot_path.read_text(encoding="utf-8"),
        ]
        for text in contents:
            for token in self._BAD_MOJIBAKE_TOKENS:
                self.assertNotIn(token, text)
            assert_text_has_no_legacy_english_status_tokens(self, text)


if __name__ == "__main__":
    unittest.main()
