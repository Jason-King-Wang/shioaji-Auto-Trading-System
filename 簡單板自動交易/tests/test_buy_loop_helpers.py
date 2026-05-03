from __future__ import annotations

import json
import shutil
import unittest
import uuid
from csv import DictWriter
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sinopac_auto_trading.basket import broker_custom_field_for_strategy_lot
from sinopac_auto_trading.broker_adapter import BrokerOrderResult, ManagedOrderSnapshot, PositionSnapshot
from sinopac_auto_trading.calendar import WeekTradePlan
from sinopac_auto_trading.cli import (
    _apply_local_sell_state_to_positions_rows,
    _ambiguous_fill_rows,
    _affordable_order_qty,
    _already_bought_qty_by_lot,
    _build_daily_report,
    _apply_local_sell_pnl_fallback,
    _buy_loop_source_trade_date,
    _buy_loop_skip_reason,
    _existing_buy_order_state,
    _existing_sell_order_state,
    _excluded_positions_rows,
    _effective_buy_cutoff_day,
    _load_latest_quote_rows_for_stock_ids,
    _live_buy_repair_required_reason,
    _positions_rows_from_local_orders,
    _read_csv_rows,
    _load_strategy_positions_for_sell_loop,
    _positions_rows_from_fills,
    _secondary_add_allowed_on_trade_date,
    _selected_fill_rows,
    _selected_positions_rows,
    _sell_loop_readiness_summary,
    _write_sell_loop_readiness,
    _sell_fill_stats_by_stock,
    command_buy_loop,
    command_sell_loop,
)
from sinopac_auto_trading.config import AutoTradingConfig, FeeConfig, ProviderConfig, Settings
from sinopac_auto_trading.order_engine import QuoteState
from sinopac_auto_trading.sell_policy import StrategyPosition


class BuyLoopHelperTests(unittest.TestCase):
    class _Broker:
        def __init__(self, fills: list[dict[str, object]]) -> None:
            self._fills = fills

        def get_fills(self, since=None) -> list[dict[str, object]]:
            return list(self._fills)

    class _PositionBroker:
        def __init__(self, positions: list[PositionSnapshot]) -> None:
            self._positions = positions

        def get_positions(self) -> list[PositionSnapshot]:
            return list(self._positions)

    class _SellLoopBroker:
        def __init__(
            self,
            *,
            managed_orders: dict[str, ManagedOrderSnapshot] | None = None,
            positions: list[PositionSnapshot] | None = None,
            fills: list[dict[str, object]] | None = None,
        ) -> None:
            self._managed_orders = managed_orders or {}
            self._positions = positions or []
            self._fills = fills or []
            self.place_sell_calls: list[tuple[str, float, int, str, dict[str, object]]] = []

        def get_account_summary(self):
            return SimpleNamespace(signed=True)

        def is_market_open(self) -> bool:
            return True

        def get_quote_state(self, stock_id: str):
            return (
                QuoteState(last_price=120.0, bid1=120.0, ask1=120.5),
                "TSMC",
                "TSE",
                "2026-04-24T13:05:00+08:00",
            )

        def get_managed_order(self, order_id: str) -> ManagedOrderSnapshot | None:
            return self._managed_orders.get(order_id)

        def place_sell_order(self, stock_id: str, price: float, qty: int, order_lot: str, metadata: dict[str, object]):
            self.place_sell_calls.append((stock_id, price, qty, order_lot, metadata))
            raise AssertionError("sell_loop should not place a duplicate sell order when an active order already exists")

        def supports_order_lot(self, order_lot: str) -> bool:
            return True

        def get_positions(self) -> list[PositionSnapshot]:
            return list(self._positions)

        def get_fills(self, since=None) -> list[dict[str, object]]:
            return list(self._fills)

    class _BuyLoopBroker:
        def __init__(
            self,
            *args,
            managed_orders: dict[str, ManagedOrderSnapshot] | None = None,
            positions: list[PositionSnapshot] | None = None,
            fills: list[dict[str, object]] | None = None,
            cash_available: float = 500000.0,
            **kwargs,
        ) -> None:
            self._managed_orders = managed_orders or {}
            self._positions = positions or []
            self._fills = fills or []
            self._cash_available = cash_available
            self.place_buy_calls: list[tuple[str, float, int, str, dict[str, object]]] = []
            self.cancel_calls: list[str] = []

        def get_account_summary(self):
            return SimpleNamespace(signed=True)

        def is_market_open(self) -> bool:
            return True

        def supports_order_lot(self, order_lot: str) -> bool:
            return True

        def get_cash_available(self) -> float:
            return self._cash_available

        def get_quote_state(self, stock_id: str):
            return (
                QuoteState(last_price=100.0, bid1=99.5, ask1=100.0),
                "TSMC",
                "TSE",
                "2026-04-22T12:45:00+08:00",
            )

        def get_managed_order(self, order_id: str) -> ManagedOrderSnapshot | None:
            return self._managed_orders.get(order_id)

        def place_buy_order(self, stock_id: str, price: float, qty: int, order_lot: str, metadata: dict[str, object]):
            self.place_buy_calls.append((stock_id, price, qty, order_lot, metadata))
            raise AssertionError("buy_loop should not place a duplicate buy order when a submitted order already exists")

        def cancel_order(self, order_id: str) -> None:
            self.cancel_calls.append(order_id)

        def get_positions(self) -> list[PositionSnapshot]:
            return list(self._positions)

        def get_fills(self, since=None) -> list[dict[str, object]]:
            return list(self._fills)

    def _case_dir(self, name: str) -> Path:
        path = Path(__file__).resolve().parent / "_tmp" / f"{name}-{uuid.uuid4().hex}"
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    @staticmethod
    def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    def test_affordable_order_qty_keeps_requested_odd_lot_when_budget_is_enough(self) -> None:
        self.assertEqual(
            _affordable_order_qty(requested_qty=3, target_price=100.0, remaining_budget=500.0),
            3,
        )

    def test_affordable_order_qty_caps_odd_lot_by_remaining_budget(self) -> None:
        self.assertEqual(
            _affordable_order_qty(requested_qty=5, target_price=120.0, remaining_budget=260.0),
            2,
        )

    def test_affordable_order_qty_rounds_common_lot_by_thousand_shares(self) -> None:
        self.assertEqual(
            _affordable_order_qty(
                requested_qty=3000,
                target_price=10.0,
                remaining_budget=25000.0,
                order_lot="common",
            ),
            2000,
        )

    def test_secondary_add_gate_only_opens_on_second_trade_day(self) -> None:
        settings = type("SettingsStub", (), {"auto_trading": type("AutoStub", (), {"enable_secondary_add": True})()})()
        settings.auto_trading = type(
            "AutoTradingStub",
            (),
            {
                "enable_secondary_add": True,
                "secondary_add_budget_pct_min": 0.30,
                "secondary_add_budget_pct_max": 0.40,
            },
        )()
        plan = WeekTradePlan(
            anchor_date=date(2026, 4, 24),
            week_trade_days=[date(2026, 4, 21), date(2026, 4, 22), date(2026, 4, 24)],
            buy_cutoff_day=date(2026, 4, 22),
            last_trade_day=date(2026, 4, 24),
            calendar_missing_warning=False,
            source_path=None,
        )
        self.assertFalse(_secondary_add_allowed_on_trade_date(settings, date(2026, 4, 21), plan))
        self.assertTrue(_secondary_add_allowed_on_trade_date(settings, date(2026, 4, 22), plan))

    def test_buy_loop_skip_reason_blocks_after_first_trade_day_when_chase_is_disabled(self) -> None:
        plan = WeekTradePlan(
            anchor_date=date(2026, 4, 24),
            week_trade_days=[date(2026, 4, 20), date(2026, 4, 21), date(2026, 4, 22), date(2026, 4, 24)],
            buy_cutoff_day=date(2026, 4, 22),
            last_trade_day=date(2026, 4, 24),
            calendar_missing_warning=False,
            source_path=None,
        )
        settings = SimpleNamespace(
            auto_trading=AutoTradingConfig(
                weekly_budget=100000,
                overrun_tolerance=0.0,
                allow_buy_after_first_trade_day=False,
            )
        )

        self.assertEqual(_effective_buy_cutoff_day(settings, plan), date(2026, 4, 20))
        self.assertEqual(_buy_loop_skip_reason(settings, date(2026, 4, 20), plan), "")
        self.assertEqual(
            _buy_loop_skip_reason(settings, date(2026, 4, 21), plan),
            "after_first_trade_day_buy_chase_disabled",
        )

    def test_buy_loop_skip_reason_can_keep_legacy_cutoff_when_chase_is_enabled(self) -> None:
        plan = WeekTradePlan(
            anchor_date=date(2026, 4, 24),
            week_trade_days=[date(2026, 4, 20), date(2026, 4, 21), date(2026, 4, 22), date(2026, 4, 24)],
            buy_cutoff_day=date(2026, 4, 22),
            last_trade_day=date(2026, 4, 24),
            calendar_missing_warning=False,
            source_path=None,
        )
        settings = SimpleNamespace(
            auto_trading=AutoTradingConfig(
                weekly_budget=100000,
                overrun_tolerance=0.0,
                allow_buy_after_first_trade_day=True,
            )
        )

        self.assertEqual(_effective_buy_cutoff_day(settings, plan), date(2026, 4, 22))
        self.assertEqual(_buy_loop_skip_reason(settings, date(2026, 4, 21), plan), "")
        self.assertEqual(_buy_loop_skip_reason(settings, date(2026, 4, 23), plan), "after_buy_cutoff_day")
        self.assertEqual(_buy_loop_source_trade_date(settings, date(2026, 4, 21), plan), date(2026, 4, 20))

    def test_live_buy_repair_required_reason_records_live_gate_blocks(self) -> None:
        self.assertEqual(
            _live_buy_repair_required_reason(
                requested_live=True,
                can_go_live=False,
                live_guard="weekly_budget_missing",
                order_rows=[],
            ),
            "live_gate:weekly_budget_missing",
        )

    def test_live_buy_repair_required_reason_records_failed_or_incomplete_live_rows(self) -> None:
        reason = _live_buy_repair_required_reason(
            requested_live=True,
            can_go_live=True,
            live_guard="live_confirmed",
            order_rows=[
                {
                    "stock_id": "2330",
                    "target_qty": 2,
                    "filled_qty": 0,
                    "active_order_qty": 0,
                    "action": "place",
                    "status": "Failed",
                },
                {
                    "stock_id": "2454",
                    "target_qty": 3,
                    "filled_qty": 0,
                    "active_order_qty": 1,
                    "action": "place",
                    "status": "Submitted",
                },
            ],
        )

        self.assertIn("2330:failed", reason)
        self.assertIn("2454:not_fully_submitted", reason)

    def test_live_buy_repair_required_reason_allows_fully_submitted_live_rows(self) -> None:
        self.assertEqual(
            _live_buy_repair_required_reason(
                requested_live=True,
                can_go_live=True,
                live_guard="live_confirmed",
                order_rows=[
                    {
                        "stock_id": "2330",
                        "target_qty": 2,
                        "filled_qty": 0,
                        "active_order_qty": 2,
                        "action": "place",
                        "status": "Submitted",
                    }
                ],
            ),
            "",
        )

    def test_positions_rows_from_fills_use_strategy_scoped_cost_and_latest_quote(self) -> None:
        fills_rows = [
            {"stock_id": "2330", "side": "Buy", "fill_qty": 1, "fill_price": 100.0},
            {"stock_id": "2330", "side": "Buy", "fill_qty": 2, "fill_price": 103.0},
            {"stock_id": "2330", "side": "Sell", "fill_qty": 1, "fill_price": 110.0},
        ]
        rows = _positions_rows_from_fills(
            trade_date=date(2026, 4, 21),
            fills_rows=fills_rows,
            selection_meta_by_stock={"2330": {"stock_name": "TSMC", "source": "A+B"}},
            quote_rows_by_stock={"2330": QuoteState(last_price=105.0, bid1=104.0, ask1=105.0)},
        )
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["stock_id"], "2330")
        self.assertEqual(row["holding_qty"], 2)
        self.assertEqual(row["stock_name"], "TSMC")
        self.assertEqual(row["source"], "A+B")
        self.assertAlmostEqual(float(row["buy_avg_price"]), 102.0)
        self.assertAlmostEqual(float(row["buy_total_cost"]), 204.0)
        self.assertAlmostEqual(float(row["current_price"]), 105.0)
        self.assertEqual(row["status"], "strategy_fill_scoped")

    def test_positions_rows_from_local_orders_use_local_fill_qty_and_order_price(self) -> None:
        rows = _positions_rows_from_local_orders(
            trade_date=date(2026, 4, 22),
            order_rows=[
                {
                    "strategy_lot_id": "auto-2026-04-22:secondary_add:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "basket_tag": "secondary_add",
                    "order_price": 101.0,
                    "filled_qty": 2,
                    "last_price": 103.0,
                }
            ],
            selection_meta_by_stock={"2330": {"stock_name": "TSMC", "source": "A", "basket_tag": "main"}},
            selection_meta_by_strategy_lot={
                "auto-2026-04-22:secondary_add:2330": {
                    "stock_name": "TSMC",
                    "source": "B",
                    "basket_tag": "secondary_add",
                }
            },
            quote_rows_by_stock={"2330": QuoteState(last_price=104.0, bid1=103.5, ask1=104.0)},
        )
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["strategy_lot_id"], "auto-2026-04-22:secondary_add:2330")
        self.assertEqual(row["holding_qty"], 2)
        self.assertEqual(row["source"], "B")
        self.assertEqual(row["basket_tag"], "secondary_add")
        self.assertAlmostEqual(float(row["buy_avg_price"]), 101.0)
        self.assertAlmostEqual(float(row["buy_total_cost"]), 202.0)
        self.assertAlmostEqual(float(row["current_price"]), 104.0)
        self.assertEqual(row["status"], "local_order_fill_fallback")

    def test_apply_local_sell_state_to_positions_rows_reduces_holdings_when_local_state_is_more_conservative(self) -> None:
        adjusted_rows, adjusted_lot_ids = _apply_local_sell_state_to_positions_rows(
            trade_date=date(2026, 4, 24),
            positions_rows=[
                {
                    "run_id": "auto-2026-04-24",
                    "strategy_lot_id": "auto-2026-04-21:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "source": "A",
                    "basket_tag": "main",
                    "holding_qty": 3,
                    "buy_avg_price": 100.0,
                    "buy_total_cost": 300.0,
                    "current_price": 121.0,
                    "status": "strategy_fill_scoped",
                }
            ],
            opening_positions=[
                StrategyPosition(
                    strategy_lot_id="auto-2026-04-21:2330",
                    stock_id="2330",
                    stock_name="TSMC",
                    holding_qty=3,
                    buy_avg_price=100.0,
                    buy_total_cost=300.0,
                    source="A",
                    basket_tag="main",
                )
            ],
            sell_rows=[
                {
                    "strategy_lot_id": "auto-2026-04-21:2330",
                    "sold_qty": 1,
                    "remaining_qty": 2,
                }
            ],
            quote_rows_by_stock={"2330": QuoteState(last_price=122.0, bid1=121.5, ask1=122.0)},
        )
        self.assertEqual(adjusted_lot_ids, ["auto-2026-04-21:2330"])
        self.assertEqual(len(adjusted_rows), 1)
        row = adjusted_rows[0]
        self.assertEqual(row["holding_qty"], 2)
        self.assertEqual(row["status"], "local_sell_fill_fallback")
        self.assertAlmostEqual(float(row["buy_total_cost"]), 200.0)
        self.assertAlmostEqual(float(row["current_price"]), 122.0)

    def test_apply_local_sell_pnl_fallback_estimates_realized_pnl_from_local_order_state(self) -> None:
        updated_rows, fallback_lot_ids = _apply_local_sell_pnl_fallback(
            sell_rows=[
                {
                    "strategy_lot_id": "auto-2026-04-21:2330",
                    "sell_order_price": 119.0,
                    "sold_qty": 1,
                    "remaining_qty": 2,
                    "actual_fill_avg_price": "",
                    "allocated_buy_cost": "",
                    "realized_pnl": "",
                }
            ],
            opening_positions=[
                StrategyPosition(
                    strategy_lot_id="auto-2026-04-21:2330",
                    stock_id="2330",
                    stock_name="TSMC",
                    holding_qty=3,
                    buy_avg_price=100.0,
                    buy_total_cost=300.0,
                    source="A",
                    basket_tag="main",
                )
            ],
            fees=FeeConfig(
                commission_rate=0.0,
                commission_discount=1.0,
                minimum_commission=0.0,
                transaction_tax_rate_stock=0.0,
                transaction_tax_rate_day_trade=0.0,
            ),
        )
        self.assertEqual(fallback_lot_ids, ["auto-2026-04-21:2330"])
        self.assertEqual(len(updated_rows), 1)
        row = updated_rows[0]
        self.assertEqual(row["sell_pnl_source"], "local_sell_order_fallback")
        self.assertAlmostEqual(float(row["actual_fill_avg_price"]), 119.0)
        self.assertAlmostEqual(float(row["allocated_buy_cost"]), 100.0)
        self.assertAlmostEqual(float(row["realized_pnl"]), 19.0)

    def test_load_strategy_positions_for_sell_loop_picks_latest_week_positions(self) -> None:
        root = self._case_dir("sell-loop-positions")
        self._write_csv(
            root / "2026-04-21" / "positions.csv",
            [
                {
                    "strategy_lot_id": "run-21:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "holding_qty": 1,
                    "buy_avg_price": 1000,
                    "buy_total_cost": 1000,
                    "source": "A",
                }
            ],
        )
        self._write_csv(
            root / "2026-04-22" / "positions.csv",
            [
                {
                    "strategy_lot_id": "run-22:2454",
                    "stock_id": "2454",
                    "stock_name": "MediaTek",
                    "holding_qty": 2,
                    "buy_avg_price": 1200,
                    "buy_total_cost": 2400,
                    "source": "B",
                }
            ],
        )
        plan = WeekTradePlan(
            anchor_date=date(2026, 4, 24),
            week_trade_days=[date(2026, 4, 21), date(2026, 4, 22), date(2026, 4, 24)],
            buy_cutoff_day=date(2026, 4, 22),
            last_trade_day=date(2026, 4, 24),
            calendar_missing_warning=False,
            source_path=None,
        )
        with patch("sinopac_auto_trading.cli.resolve_week_trade_plan", return_value=plan), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            side_effect=lambda trade_date: root / trade_date.isoformat(),
        ):
            positions, source_date, _ = _load_strategy_positions_for_sell_loop(date(2026, 4, 24))
        self.assertEqual(source_date, date(2026, 4, 22))
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0].stock_id, "2454")
        self.assertEqual(positions[0].holding_qty, 2)

    def test_sell_loop_readiness_allows_prepare_only_before_last_trade_day_when_positions_exist(self) -> None:
        root = self._case_dir("sell-loop-readiness-prepare-before-last")
        trade_date = date(2026, 4, 22)
        last_trade_day = date(2026, 4, 24)
        plan = WeekTradePlan(
            anchor_date=trade_date,
            week_trade_days=[trade_date, last_trade_day],
            buy_cutoff_day=trade_date,
            last_trade_day=last_trade_day,
            calendar_missing_warning=False,
            source_path=None,
        )
        self._write_csv(
            root / trade_date.isoformat() / "positions.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-22:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "source": "A",
                    "basket_tag": "main",
                    "holding_qty": 1,
                    "buy_avg_price": 100.0,
                    "buy_total_cost": 100.0,
                }
            ],
        )

        with patch("sinopac_auto_trading.cli.resolve_week_trade_plan", return_value=plan), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ):
            result = _sell_loop_readiness_summary(trade_date)

        self.assertFalse(result.is_last_trade_day)
        self.assertTrue(result.positions_ready)
        self.assertEqual(result.blocking_reason, "ready_to_prepare")
        self.assertEqual(result.next_action, "run_sell_loop_prepare_only_after_market_open")

    def test_write_sell_loop_readiness_records_ready_artifact(self) -> None:
        root = self._case_dir("sell-loop-readiness-ready")
        trade_date = date(2026, 4, 24)
        plan = WeekTradePlan(
            anchor_date=trade_date,
            week_trade_days=[trade_date],
            buy_cutoff_day=trade_date,
            last_trade_day=trade_date,
            calendar_missing_warning=False,
            source_path=None,
        )
        self._write_csv(
            root / trade_date.isoformat() / "positions.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-24:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "source": "A",
                    "basket_tag": "main",
                    "holding_qty": 1,
                    "buy_avg_price": 100.0,
                    "buy_total_cost": 100.0,
                }
            ],
        )
        (root / trade_date.isoformat() / "post_guarded_order_check.json").write_text(
            json.dumps(
                {
                    "after_status": "reconciled_with_fills",
                    "recommendation": "fills_found_review_positions_and_sell_loop",
                    "fills_count": 1,
                    "positions_count": 1,
                }
            ),
            encoding="utf-8",
        )

        with patch("sinopac_auto_trading.cli.resolve_week_trade_plan", return_value=plan), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ):
            result = _write_sell_loop_readiness(trade_date)

        self.assertEqual(result.blocking_reason, "ready_to_evaluate")
        artifact = json.loads((root / trade_date.isoformat() / "sell_loop_readiness.json").read_text(encoding="utf-8"))
        self.assertEqual(artifact["positions_count"], 1)
        self.assertEqual(artifact["post_guarded_status"], "reconciled_with_fills")
        state = json.loads((root / trade_date.isoformat() / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "sell_loop_readiness_checked")
        self.assertEqual(state["sell_loop_readiness"]["blocking_reason"], "ready_to_evaluate")

    def test_sell_loop_readiness_recommends_reconcile_when_submitted_without_fills(self) -> None:
        root = self._case_dir("sell-loop-readiness-reconcile")
        trade_date = date(2026, 4, 24)
        plan = WeekTradePlan(
            anchor_date=trade_date,
            week_trade_days=[trade_date],
            buy_cutoff_day=trade_date,
            last_trade_day=trade_date,
            calendar_missing_warning=False,
            source_path=None,
        )
        self._write_csv(
            root / trade_date.isoformat() / "positions.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-24:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "source": "A",
                    "basket_tag": "main",
                    "holding_qty": 1,
                    "buy_avg_price": 100.0,
                    "buy_total_cost": 100.0,
                }
            ],
        )
        (root / trade_date.isoformat() / "post_guarded_order_check.json").write_text(
            json.dumps(
                {
                    "after_status": "submitted_no_fills_yet",
                    "recommendation": "run_reconcile_broker_state_after_market_updates",
                    "fills_count": 0,
                    "positions_count": 0,
                }
            ),
            encoding="utf-8",
        )

        with patch("sinopac_auto_trading.cli.resolve_week_trade_plan", return_value=plan), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ):
            result = _sell_loop_readiness_summary(trade_date)

        self.assertEqual(result.blocking_reason, "broker_reconcile_recommended")
        self.assertEqual(result.next_action, "run_post_guarded_order_check_with_live_reconcile_after_market_updates")

    def test_load_latest_quote_rows_for_stock_ids_merges_latest_per_symbol(self) -> None:
        root = self._case_dir("latest-quotes")
        self._write_csv(
            root / "2026-04-21" / "quote_snapshots.csv",
            [
                {"stock_id": "2330", "last_price": 1000, "bid1": 999, "ask1": 1000},
                {"stock_id": "2454", "last_price": 1200, "bid1": 1199, "ask1": 1200},
            ],
        )
        self._write_csv(
            root / "2026-04-22" / "quote_snapshots.csv",
            [
                {"stock_id": "2330", "last_price": 1010, "bid1": 1009, "ask1": 1010},
            ],
        )
        plan = WeekTradePlan(
            anchor_date=date(2026, 4, 24),
            week_trade_days=[date(2026, 4, 21), date(2026, 4, 22), date(2026, 4, 24)],
            buy_cutoff_day=date(2026, 4, 22),
            last_trade_day=date(2026, 4, 24),
            calendar_missing_warning=False,
            source_path=None,
        )
        with patch("sinopac_auto_trading.cli.resolve_week_trade_plan", return_value=plan), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            side_effect=lambda trade_date: root / trade_date.isoformat(),
        ):
            rows = _load_latest_quote_rows_for_stock_ids(date(2026, 4, 24), {"2330", "2454"})
        self.assertEqual(rows["2330"]["last_price"], "1010")
        self.assertEqual(rows["2454"]["last_price"], "1200")

    def test_selected_fill_rows_use_order_id_mapping_from_week_ledgers(self) -> None:
        root = self._case_dir("fill-order-id-mapping")
        self._write_csv(
            root / "2026-04-21" / "orders.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-21:2330",
                    "stock_id": "2330",
                    "order_id": "BUY-2330-1",
                }
            ],
        )
        self._write_csv(
            root / "2026-04-24" / "sell_decisions.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-21:2330",
                    "stock_id": "2330",
                    "sell_order_id": "SELL-2330-1",
                }
            ],
        )
        plan = WeekTradePlan(
            anchor_date=date(2026, 4, 24),
            week_trade_days=[date(2026, 4, 21), date(2026, 4, 22), date(2026, 4, 24)],
            buy_cutoff_day=date(2026, 4, 22),
            last_trade_day=date(2026, 4, 24),
            calendar_missing_warning=False,
            source_path=None,
        )
        broker = self._Broker(
            [
                {
                    "order_id": "SELL-2330-1",
                    "stock_id": "2330",
                    "side": "Sell",
                    "fill_qty": 1,
                    "fill_price": 111.0,
                    "fill_time": "2026-04-24T13:10:00+08:00",
                }
            ]
        )
        with patch("sinopac_auto_trading.cli.resolve_week_trade_plan", return_value=plan), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            side_effect=lambda trade_date: root / trade_date.isoformat(),
        ), patch(
            "sinopac_auto_trading.ledger.resolve_week_trade_plan",
            return_value=plan,
        ), patch(
            "sinopac_auto_trading.ledger.auto_trading_dir_for",
            side_effect=lambda trade_date: root / trade_date.isoformat(),
        ):
            rows = _selected_fill_rows(
                broker=broker,
                trade_date=date(2026, 4, 24),
                target_stock_ids={"2330"},
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["strategy_lot_id"], "auto-2026-04-21:2330")
        self.assertEqual(rows[0]["broker_fill_id"], "SELL-2330-1")

    def test_selected_fill_rows_fall_back_to_trade_date_strategy_lot_when_mapping_missing(self) -> None:
        broker = self._Broker(
            [
                {
                    "order_id": "UNKNOWN-1",
                    "stock_id": "2330",
                    "side": "Buy",
                    "fill_qty": 1,
                    "fill_price": 100.0,
                    "fill_time": "2026-04-22T09:15:00+08:00",
                }
            ]
        )
        plan = WeekTradePlan(
            anchor_date=date(2026, 4, 24),
            week_trade_days=[date(2026, 4, 21), date(2026, 4, 22), date(2026, 4, 24)],
            buy_cutoff_day=date(2026, 4, 22),
            last_trade_day=date(2026, 4, 24),
            calendar_missing_warning=False,
            source_path=None,
        )
        root = self._case_dir("fill-order-id-fallback")
        with patch("sinopac_auto_trading.cli.resolve_week_trade_plan", return_value=plan), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            side_effect=lambda trade_date: root / trade_date.isoformat(),
        ), patch(
            "sinopac_auto_trading.ledger.resolve_week_trade_plan",
            return_value=plan,
        ), patch(
            "sinopac_auto_trading.ledger.auto_trading_dir_for",
            side_effect=lambda trade_date: root / trade_date.isoformat(),
        ):
            rows = _selected_fill_rows(
                broker=broker,
                trade_date=date(2026, 4, 22),
                target_stock_ids={"2330"},
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["strategy_lot_id"], "auto-2026-04-22:2330")

    def test_selected_fill_rows_resolve_strategy_lot_from_custom_field_lookup(self) -> None:
        root = self._case_dir("fill-custom-field-mapping")
        self._write_csv(
            root / "2026-04-22" / "orders.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-22:secondary_add:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "basket_tag": "secondary_add",
                    "order_id": "BUY-2330-1",
                    "broker_custom_field": "B12345",
                    "status": "submitted",
                }
            ],
        )
        broker = self._Broker(
            [
                {
                    "order_id": "UNKNOWN-1",
                    "broker_custom_field": "B12345",
                    "stock_id": "2330",
                    "side": "Buy",
                    "fill_qty": 1,
                    "fill_price": 100.0,
                    "fill_time": "2026-04-22T09:15:00+08:00",
                }
            ]
        )
        plan = WeekTradePlan(
            anchor_date=date(2026, 4, 24),
            week_trade_days=[date(2026, 4, 22), date(2026, 4, 24)],
            buy_cutoff_day=date(2026, 4, 22),
            last_trade_day=date(2026, 4, 24),
            calendar_missing_warning=False,
            source_path=None,
        )
        with patch("sinopac_auto_trading.cli.resolve_week_trade_plan", return_value=plan), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            side_effect=lambda trade_date: root / trade_date.isoformat(),
        ), patch(
            "sinopac_auto_trading.ledger.resolve_week_trade_plan",
            return_value=plan,
        ), patch(
            "sinopac_auto_trading.ledger.auto_trading_dir_for",
            side_effect=lambda trade_date: root / trade_date.isoformat(),
        ):
            rows = _selected_fill_rows(
                broker=broker,
                trade_date=date(2026, 4, 22),
                target_stock_ids={"2330"},
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["strategy_lot_id"], "auto-2026-04-22:secondary_add:2330")
        self.assertEqual(rows[0]["broker_custom_field"], "B12345")

    def test_selected_fill_rows_mark_ambiguous_when_same_stock_has_multiple_lots_without_mapping(self) -> None:
        root = self._case_dir("fill-ambiguous-mapping")
        self._write_csv(
            root / "2026-04-22" / "orders.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-22:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "basket_tag": "main",
                    "order_id": "BUY-2330-1",
                    "broker_custom_field": "B11111",
                    "status": "submitted",
                },
                {
                    "strategy_lot_id": "auto-2026-04-22:secondary_add:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "basket_tag": "secondary_add",
                    "order_id": "BUY-2330-2",
                    "broker_custom_field": "B22222",
                    "status": "submitted",
                },
            ],
        )
        broker = self._Broker(
            [
                {
                    "order_id": "UNKNOWN-1",
                    "stock_id": "2330",
                    "side": "Buy",
                    "fill_qty": 1,
                    "fill_price": 100.0,
                    "fill_time": "2026-04-22T09:15:00+08:00",
                }
            ]
        )
        plan = WeekTradePlan(
            anchor_date=date(2026, 4, 24),
            week_trade_days=[date(2026, 4, 22), date(2026, 4, 24)],
            buy_cutoff_day=date(2026, 4, 22),
            last_trade_day=date(2026, 4, 24),
            calendar_missing_warning=False,
            source_path=None,
        )
        with patch("sinopac_auto_trading.cli.resolve_week_trade_plan", return_value=plan), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            side_effect=lambda trade_date: root / trade_date.isoformat(),
        ), patch(
            "sinopac_auto_trading.ledger.resolve_week_trade_plan",
            return_value=plan,
        ), patch(
            "sinopac_auto_trading.ledger.auto_trading_dir_for",
            side_effect=lambda trade_date: root / trade_date.isoformat(),
        ):
            rows = _selected_fill_rows(
                broker=broker,
                trade_date=date(2026, 4, 22),
                target_stock_ids={"2330"},
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["strategy_lot_id"], "")
        self.assertEqual(rows[0]["basket_tag"], "")
        self.assertEqual(rows[0]["fill_assignment_status"], "ambiguous_unmapped_fill")

    def test_excluded_positions_rows_capture_broker_qty_above_strategy_scope(self) -> None:
        broker_positions = [
            PositionSnapshot(stock_id="2330", stock_name="TSMC", quantity=3, avg_price=1000.0),
            PositionSnapshot(stock_id="2454", stock_name="MediaTek", quantity=2, avg_price=1200.0),
        ]
        strategy_positions = [
            {"stock_id": "2330", "stock_name": "TSMC", "holding_qty": 1},
        ]
        rows = _excluded_positions_rows(
            broker_positions=broker_positions,
            strategy_positions_rows=strategy_positions,
        )
        self.assertEqual(len(rows), 2)
        by_stock = {row["stock_id"]: row for row in rows}
        self.assertEqual(by_stock["2330"]["excluded_qty"], 2)
        self.assertEqual(by_stock["2330"]["reason"], "broker_qty_exceeds_strategy_qty")
        self.assertEqual(by_stock["2454"]["excluded_qty"], 2)
        self.assertEqual(by_stock["2454"]["reason"], "legacy_non_strategy_holding")

    def test_excluded_positions_rows_sum_same_stock_across_strategy_lots(self) -> None:
        broker_positions = [
            PositionSnapshot(stock_id="2330", stock_name="TSMC", quantity=3, avg_price=1000.0),
        ]
        strategy_positions = [
            {"strategy_lot_id": "auto-2026-04-22:2330", "stock_id": "2330", "stock_name": "TSMC", "holding_qty": 1, "basket_tag": "main"},
            {
                "strategy_lot_id": "auto-2026-04-22:secondary_add:2330",
                "stock_id": "2330",
                "stock_name": "TSMC",
                "holding_qty": 2,
                "basket_tag": "secondary_add",
            },
        ]
        rows = _excluded_positions_rows(
            broker_positions=broker_positions,
            strategy_positions_rows=strategy_positions,
        )
        self.assertEqual(rows, [])

    def test_selected_positions_rows_split_same_stock_using_opening_lots(self) -> None:
        broker = self._PositionBroker(
            [PositionSnapshot(stock_id="2330", stock_name="TSMC", quantity=3, avg_price=97.0)]
        )
        rows = _selected_positions_rows(
            trade_date=date(2026, 4, 22),
            broker=broker,
            target_stock_ids={"2330"},
            selection_meta_by_stock={"2330": {"stock_name": "TSMC", "source": "A+B", "basket_tag": "main"}},
            selection_meta_by_strategy_lot={
                "auto-2026-04-21:2330": {"stock_name": "TSMC", "source": "A", "basket_tag": "main"},
                "auto-2026-04-21:secondary_add:2330": {
                    "stock_name": "TSMC",
                    "source": "B",
                    "basket_tag": "secondary_add",
                },
            },
            quote_rows_by_stock={"2330": QuoteState(last_price=101.0, bid1=100.5, ask1=101.0)},
            opening_positions=[
                StrategyPosition(
                    strategy_lot_id="auto-2026-04-21:2330",
                    stock_id="2330",
                    stock_name="TSMC",
                    holding_qty=1,
                    buy_avg_price=100.0,
                    buy_total_cost=100.0,
                    source="A",
                    basket_tag="main",
                ),
                StrategyPosition(
                    strategy_lot_id="auto-2026-04-21:secondary_add:2330",
                    stock_id="2330",
                    stock_name="TSMC",
                    holding_qty=2,
                    buy_avg_price=95.0,
                    buy_total_cost=190.0,
                    source="B",
                    basket_tag="secondary_add",
                ),
            ],
        )
        self.assertEqual(len(rows), 2)
        by_lot = {row["strategy_lot_id"]: row for row in rows}
        self.assertEqual(by_lot["auto-2026-04-21:2330"]["holding_qty"], 1)
        self.assertEqual(by_lot["auto-2026-04-21:2330"]["status"], "broker_snapshot_opening_lot_scaled")
        self.assertEqual(by_lot["auto-2026-04-21:2330"]["basket_tag"], "main")
        self.assertEqual(by_lot["auto-2026-04-21:secondary_add:2330"]["holding_qty"], 2)
        self.assertEqual(by_lot["auto-2026-04-21:secondary_add:2330"]["basket_tag"], "secondary_add")
        self.assertAlmostEqual(float(by_lot["auto-2026-04-21:secondary_add:2330"]["buy_total_cost"]), 190.0)
        self.assertAlmostEqual(float(by_lot["auto-2026-04-21:secondary_add:2330"]["current_price"]), 101.0)

    def test_existing_sell_order_state_blocks_resubmit_when_unverified(self) -> None:
        broker = self._SellLoopBroker()
        state = _existing_sell_order_state(
            existing_row={
                "sell_order_id": "SELL-2330-1",
                "sell_order_price": 120.0,
                "sell_order_status": "Submitted",
                "sold_qty": 1,
                "remaining_qty": 2,
            },
            broker=broker,
            default_remaining_qty=3,
        )
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state.order_id, "SELL-2330-1")
        self.assertEqual(state.status, "Submitted")
        self.assertEqual(state.gate_reason, "existing_sell_order_unverified")
        self.assertEqual(state.filled_qty, 1)
        self.assertEqual(state.remaining_qty, 2)

    def test_existing_buy_order_state_blocks_resubmit_when_unverified(self) -> None:
        broker = self._BuyLoopBroker()
        state = _existing_buy_order_state(
            existing_row={
                "order_id": "BUY-2330-1",
                "order_price": 100.0,
                "status": "Submitted",
                "filled_qty": 1,
                "active_order_qty": 2,
                "remaining_qty": 2,
            },
            broker=broker,
            default_remaining_qty=3,
        )
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state.order_id, "BUY-2330-1")
        self.assertEqual(state.status, "Submitted")
        self.assertEqual(state.gate_reason, "existing_buy_order_unverified")
        self.assertEqual(state.filled_qty, 1)
        self.assertEqual(state.remaining_qty, 2)

    def test_command_buy_loop_keeps_existing_submitted_buy_order_when_snapshot_missing(self) -> None:
        root = self._case_dir("buy-loop-existing-order")
        trade_date = date(2026, 4, 22)
        self._write_csv(
            root / trade_date.isoformat() / "sizing.csv",
            [
                {
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "source": "A",
                    "basket_tag": "main",
                    "source_weight": 1.0,
                    "target_qty": 3,
                    "estimated_buy_price": 100.0,
                    "projected_cost": 300.0,
                }
            ],
        )
        self._write_csv(
            root / trade_date.isoformat() / "orders.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-22:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "basket_tag": "main",
                    "target_price": 100.0,
                    "target_qty": 3,
                    "active_order_qty": 2,
                    "filled_qty": 1,
                    "remaining_qty": 2,
                    "action": "place",
                    "status": "Submitted",
                    "order_id": "BUY-2330-1",
                    "order_price": 100.0,
                    "broker_custom_field": "B11111",
                    "current_mode": "normal",
                    "last_price": 100.0,
                    "bid1": 99.5,
                    "ask1": 100.0,
                    "quote_timestamp": "2026-04-22T12:45:00+08:00",
                    "buy_submission_gate": "quote_fresh",
                    "note": "submitted_live",
                }
            ],
        )
        plan = WeekTradePlan(
            anchor_date=date(2026, 4, 24),
            week_trade_days=[date(2026, 4, 21), trade_date, date(2026, 4, 24)],
            buy_cutoff_day=trade_date,
            last_trade_day=date(2026, 4, 24),
            calendar_missing_warning=False,
            source_path=None,
        )
        settings = SimpleNamespace(
            fees=FeeConfig(
                commission_rate=0.0,
                commission_discount=1.0,
                minimum_commission=0.0,
                transaction_tax_rate_stock=0.0,
                transaction_tax_rate_day_trade=0.0,
            ),
            auto_trading=SimpleNamespace(
                hard_budget=500000.0,
                quote_stale_seconds=3600,
                cost_buffer_multiplier=1.0,
                live_enabled=True,
                enable_secondary_add=True,
            ),
            allow_live_submit=True,
            live_trading_confirmed=lambda confirm_live: True,
            project_root=Path.cwd(),
        )
        broker_instances: list[BuyLoopHelperTests._BuyLoopBroker] = []

        class _PatchedBuyLoopBroker(self._BuyLoopBroker):
            def __init__(self, *args, **kwargs) -> None:
                super().__init__(*args, **kwargs)
                broker_instances.append(self)

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 4, 22, 12, 50, tzinfo=tz)

        with patch("sinopac_auto_trading.cli.Settings.load", return_value=settings), patch(
            "sinopac_auto_trading.cli.resolve_week_trade_plan",
            return_value=plan,
        ), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ), patch(
            "sinopac_auto_trading.ledger.resolve_week_trade_plan",
            return_value=plan,
        ), patch(
            "sinopac_auto_trading.ledger.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ), patch(
            "sinopac_auto_trading.cli.ShioajiSinoPacBrokerAdapter",
            _PatchedBuyLoopBroker,
        ), patch(
            "sinopac_auto_trading.cli.datetime",
            _FixedDatetime,
        ):
            result = command_buy_loop(
                SimpleNamespace(
                    trade_date=trade_date.isoformat(),
                    live=True,
                    confirm_live=True,
                    reprice_threshold_ticks=1,
                )
            )

        self.assertEqual(result, 0)
        self.assertEqual(len(broker_instances), 1)
        self.assertEqual(len(broker_instances[0].place_buy_calls), 0)
        rows = _read_csv_rows(root / trade_date.isoformat() / "orders.csv")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["order_id"], "BUY-2330-1")
        self.assertEqual(rows[0]["status"], "Submitted")
        self.assertEqual(rows[0]["action"], "keep")
        self.assertEqual(rows[0]["active_order_qty"], "2")
        self.assertEqual(rows[0]["filled_qty"], "1")
        self.assertEqual(rows[0]["buy_submission_gate"], "existing_buy_order_unverified")
        positions_rows = _read_csv_rows(root / trade_date.isoformat() / "positions.csv")
        self.assertEqual(len(positions_rows), 1)
        self.assertEqual(positions_rows[0]["strategy_lot_id"], "auto-2026-04-22:2330")
        self.assertEqual(positions_rows[0]["holding_qty"], "1")
        self.assertEqual(positions_rows[0]["status"], "local_order_fill_fallback")
        self.assertEqual(positions_rows[0]["buy_avg_price"], "100.0")

    def test_command_buy_loop_keeps_broker_order_found_by_custom_field_when_local_order_missing(self) -> None:
        root = self._case_dir("buy-loop-broker-custom-field")
        trade_date = date(2026, 4, 22)
        strategy_lot_id = "auto-2026-04-22:2330"
        broker_custom_field = broker_custom_field_for_strategy_lot(strategy_lot_id, prefix="BL")
        self._write_csv(
            root / trade_date.isoformat() / "sizing.csv",
            [
                {
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "source": "A",
                    "basket_tag": "main",
                    "source_weight": 1.0,
                    "target_qty": 3,
                    "estimated_buy_price": 100.0,
                    "projected_cost": 300.0,
                }
            ],
        )
        plan = WeekTradePlan(
            anchor_date=date(2026, 4, 24),
            week_trade_days=[date(2026, 4, 21), trade_date, date(2026, 4, 24)],
            buy_cutoff_day=trade_date,
            last_trade_day=date(2026, 4, 24),
            calendar_missing_warning=False,
            source_path=None,
        )
        settings = SimpleNamespace(
            fees=FeeConfig(
                commission_rate=0.0,
                commission_discount=1.0,
                minimum_commission=0.0,
                transaction_tax_rate_stock=0.0,
                transaction_tax_rate_day_trade=0.0,
            ),
            auto_trading=SimpleNamespace(
                hard_budget=500000.0,
                quote_stale_seconds=3600,
                cost_buffer_multiplier=1.0,
                live_enabled=True,
                enable_secondary_add=True,
            ),
            allow_live_submit=True,
            live_trading_confirmed=lambda confirm_live: True,
            project_root=Path.cwd(),
        )
        broker_instances: list[BuyLoopHelperTests._BuyLoopBroker] = []

        class _PatchedBuyLoopBroker(self._BuyLoopBroker):
            def __init__(self, *args, **kwargs) -> None:
                super().__init__(*args, **kwargs)
                broker_instances.append(self)

            def get_managed_order_by_custom_field(self, custom_field: str, *, side=None, stock_id=None):
                if custom_field != broker_custom_field or side != "Buy" or stock_id != "2330":
                    return None
                return ManagedOrderSnapshot(
                    order_id="BROKER-BUY-2330-1",
                    stock_id="2330",
                    order_price=100.0,
                    order_qty=3,
                    filled_qty=1,
                    remaining_qty=2,
                    status="active",
                )

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 4, 22, 12, 50, tzinfo=tz)

        with patch("sinopac_auto_trading.cli.Settings.load", return_value=settings), patch(
            "sinopac_auto_trading.cli.resolve_week_trade_plan",
            return_value=plan,
        ), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ), patch(
            "sinopac_auto_trading.ledger.resolve_week_trade_plan",
            return_value=plan,
        ), patch(
            "sinopac_auto_trading.ledger.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ), patch(
            "sinopac_auto_trading.cli.ShioajiSinoPacBrokerAdapter",
            _PatchedBuyLoopBroker,
        ), patch(
            "sinopac_auto_trading.cli.datetime",
            _FixedDatetime,
        ):
            result = command_buy_loop(
                SimpleNamespace(
                    trade_date=trade_date.isoformat(),
                    live=True,
                    confirm_live=True,
                    reprice_threshold_ticks=1,
                )
            )

        self.assertEqual(result, 0)
        self.assertEqual(len(broker_instances), 1)
        self.assertEqual(len(broker_instances[0].place_buy_calls), 0)
        rows = _read_csv_rows(root / trade_date.isoformat() / "orders.csv")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["strategy_lot_id"], strategy_lot_id)
        self.assertEqual(rows[0]["order_id"], "BROKER-BUY-2330-1")
        self.assertEqual(rows[0]["status"], "active")
        self.assertEqual(rows[0]["action"], "keep")
        self.assertEqual(rows[0]["active_order_qty"], "2")
        self.assertEqual(rows[0]["filled_qty"], "1")
        self.assertEqual(rows[0]["buy_submission_gate"], "broker_custom_field_buy_order_active")
        self.assertEqual(rows[0]["broker_custom_field"], broker_custom_field)

    def test_command_buy_loop_chase_uses_first_trade_day_sizing_not_new_daily_preselect(self) -> None:
        root = self._case_dir("buy-loop-chase-first-day-source")
        first_trade_day = date(2026, 4, 20)
        trade_date = date(2026, 4, 21)
        self._write_csv(
            root / first_trade_day.isoformat() / "sizing.csv",
            [
                {
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "source": "A",
                    "basket_tag": "main",
                    "source_weight": 1.0,
                    "target_qty": 3,
                    "estimated_buy_price": 100.0,
                    "projected_cost": 300.0,
                }
            ],
        )
        self._write_csv(
            root / first_trade_day.isoformat() / "positions.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-20:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "holding_qty": 1,
                    "buy_avg_price": 100.0,
                    "buy_total_cost": 100.0,
                    "source": "A",
                    "basket_tag": "main",
                }
            ],
        )
        self._write_csv(
            root / trade_date.isoformat() / "sizing.csv",
            [
                {
                    "stock_id": "2454",
                    "stock_name": "MediaTek",
                    "source": "A",
                    "basket_tag": "main",
                    "source_weight": 1.0,
                    "target_qty": 3,
                    "estimated_buy_price": 800.0,
                    "projected_cost": 2400.0,
                }
            ],
        )
        plan = WeekTradePlan(
            anchor_date=date(2026, 4, 24),
            week_trade_days=[first_trade_day, trade_date, date(2026, 4, 22), date(2026, 4, 24)],
            buy_cutoff_day=date(2026, 4, 22),
            last_trade_day=date(2026, 4, 24),
            calendar_missing_warning=False,
            source_path=None,
        )
        settings = SimpleNamespace(
            fees=FeeConfig(
                commission_rate=0.0,
                commission_discount=1.0,
                minimum_commission=0.0,
                transaction_tax_rate_stock=0.0,
                transaction_tax_rate_day_trade=0.0,
            ),
            auto_trading=AutoTradingConfig(
                weekly_budget=100000,
                overrun_tolerance=0.0,
                allow_buy_after_first_trade_day=True,
                cost_buffer_multiplier=1.0,
            ),
            allow_live_submit=False,
            live_trading_confirmed=lambda confirm_live: False,
            project_root=root,
        )

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 4, 21, 10, 0, tzinfo=tz)

        with patch("sinopac_auto_trading.cli.Settings.load", return_value=settings), patch(
            "sinopac_auto_trading.cli.resolve_week_trade_plan",
            return_value=plan,
        ), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ), patch(
            "sinopac_auto_trading.ledger.resolve_week_trade_plan",
            return_value=plan,
        ), patch(
            "sinopac_auto_trading.ledger.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ), patch(
            "sinopac_auto_trading.cli.datetime",
            _FixedDatetime,
        ):
            result = command_buy_loop(
                SimpleNamespace(
                    trade_date=trade_date.isoformat(),
                    live=False,
                    confirm_live=False,
                    reprice_threshold_ticks=1,
                )
            )

        self.assertEqual(result, 0)
        rows = _read_csv_rows(root / trade_date.isoformat() / "orders.csv")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stock_id"], "2330")
        self.assertEqual(rows[0]["strategy_lot_id"], "auto-2026-04-20:2330")
        self.assertEqual(rows[0]["target_qty"], "3")
        self.assertEqual(rows[0]["filled_qty"], "1")
        self.assertEqual(rows[0]["remaining_qty"], "2")
        state = json.loads((root / trade_date.isoformat() / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["buy_loop"]["buy_source_trade_date"], first_trade_day.isoformat())
        self.assertTrue(state["buy_loop"]["buy_chase_mode"])

    def test_command_buy_loop_chase_only_buys_unfilled_qty_from_first_day_lot(self) -> None:
        root = self._case_dir("buy-loop-chase-remaining-only")
        first_trade_day = date(2026, 4, 20)
        trade_date = date(2026, 4, 21)
        self._write_csv(
            root / first_trade_day.isoformat() / "sizing.csv",
            [
                {
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "source": "A",
                    "basket_tag": "main",
                    "source_weight": 1.0,
                    "target_qty": 3,
                    "estimated_buy_price": 100.0,
                    "projected_cost": 300.0,
                }
            ],
        )
        self._write_csv(
            root / first_trade_day.isoformat() / "fills.csv",
            [
                {
                    "run_id": "auto-2026-04-20",
                    "strategy_lot_id": "auto-2026-04-20:2330",
                    "basket_tag": "main",
                    "stock_id": "2330",
                    "side": "Buy",
                    "fill_price": 100.0,
                    "fill_qty": 1,
                    "fee": 0.0,
                    "tax": 0.0,
                    "fill_time": "2026-04-20T09:31:00+08:00",
                    "broker_fill_id": "FILL-2330-1",
                    "broker_custom_field": "B11111",
                    "fill_assignment_status": "resolved_broker_strategy_lot",
                }
            ],
        )
        plan = WeekTradePlan(
            anchor_date=date(2026, 4, 24),
            week_trade_days=[first_trade_day, trade_date, date(2026, 4, 22), date(2026, 4, 24)],
            buy_cutoff_day=date(2026, 4, 22),
            last_trade_day=date(2026, 4, 24),
            calendar_missing_warning=False,
            source_path=None,
        )
        settings = SimpleNamespace(
            fees=FeeConfig(
                commission_rate=0.0,
                commission_discount=1.0,
                minimum_commission=0.0,
                transaction_tax_rate_stock=0.0,
                transaction_tax_rate_day_trade=0.0,
            ),
            auto_trading=AutoTradingConfig(
                weekly_budget=100000,
                overrun_tolerance=0.0,
                allow_buy_after_first_trade_day=True,
                cost_buffer_multiplier=1.0,
            ),
            allow_live_submit=False,
            live_trading_confirmed=lambda confirm_live: False,
            project_root=root,
        )

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 4, 21, 10, 0, tzinfo=tz)

        with patch("sinopac_auto_trading.cli.Settings.load", return_value=settings), patch(
            "sinopac_auto_trading.cli.resolve_week_trade_plan",
            return_value=plan,
        ), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ), patch(
            "sinopac_auto_trading.ledger.resolve_week_trade_plan",
            return_value=plan,
        ), patch(
            "sinopac_auto_trading.ledger.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ), patch(
            "sinopac_auto_trading.cli.datetime",
            _FixedDatetime,
        ):
            self.assertEqual(_already_bought_qty_by_lot(trade_date)["auto-2026-04-20:2330"], 1)
            result = command_buy_loop(
                SimpleNamespace(
                    trade_date=trade_date.isoformat(),
                    live=False,
                    confirm_live=False,
                    reprice_threshold_ticks=1,
                )
            )

        self.assertEqual(result, 0)
        rows = _read_csv_rows(root / trade_date.isoformat() / "orders.csv")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["strategy_lot_id"], "auto-2026-04-20:2330")
        self.assertEqual(rows[0]["target_qty"], "3")
        self.assertEqual(rows[0]["filled_qty"], "1")
        self.assertEqual(rows[0]["remaining_qty"], "2")
        self.assertEqual(rows[0]["active_order_qty"], "2")

    def test_command_buy_loop_chase_does_not_rebuy_when_first_day_lot_is_filled(self) -> None:
        root = self._case_dir("buy-loop-chase-no-rebuy")
        first_trade_day = date(2026, 4, 20)
        trade_date = date(2026, 4, 21)
        self._write_csv(
            root / first_trade_day.isoformat() / "sizing.csv",
            [
                {
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "source": "A",
                    "basket_tag": "main",
                    "source_weight": 1.0,
                    "target_qty": 3,
                    "estimated_buy_price": 100.0,
                    "projected_cost": 300.0,
                }
            ],
        )
        self._write_csv(
            root / first_trade_day.isoformat() / "positions.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-20:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "holding_qty": 3,
                    "buy_avg_price": 100.0,
                    "buy_total_cost": 300.0,
                    "source": "A",
                    "basket_tag": "main",
                }
            ],
        )
        plan = WeekTradePlan(
            anchor_date=date(2026, 4, 24),
            week_trade_days=[first_trade_day, trade_date, date(2026, 4, 22), date(2026, 4, 24)],
            buy_cutoff_day=date(2026, 4, 22),
            last_trade_day=date(2026, 4, 24),
            calendar_missing_warning=False,
            source_path=None,
        )
        settings = SimpleNamespace(
            fees=FeeConfig(
                commission_rate=0.0,
                commission_discount=1.0,
                minimum_commission=0.0,
                transaction_tax_rate_stock=0.0,
                transaction_tax_rate_day_trade=0.0,
            ),
            auto_trading=AutoTradingConfig(
                weekly_budget=100000,
                overrun_tolerance=0.0,
                allow_buy_after_first_trade_day=True,
                cost_buffer_multiplier=1.0,
            ),
            allow_live_submit=False,
            live_trading_confirmed=lambda confirm_live: False,
            project_root=root,
        )

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 4, 21, 10, 0, tzinfo=tz)

        with patch("sinopac_auto_trading.cli.Settings.load", return_value=settings), patch(
            "sinopac_auto_trading.cli.resolve_week_trade_plan",
            return_value=plan,
        ), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ), patch(
            "sinopac_auto_trading.ledger.resolve_week_trade_plan",
            return_value=plan,
        ), patch(
            "sinopac_auto_trading.ledger.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ), patch(
            "sinopac_auto_trading.cli.datetime",
            _FixedDatetime,
        ):
            result = command_buy_loop(
                SimpleNamespace(
                    trade_date=trade_date.isoformat(),
                    live=False,
                    confirm_live=False,
                    reprice_threshold_ticks=1,
                )
            )

        self.assertEqual(result, 0)
        rows = _read_csv_rows(root / trade_date.isoformat() / "orders.csv")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["strategy_lot_id"], "auto-2026-04-20:2330")
        self.assertEqual(rows[0]["target_qty"], "3")
        self.assertEqual(rows[0]["filled_qty"], "3")
        self.assertEqual(rows[0]["remaining_qty"], "0")
        self.assertEqual(rows[0]["active_order_qty"], "0")
        self.assertEqual(rows[0]["action"], "done")

    def test_command_buy_loop_blocks_new_live_buy_when_ambiguous_fill_exists_for_same_stock(self) -> None:
        root = self._case_dir("buy-loop-ambiguous-fill-guard")
        trade_date = date(2026, 4, 22)
        self._write_csv(
            root / trade_date.isoformat() / "sizing.csv",
            [
                {
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "source": "A",
                    "basket_tag": "main",
                    "source_weight": 1.0,
                    "target_qty": 3,
                    "estimated_buy_price": 100.0,
                    "projected_cost": 300.0,
                }
            ],
        )
        self._write_csv(
            root / date(2026, 4, 21).isoformat() / "positions.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-21:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "holding_qty": 1,
                    "buy_avg_price": 95.0,
                    "buy_total_cost": 95.0,
                    "source": "A",
                    "basket_tag": "main",
                },
                {
                    "strategy_lot_id": "auto-2026-04-21:secondary_add:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "holding_qty": 1,
                    "buy_avg_price": 96.0,
                    "buy_total_cost": 96.0,
                    "source": "A",
                    "basket_tag": "secondary_add",
                },
            ],
        )
        plan = WeekTradePlan(
            anchor_date=date(2026, 4, 24),
            week_trade_days=[date(2026, 4, 21), trade_date, date(2026, 4, 24)],
            buy_cutoff_day=trade_date,
            last_trade_day=date(2026, 4, 24),
            calendar_missing_warning=False,
            source_path=None,
        )
        settings = SimpleNamespace(
            fees=FeeConfig(
                commission_rate=0.0,
                commission_discount=1.0,
                minimum_commission=0.0,
                transaction_tax_rate_stock=0.0,
                transaction_tax_rate_day_trade=0.0,
            ),
            auto_trading=SimpleNamespace(
                hard_budget=500000.0,
                quote_stale_seconds=3600,
                cost_buffer_multiplier=1.0,
                live_enabled=True,
                enable_secondary_add=True,
            ),
            allow_live_submit=True,
            live_trading_confirmed=lambda confirm_live: True,
            project_root=Path.cwd(),
        )
        broker_instances: list[BuyLoopHelperTests._BuyLoopBroker] = []

        class _PatchedBuyLoopBroker(self._BuyLoopBroker):
            def __init__(self, *args, **kwargs) -> None:
                kwargs.setdefault(
                    "fills",
                    [
                        {
                            "stock_id": "2330",
                            "side": "Buy",
                            "fill_price": 100.0,
                            "fill_qty": 1,
                            "fill_time": "2026-04-22T12:46:00+08:00",
                            "order_id": "",
                            "broker_custom_field": "",
                        }
                    ],
                )
                super().__init__(*args, **kwargs)
                broker_instances.append(self)

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 4, 22, 12, 50, tzinfo=tz)

        with patch("sinopac_auto_trading.cli.Settings.load", return_value=settings), patch(
            "sinopac_auto_trading.cli.resolve_week_trade_plan",
            return_value=plan,
        ), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ), patch(
            "sinopac_auto_trading.ledger.resolve_week_trade_plan",
            return_value=plan,
        ), patch(
            "sinopac_auto_trading.ledger.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ), patch(
            "sinopac_auto_trading.cli.ShioajiSinoPacBrokerAdapter",
            _PatchedBuyLoopBroker,
        ), patch(
            "sinopac_auto_trading.cli.datetime",
            _FixedDatetime,
        ):
            result = command_buy_loop(
                SimpleNamespace(
                    trade_date=trade_date.isoformat(),
                    live=True,
                    confirm_live=True,
                    reprice_threshold_ticks=1,
                )
            )

        self.assertEqual(result, 0)
        self.assertEqual(len(broker_instances), 1)
        self.assertEqual(len(broker_instances[0].place_buy_calls), 0)
        rows = _read_csv_rows(root / trade_date.isoformat() / "orders.csv")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "skip")
        self.assertEqual(rows[0]["status"], "blocked_ambiguous_fill")
        self.assertEqual(rows[0]["buy_submission_gate"], "ambiguous_fill_guard")
        fills_rows = _read_csv_rows(root / trade_date.isoformat() / "fills.csv")
        self.assertEqual(len(fills_rows), 1)
        self.assertEqual(fills_rows[0]["fill_assignment_status"], "ambiguous_unmapped_fill")

    def test_command_buy_loop_blocks_new_live_buy_when_broker_qty_is_below_strategy_scope(self) -> None:
        root = self._case_dir("buy-loop-broker-underheld-guard")
        trade_date = date(2026, 4, 22)
        self._write_csv(
            root / trade_date.isoformat() / "sizing.csv",
            [
                {
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "source": "A",
                    "basket_tag": "main",
                    "source_weight": 1.0,
                    "target_qty": 3,
                    "estimated_buy_price": 100.0,
                    "projected_cost": 300.0,
                }
            ],
        )
        self._write_csv(
            root / date(2026, 4, 21).isoformat() / "positions.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-21:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "holding_qty": 2,
                    "buy_avg_price": 95.0,
                    "buy_total_cost": 190.0,
                    "source": "A",
                    "basket_tag": "main",
                }
            ],
        )
        plan = WeekTradePlan(
            anchor_date=date(2026, 4, 24),
            week_trade_days=[date(2026, 4, 21), trade_date, date(2026, 4, 24)],
            buy_cutoff_day=trade_date,
            last_trade_day=date(2026, 4, 24),
            calendar_missing_warning=False,
            source_path=None,
        )
        settings = SimpleNamespace(
            fees=FeeConfig(
                commission_rate=0.0,
                commission_discount=1.0,
                minimum_commission=0.0,
                transaction_tax_rate_stock=0.0,
                transaction_tax_rate_day_trade=0.0,
            ),
            auto_trading=SimpleNamespace(
                hard_budget=500000.0,
                quote_stale_seconds=3600,
                cost_buffer_multiplier=1.0,
                live_enabled=True,
                enable_secondary_add=True,
            ),
            allow_live_submit=True,
            live_trading_confirmed=lambda confirm_live: True,
            project_root=Path.cwd(),
        )
        broker_instances: list[BuyLoopHelperTests._BuyLoopBroker] = []

        class _PatchedBuyLoopBroker(self._BuyLoopBroker):
            def __init__(self, *args, **kwargs) -> None:
                kwargs.setdefault(
                    "positions",
                    [PositionSnapshot(stock_id="2330", stock_name="TSMC", quantity=1, avg_price=95.0)],
                )
                kwargs.setdefault("fills", [])
                super().__init__(*args, **kwargs)
                broker_instances.append(self)

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 4, 22, 12, 50, tzinfo=tz)

        with patch("sinopac_auto_trading.cli.Settings.load", return_value=settings), patch(
            "sinopac_auto_trading.cli.resolve_week_trade_plan",
            return_value=plan,
        ), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ), patch(
            "sinopac_auto_trading.ledger.resolve_week_trade_plan",
            return_value=plan,
        ), patch(
            "sinopac_auto_trading.ledger.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ), patch(
            "sinopac_auto_trading.cli.ShioajiSinoPacBrokerAdapter",
            _PatchedBuyLoopBroker,
        ), patch(
            "sinopac_auto_trading.cli.datetime",
            _FixedDatetime,
        ):
            result = command_buy_loop(
                SimpleNamespace(
                    trade_date=trade_date.isoformat(),
                    live=True,
                    confirm_live=True,
                    reprice_threshold_ticks=1,
                )
            )

        self.assertEqual(result, 0)
        self.assertEqual(len(broker_instances), 1)
        self.assertEqual(len(broker_instances[0].place_buy_calls), 0)
        rows = _read_csv_rows(root / trade_date.isoformat() / "orders.csv")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "skip")
        self.assertEqual(rows[0]["status"], "blocked_broker_qty_mismatch")
        self.assertEqual(rows[0]["buy_submission_gate"], "broker_qty_below_strategy_guard")
        mismatch_rows = _read_csv_rows(root / trade_date.isoformat() / "broker_position_mismatches.csv")
        self.assertEqual(mismatch_rows, [
            {
                "stock_id": "2330",
                "stock_name": "TSMC",
                "broker_qty": "1",
                "strategy_qty": "2",
                "missing_qty": "1",
                "reason": "broker_qty_below_strategy_qty",
            }
        ])
        state = json.loads((root / trade_date.isoformat() / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["buy_loop"]["broker_underheld_guard_count"], 1)
        self.assertEqual(state["buy_loop"]["broker_underheld_guard_stocks"], ["2330"])

    def test_command_sell_loop_keeps_existing_active_sell_order_per_lot(self) -> None:
        root = self._case_dir("sell-loop-existing-order")
        trade_date = date(2026, 4, 24)
        self._write_csv(
            root / trade_date.isoformat() / "positions.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-21:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "holding_qty": 3,
                    "buy_avg_price": 100.0,
                    "buy_total_cost": 300.0,
                    "source": "A",
                    "basket_tag": "main",
                }
            ],
        )
        self._write_csv(
            root / trade_date.isoformat() / "sell_decisions.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-21:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "holding_qty": 3,
                    "buy_avg_price": 100.0,
                    "buy_total_cost": 300.0,
                    "source": "A",
                    "last_price": 120.0,
                    "bid1": 120.0,
                    "ask1": 120.5,
                    "can_sell_flag": True,
                    "sell_decision": "sell",
                    "sell_decision_reason": "passed conservative threshold",
                    "conservative_sell_price": 119.0,
                    "conservative_profit": 57.0,
                    "estimated_sell_net_proceeds": 357.0,
                    "basket_tag": "main",
                    "basket_recommendation": "recommend_exit",
                    "basket_threshold": 0.0,
                    "basket_loser_loss_ratio": 0.0,
                    "quote_timestamp": "2026-04-24T13:00:00+08:00",
                    "sell_submission_gate": "submitted_live",
                    "sell_order_price": 119.0,
                    "sell_order_id": "SELL-2330-1",
                    "sell_order_status": "Submitted",
                    "sold_qty": 0,
                    "remaining_qty": 3,
                    "allocated_buy_cost": "",
                    "realized_pnl": "",
                }
            ],
        )
        broker = self._SellLoopBroker(
            managed_orders={
                "SELL-2330-1": ManagedOrderSnapshot(
                    order_id="SELL-2330-1",
                    stock_id="2330",
                    order_price=119.0,
                    order_qty=3,
                    filled_qty=0,
                    remaining_qty=3,
                    status="active",
                )
            },
            positions=[PositionSnapshot(stock_id="2330", stock_name="TSMC", quantity=3, avg_price=100.0)],
        )
        plan = WeekTradePlan(
            anchor_date=trade_date,
            week_trade_days=[date(2026, 4, 21), date(2026, 4, 22), trade_date],
            buy_cutoff_day=date(2026, 4, 22),
            last_trade_day=trade_date,
            calendar_missing_warning=False,
            source_path=None,
        )
        settings = SimpleNamespace(
            fees=FeeConfig(
                commission_rate=0.0,
                commission_discount=1.0,
                minimum_commission=0.0,
                transaction_tax_rate_stock=0.0,
                transaction_tax_rate_day_trade=0.0,
            ),
            auto_trading=SimpleNamespace(
                per_stock_profit_buffer_pct=0.0,
                per_stock_profit_buffer_min_twd=0.0,
                basket_profit_buffer_pct=0.0,
                basket_profit_buffer_min_twd=0.0,
                max_loser_loss_ratio_to_winner_profit=1.0,
                basket_recommendation_enabled=True,
                basket_auto_exit_enabled=True,
                quote_stale_seconds=3600,
                live_enabled=True,
            ),
            allow_live_submit=True,
            live_trading_confirmed=lambda confirm_live: True,
        )

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 4, 24, 13, 10, tzinfo=tz)

        with patch("sinopac_auto_trading.cli.Settings.load", return_value=settings), patch(
            "sinopac_auto_trading.cli.resolve_week_trade_plan",
            return_value=plan,
        ), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ), patch(
            "sinopac_auto_trading.ledger.resolve_week_trade_plan",
            return_value=plan,
        ), patch(
            "sinopac_auto_trading.ledger.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ), patch(
            "sinopac_auto_trading.cli.ShioajiSinoPacBrokerAdapter",
            return_value=broker,
        ), patch(
            "sinopac_auto_trading.cli.datetime",
            _FixedDatetime,
        ):
            result = command_sell_loop(SimpleNamespace(trade_date=trade_date.isoformat(), live=True, confirm_live=True))

        self.assertEqual(result, 0)
        self.assertEqual(len(broker.place_sell_calls), 0)
        rows = _read_csv_rows(root / trade_date.isoformat() / "sell_decisions.csv")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sell_order_id"], "SELL-2330-1")
        self.assertEqual(rows[0]["sell_order_status"], "active")
        self.assertEqual(rows[0]["sell_submission_gate"], "existing_sell_order_active")

    def test_command_sell_loop_prepare_only_can_run_before_last_trade_day_with_read_only_live_checks(self) -> None:
        root = self._case_dir("sell-loop-prepare-only")
        trade_date = date(2026, 4, 22)
        last_trade_day = date(2026, 4, 24)
        self._write_csv(
            root / trade_date.isoformat() / "positions.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-22:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "holding_qty": 3,
                    "buy_avg_price": 100.0,
                    "buy_total_cost": 300.0,
                    "source": "A",
                    "basket_tag": "main",
                }
            ],
        )

        class _OpeningBroker(self._SellLoopBroker):
            def get_quote_state(self, stock_id: str):
                return (
                    QuoteState(last_price=120.0, bid1=120.0, ask1=120.5),
                    "TSMC",
                    "TSE",
                    "2026-04-22T09:05:00+08:00",
                )

            def place_sell_order(self, stock_id: str, price: float, qty: int, order_lot: str, metadata: dict[str, object]):
                self.place_sell_calls.append((stock_id, price, qty, order_lot, metadata))
                raise AssertionError("prepare-only sell_loop must never submit live sell orders")

        broker = _OpeningBroker(
            positions=[PositionSnapshot(stock_id="2330", stock_name="TSMC", quantity=3, avg_price=100.0)],
            fills=[],
        )
        plan = WeekTradePlan(
            anchor_date=trade_date,
            week_trade_days=[trade_date, last_trade_day],
            buy_cutoff_day=trade_date,
            last_trade_day=last_trade_day,
            calendar_missing_warning=False,
            source_path=None,
        )
        settings = SimpleNamespace(
            fees=FeeConfig(
                commission_rate=0.0,
                commission_discount=1.0,
                minimum_commission=0.0,
                transaction_tax_rate_stock=0.0,
                transaction_tax_rate_day_trade=0.0,
            ),
            auto_trading=SimpleNamespace(
                per_stock_profit_buffer_pct=0.0,
                per_stock_profit_buffer_min_twd=0.0,
                basket_profit_buffer_pct=0.0,
                basket_profit_buffer_min_twd=0.0,
                max_loser_loss_ratio_to_winner_profit=1.0,
                basket_recommendation_enabled=True,
                basket_auto_exit_enabled=True,
                quote_stale_seconds=3600,
                live_enabled=True,
            ),
            allow_live_submit=False,
            live_trading_confirmed=lambda confirm_live: True,
        )

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 4, 22, 9, 5, tzinfo=tz)

        with patch("sinopac_auto_trading.cli.Settings.load", return_value=settings), patch(
            "sinopac_auto_trading.cli.resolve_week_trade_plan",
            return_value=plan,
        ), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ), patch(
            "sinopac_auto_trading.ledger.resolve_week_trade_plan",
            return_value=plan,
        ), patch(
            "sinopac_auto_trading.ledger.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ), patch(
            "sinopac_auto_trading.cli.ShioajiSinoPacBrokerAdapter",
            return_value=broker,
        ), patch(
            "sinopac_auto_trading.cli.datetime",
            _FixedDatetime,
        ):
            result = command_sell_loop(
                SimpleNamespace(
                    trade_date=trade_date.isoformat(),
                    live=True,
                    confirm_live=True,
                    prepare_only=True,
                )
            )

        self.assertEqual(result, 0)
        self.assertEqual(len(broker.place_sell_calls), 0)
        rows = _read_csv_rows(root / trade_date.isoformat() / "sell_decisions.csv")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sell_decision"], "sell")
        self.assertEqual(rows[0]["sell_order_status"], "ready_to_submit")
        self.assertEqual(rows[0]["sell_submission_gate"], "basket_recommendation_passed")
        state = json.loads((root / trade_date.isoformat() / "state.json").read_text(encoding="utf-8"))
        self.assertTrue(state["sell_loop"]["prepare_only"])
        self.assertTrue(state["sell_loop"]["read_only_live"])
        self.assertEqual(state["sell_loop"]["live_guard"], "sell_prepare_only_read_only_live")
        self.assertEqual(state["sell_loop"]["live_actions"], 0)

    def test_command_sell_loop_submits_whole_basket_when_basket_profit_passes_threshold(self) -> None:
        root = self._case_dir("sell-loop-whole-basket-exit")
        trade_date = date(2026, 4, 24)
        self._write_csv(
            root / trade_date.isoformat() / "positions.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-22:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "holding_qty": 10,
                    "buy_avg_price": 100.0,
                    "buy_total_cost": 1000.0,
                    "source": "A",
                    "basket_tag": "main",
                },
                {
                    "strategy_lot_id": "auto-2026-04-22:2454",
                    "stock_id": "2454",
                    "stock_name": "MediaTek",
                    "holding_qty": 10,
                    "buy_avg_price": 100.0,
                    "buy_total_cost": 1000.0,
                    "source": "A",
                    "basket_tag": "main",
                },
            ],
        )

        class _BasketExitBroker(self._SellLoopBroker):
            def get_quote_state(self, stock_id: str):
                if stock_id == "2454":
                    return (
                        QuoteState(last_price=95.0, bid1=95.0, ask1=95.5),
                        "MediaTek",
                        "TSE",
                        "2026-04-24T13:05:00+08:00",
                    )
                return (
                    QuoteState(last_price=130.0, bid1=130.0, ask1=130.5),
                    "TSMC",
                    "TSE",
                    "2026-04-24T13:05:00+08:00",
                )

            def place_sell_order(self, stock_id: str, price: float, qty: int, order_lot: str, metadata: dict[str, object]):
                self.place_sell_calls.append((stock_id, price, qty, order_lot, metadata))
                return BrokerOrderResult(
                    stock_id=stock_id,
                    side="Sell",
                    order_price=price,
                    order_qty=qty,
                    order_lot=order_lot,
                    status="submitted",
                    order_id=f"SELL-{stock_id}",
                    raw=metadata,
                )

        broker = _BasketExitBroker(
            positions=[
                PositionSnapshot(stock_id="2330", stock_name="TSMC", quantity=10, avg_price=100.0),
                PositionSnapshot(stock_id="2454", stock_name="MediaTek", quantity=10, avg_price=100.0),
            ],
            fills=[],
        )
        plan = WeekTradePlan(
            anchor_date=trade_date,
            week_trade_days=[date(2026, 4, 22), trade_date],
            buy_cutoff_day=date(2026, 4, 22),
            last_trade_day=trade_date,
            calendar_missing_warning=False,
            source_path=None,
        )
        settings = SimpleNamespace(
            fees=FeeConfig(
                commission_rate=0.0,
                commission_discount=1.0,
                minimum_commission=0.0,
                transaction_tax_rate_stock=0.0,
                transaction_tax_rate_day_trade=0.0,
            ),
            auto_trading=SimpleNamespace(
                per_stock_profit_buffer_pct=0.006,
                per_stock_profit_buffer_min_twd=100.0,
                basket_profit_buffer_pct=0.008,
                basket_profit_buffer_min_twd=0.0,
                max_loser_loss_ratio_to_winner_profit=0.35,
                basket_recommendation_enabled=True,
                basket_auto_exit_enabled=True,
                quote_stale_seconds=3600,
                live_enabled=True,
            ),
            allow_live_submit=True,
            live_trading_confirmed=lambda confirm_live: True,
        )

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 4, 24, 13, 10, tzinfo=tz)

        with patch("sinopac_auto_trading.cli.Settings.load", return_value=settings), patch(
            "sinopac_auto_trading.cli.resolve_week_trade_plan",
            return_value=plan,
        ), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ), patch(
            "sinopac_auto_trading.ledger.resolve_week_trade_plan",
            return_value=plan,
        ), patch(
            "sinopac_auto_trading.ledger.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ), patch(
            "sinopac_auto_trading.cli.ShioajiSinoPacBrokerAdapter",
            return_value=broker,
        ), patch(
            "sinopac_auto_trading.cli.datetime",
            _FixedDatetime,
        ):
            result = command_sell_loop(SimpleNamespace(trade_date=trade_date.isoformat(), live=True, confirm_live=True))

        self.assertEqual(result, 0)
        self.assertEqual([call[0] for call in broker.place_sell_calls], ["2330", "2454"])
        rows = _read_csv_rows(root / trade_date.isoformat() / "sell_decisions.csv")
        rows_by_stock = {row["stock_id"]: row for row in rows}
        self.assertEqual(rows_by_stock["2330"]["sell_decision"], "sell")
        self.assertEqual(rows_by_stock["2454"]["sell_decision"], "sell")
        self.assertEqual(rows_by_stock["2454"]["can_sell_flag"], "False")
        self.assertIn("basket_exit_sells_all", rows_by_stock["2454"]["sell_decision_reason"])
        self.assertEqual(rows_by_stock["2454"]["sell_submission_gate"], "submitted_live")

    def test_command_sell_loop_uses_local_partial_sell_state_when_live_fills_are_missing(self) -> None:
        root = self._case_dir("sell-loop-local-fallback")
        trade_date = date(2026, 4, 24)
        self._write_csv(
            root / trade_date.isoformat() / "positions.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-21:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "holding_qty": 3,
                    "buy_avg_price": 100.0,
                    "buy_total_cost": 300.0,
                    "source": "A",
                    "basket_tag": "main",
                }
            ],
        )
        self._write_csv(
            root / trade_date.isoformat() / "sell_decisions.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-21:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "holding_qty": 3,
                    "buy_avg_price": 100.0,
                    "buy_total_cost": 300.0,
                    "source": "A",
                    "last_price": 120.0,
                    "bid1": 120.0,
                    "ask1": 120.5,
                    "can_sell_flag": True,
                    "sell_decision": "sell",
                    "sell_decision_reason": "passed conservative threshold",
                    "conservative_sell_price": 119.0,
                    "conservative_profit": 57.0,
                    "estimated_sell_net_proceeds": 357.0,
                    "basket_tag": "main",
                    "basket_recommendation": "recommend_exit",
                    "basket_threshold": 0.0,
                    "basket_loser_loss_ratio": 0.0,
                    "quote_timestamp": "2026-04-24T13:00:00+08:00",
                    "sell_submission_gate": "submitted_live",
                    "sell_order_price": 119.0,
                    "sell_order_id": "SELL-2330-2",
                    "sell_order_status": "Submitted",
                    "sold_qty": 0,
                    "remaining_qty": 3,
                    "allocated_buy_cost": "",
                    "realized_pnl": "",
                }
            ],
        )
        broker = self._SellLoopBroker(
            managed_orders={
                "SELL-2330-2": ManagedOrderSnapshot(
                    order_id="SELL-2330-2",
                    stock_id="2330",
                    order_price=119.0,
                    order_qty=3,
                    filled_qty=1,
                    remaining_qty=2,
                    status="active",
                )
            },
            positions=[PositionSnapshot(stock_id="2330", stock_name="TSMC", quantity=2, avg_price=100.0)],
            fills=[],
        )
        plan = WeekTradePlan(
            anchor_date=trade_date,
            week_trade_days=[date(2026, 4, 21), date(2026, 4, 22), trade_date],
            buy_cutoff_day=date(2026, 4, 22),
            last_trade_day=trade_date,
            calendar_missing_warning=False,
            source_path=None,
        )
        settings = SimpleNamespace(
            fees=FeeConfig(
                commission_rate=0.0,
                commission_discount=1.0,
                minimum_commission=0.0,
                transaction_tax_rate_stock=0.0,
                transaction_tax_rate_day_trade=0.0,
            ),
            auto_trading=SimpleNamespace(
                per_stock_profit_buffer_pct=0.0,
                per_stock_profit_buffer_min_twd=0.0,
                basket_profit_buffer_pct=0.0,
                basket_profit_buffer_min_twd=0.0,
                max_loser_loss_ratio_to_winner_profit=1.0,
                basket_recommendation_enabled=True,
                basket_auto_exit_enabled=True,
                quote_stale_seconds=3600,
                live_enabled=True,
            ),
            allow_live_submit=True,
            live_trading_confirmed=lambda confirm_live: True,
        )

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 4, 24, 13, 10, tzinfo=tz)

        with patch("sinopac_auto_trading.cli.Settings.load", return_value=settings), patch(
            "sinopac_auto_trading.cli.resolve_week_trade_plan",
            return_value=plan,
        ), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ), patch(
            "sinopac_auto_trading.ledger.resolve_week_trade_plan",
            return_value=plan,
        ), patch(
            "sinopac_auto_trading.ledger.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ), patch(
            "sinopac_auto_trading.cli.ShioajiSinoPacBrokerAdapter",
            return_value=broker,
        ), patch(
            "sinopac_auto_trading.cli.datetime",
            _FixedDatetime,
        ):
            result = command_sell_loop(SimpleNamespace(trade_date=trade_date.isoformat(), live=True, confirm_live=True))

        self.assertEqual(result, 0)
        self.assertEqual(len(broker.place_sell_calls), 0)
        sell_rows = _read_csv_rows(root / trade_date.isoformat() / "sell_decisions.csv")
        self.assertEqual(len(sell_rows), 1)
        self.assertEqual(sell_rows[0]["sell_order_id"], "SELL-2330-2")
        self.assertEqual(sell_rows[0]["sold_qty"], "1")
        self.assertEqual(sell_rows[0]["remaining_qty"], "2")
        self.assertEqual(sell_rows[0]["actual_fill_avg_price"], "119.0")
        self.assertEqual(sell_rows[0]["allocated_buy_cost"], "100.0")
        self.assertEqual(sell_rows[0]["realized_pnl"], "19.0")
        self.assertEqual(sell_rows[0]["sell_pnl_source"], "local_sell_order_fallback")
        positions_rows = _read_csv_rows(root / trade_date.isoformat() / "positions.csv")
        self.assertEqual(len(positions_rows), 1)
        self.assertEqual(positions_rows[0]["strategy_lot_id"], "auto-2026-04-21:2330")
        self.assertEqual(positions_rows[0]["holding_qty"], "2")
        self.assertEqual(positions_rows[0]["status"], "local_sell_fill_fallback")

    def test_command_sell_loop_blocks_new_live_sell_when_ambiguous_fill_exists_for_same_stock(self) -> None:
        root = self._case_dir("sell-loop-ambiguous-fill-guard")
        trade_date = date(2026, 4, 24)
        self._write_csv(
            root / trade_date.isoformat() / "positions.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-22:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "holding_qty": 3,
                    "buy_avg_price": 100.0,
                    "buy_total_cost": 300.0,
                    "source": "A",
                    "basket_tag": "main",
                },
                {
                    "strategy_lot_id": "auto-2026-04-22:secondary_add:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "holding_qty": 2,
                    "buy_avg_price": 99.0,
                    "buy_total_cost": 198.0,
                    "source": "A",
                    "basket_tag": "secondary_add",
                },
            ],
        )
        broker = self._SellLoopBroker(
            positions=[PositionSnapshot(stock_id="2330", stock_name="TSMC", quantity=5, avg_price=99.5)],
            fills=[
                {
                    "stock_id": "2330",
                    "side": "Sell",
                    "fill_price": 119.0,
                    "fill_qty": 1,
                    "fill_time": "2026-04-24T13:05:00+08:00",
                    "order_id": "",
                    "broker_custom_field": "",
                }
            ],
        )
        plan = WeekTradePlan(
            anchor_date=trade_date,
            week_trade_days=[date(2026, 4, 22), trade_date],
            buy_cutoff_day=date(2026, 4, 22),
            last_trade_day=trade_date,
            calendar_missing_warning=False,
            source_path=None,
        )
        settings = SimpleNamespace(
            fees=FeeConfig(
                commission_rate=0.0,
                commission_discount=1.0,
                minimum_commission=0.0,
                transaction_tax_rate_stock=0.0,
                transaction_tax_rate_day_trade=0.0,
            ),
            auto_trading=SimpleNamespace(
                per_stock_profit_buffer_pct=0.0,
                per_stock_profit_buffer_min_twd=0.0,
                basket_profit_buffer_pct=0.0,
                basket_profit_buffer_min_twd=0.0,
                max_loser_loss_ratio_to_winner_profit=1.0,
                basket_recommendation_enabled=True,
                basket_auto_exit_enabled=True,
                quote_stale_seconds=3600,
                live_enabled=True,
            ),
            allow_live_submit=True,
            live_trading_confirmed=lambda confirm_live: True,
        )

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 4, 24, 13, 10, tzinfo=tz)

        with patch("sinopac_auto_trading.cli.Settings.load", return_value=settings), patch(
            "sinopac_auto_trading.cli.resolve_week_trade_plan",
            return_value=plan,
        ), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ), patch(
            "sinopac_auto_trading.ledger.resolve_week_trade_plan",
            return_value=plan,
        ), patch(
            "sinopac_auto_trading.ledger.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ), patch(
            "sinopac_auto_trading.cli.ShioajiSinoPacBrokerAdapter",
            return_value=broker,
        ), patch(
            "sinopac_auto_trading.cli.datetime",
            _FixedDatetime,
        ):
            result = command_sell_loop(SimpleNamespace(trade_date=trade_date.isoformat(), live=True, confirm_live=True))

        self.assertEqual(result, 0)
        self.assertEqual(len(broker.place_sell_calls), 0)
        sell_rows = _read_csv_rows(root / trade_date.isoformat() / "sell_decisions.csv")
        self.assertEqual(len(sell_rows), 2)
        for row in sell_rows:
            self.assertEqual(row["sell_order_status"], "blocked_ambiguous_fill")
            self.assertEqual(row["sell_submission_gate"], "ambiguous_fill_guard")
        state = json.loads((root / trade_date.isoformat() / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["sell_loop"]["ambiguous_fill_guard_count"], 2)
        fills_rows = _read_csv_rows(root / trade_date.isoformat() / "fills.csv")
        self.assertEqual(len(fills_rows), 1)
        self.assertEqual(fills_rows[0]["fill_assignment_status"], "ambiguous_unmapped_fill")

    def test_command_sell_loop_blocks_new_live_sell_when_broker_qty_is_below_strategy_scope(self) -> None:
        root = self._case_dir("sell-loop-broker-underheld-guard")
        trade_date = date(2026, 4, 24)
        self._write_csv(
            root / trade_date.isoformat() / "positions.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-22:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "holding_qty": 3,
                    "buy_avg_price": 100.0,
                    "buy_total_cost": 300.0,
                    "source": "A",
                    "basket_tag": "main",
                },
                {
                    "strategy_lot_id": "auto-2026-04-22:secondary_add:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "holding_qty": 2,
                    "buy_avg_price": 99.0,
                    "buy_total_cost": 198.0,
                    "source": "A",
                    "basket_tag": "secondary_add",
                },
            ],
        )
        broker = self._SellLoopBroker(
            positions=[PositionSnapshot(stock_id="2330", stock_name="TSMC", quantity=4, avg_price=99.5)],
            fills=[],
        )
        plan = WeekTradePlan(
            anchor_date=trade_date,
            week_trade_days=[date(2026, 4, 22), trade_date],
            buy_cutoff_day=date(2026, 4, 22),
            last_trade_day=trade_date,
            calendar_missing_warning=False,
            source_path=None,
        )
        settings = SimpleNamespace(
            fees=FeeConfig(
                commission_rate=0.0,
                commission_discount=1.0,
                minimum_commission=0.0,
                transaction_tax_rate_stock=0.0,
                transaction_tax_rate_day_trade=0.0,
            ),
            auto_trading=SimpleNamespace(
                per_stock_profit_buffer_pct=0.0,
                per_stock_profit_buffer_min_twd=0.0,
                basket_profit_buffer_pct=0.0,
                basket_profit_buffer_min_twd=0.0,
                max_loser_loss_ratio_to_winner_profit=1.0,
                basket_recommendation_enabled=True,
                basket_auto_exit_enabled=True,
                quote_stale_seconds=3600,
                live_enabled=True,
            ),
            allow_live_submit=True,
            live_trading_confirmed=lambda confirm_live: True,
        )

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 4, 24, 13, 10, tzinfo=tz)

        with patch("sinopac_auto_trading.cli.Settings.load", return_value=settings), patch(
            "sinopac_auto_trading.cli.resolve_week_trade_plan",
            return_value=plan,
        ), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ), patch(
            "sinopac_auto_trading.ledger.resolve_week_trade_plan",
            return_value=plan,
        ), patch(
            "sinopac_auto_trading.ledger.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ), patch(
            "sinopac_auto_trading.cli.ShioajiSinoPacBrokerAdapter",
            return_value=broker,
        ), patch(
            "sinopac_auto_trading.cli.datetime",
            _FixedDatetime,
        ):
            result = command_sell_loop(SimpleNamespace(trade_date=trade_date.isoformat(), live=True, confirm_live=True))

        self.assertEqual(result, 0)
        self.assertEqual(len(broker.place_sell_calls), 0)
        sell_rows = _read_csv_rows(root / trade_date.isoformat() / "sell_decisions.csv")
        self.assertEqual(len(sell_rows), 2)
        for row in sell_rows:
            self.assertEqual(row["sell_order_status"], "blocked_broker_qty_mismatch")
            self.assertEqual(row["sell_submission_gate"], "broker_qty_below_strategy_guard")
        state = json.loads((root / trade_date.isoformat() / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["sell_loop"]["broker_underheld_guard_count"], 2)
        self.assertEqual(state["sell_loop"]["broker_underheld_guard_stocks"], ["2330"])
        mismatch_rows = _read_csv_rows(root / trade_date.isoformat() / "broker_position_mismatches.csv")
        self.assertEqual(mismatch_rows, [
            {
                "stock_id": "2330",
                "stock_name": "TSMC",
                "broker_qty": "4",
                "strategy_qty": "5",
                "missing_qty": "1",
                "reason": "broker_qty_below_strategy_qty",
            }
        ])

    def test_command_sell_loop_blocks_new_live_sell_when_broker_has_excluded_scope_for_same_stock(self) -> None:
        root = self._case_dir("sell-loop-excluded-position-guard")
        trade_date = date(2026, 4, 24)
        self._write_csv(
            root / trade_date.isoformat() / "positions.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-22:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "holding_qty": 2,
                    "buy_avg_price": 100.0,
                    "buy_total_cost": 200.0,
                    "source": "A",
                    "basket_tag": "main",
                },
                {
                    "strategy_lot_id": "auto-2026-04-22:secondary_add:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "holding_qty": 1,
                    "buy_avg_price": 99.0,
                    "buy_total_cost": 99.0,
                    "source": "A",
                    "basket_tag": "secondary_add",
                },
            ],
        )
        broker = self._SellLoopBroker(
            positions=[PositionSnapshot(stock_id="2330", stock_name="TSMC", quantity=5, avg_price=99.5)],
            fills=[],
        )
        plan = WeekTradePlan(
            anchor_date=trade_date,
            week_trade_days=[date(2026, 4, 22), trade_date],
            buy_cutoff_day=date(2026, 4, 22),
            last_trade_day=trade_date,
            calendar_missing_warning=False,
            source_path=None,
        )
        settings = SimpleNamespace(
            fees=FeeConfig(
                commission_rate=0.0,
                commission_discount=1.0,
                minimum_commission=0.0,
                transaction_tax_rate_stock=0.0,
                transaction_tax_rate_day_trade=0.0,
            ),
            auto_trading=SimpleNamespace(
                per_stock_profit_buffer_pct=0.0,
                per_stock_profit_buffer_min_twd=0.0,
                basket_profit_buffer_pct=0.0,
                basket_profit_buffer_min_twd=0.0,
                max_loser_loss_ratio_to_winner_profit=1.0,
                basket_recommendation_enabled=True,
                basket_auto_exit_enabled=True,
                quote_stale_seconds=3600,
                live_enabled=True,
            ),
            allow_live_submit=True,
            live_trading_confirmed=lambda confirm_live: True,
        )

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 4, 24, 13, 10, tzinfo=tz)

        with patch("sinopac_auto_trading.cli.Settings.load", return_value=settings), patch(
            "sinopac_auto_trading.cli.resolve_week_trade_plan",
            return_value=plan,
        ), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ), patch(
            "sinopac_auto_trading.ledger.resolve_week_trade_plan",
            return_value=plan,
        ), patch(
            "sinopac_auto_trading.ledger.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ), patch(
            "sinopac_auto_trading.cli.ShioajiSinoPacBrokerAdapter",
            return_value=broker,
        ), patch(
            "sinopac_auto_trading.cli.datetime",
            _FixedDatetime,
        ):
            result = command_sell_loop(SimpleNamespace(trade_date=trade_date.isoformat(), live=True, confirm_live=True))

        self.assertEqual(result, 0)
        self.assertEqual(len(broker.place_sell_calls), 0)
        sell_rows = _read_csv_rows(root / trade_date.isoformat() / "sell_decisions.csv")
        self.assertEqual(len(sell_rows), 2)
        for row in sell_rows:
            self.assertEqual(row["sell_order_status"], "blocked_excluded_position_scope")
            self.assertEqual(row["sell_submission_gate"], "excluded_position_guard")
        state = json.loads((root / trade_date.isoformat() / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["sell_loop"]["excluded_position_guard_count"], 2)
        excluded_rows = _read_csv_rows(root / trade_date.isoformat() / "excluded_positions.csv")
        self.assertEqual(excluded_rows, [
            {
                "stock_id": "2330",
                "stock_name": "TSMC",
                "broker_qty": "5",
                "strategy_qty": "3",
                "excluded_qty": "2",
                "reason": "broker_qty_exceeds_strategy_qty",
            }
        ])

    def test_ambiguous_fill_rows_and_daily_report_warning_surface_unmapped_multi_lot_fills(self) -> None:
        root = self._case_dir("ambiguous-fill-warning")
        trade_date = date(2026, 4, 24)
        self._write_csv(
            root / trade_date.isoformat() / "fills.csv",
            [
                {
                    "strategy_lot_id": "",
                    "basket_tag": "",
                    "stock_id": "2330",
                    "side": "Buy",
                    "fill_price": 100.0,
                    "fill_qty": 1,
                    "broker_fill_id": "UNKNOWN-1",
                    "broker_custom_field": "",
                    "fill_assignment_status": "ambiguous_unmapped_fill",
                }
            ],
        )
        (root / trade_date.isoformat() / "state.json").write_text("{}", encoding="utf-8")
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
            auto_trading=AutoTradingConfig(weekly_budget=100000, overrun_tolerance=0.0, basket_profit_buffer_min_twd=0.0),
            fees=FeeConfig(),
            providers=ProviderConfig(active="manual_csv"),
            project_root=Path.cwd(),
        )
        plan = WeekTradePlan(
            anchor_date=trade_date,
            week_trade_days=[date(2026, 4, 22), trade_date],
            buy_cutoff_day=date(2026, 4, 22),
            last_trade_day=trade_date,
            calendar_missing_warning=False,
            source_path=None,
        )
        with patch("sinopac_auto_trading.cli.resolve_week_trade_plan", return_value=plan), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ), patch(
            "sinopac_auto_trading.cli.input_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ):
            ambiguous_rows = _ambiguous_fill_rows(trade_date=trade_date)
            report = _build_daily_report(settings, trade_date)
        self.assertEqual(len(ambiguous_rows), 1)
        self.assertIn("ambiguous live fill rows", "\n".join(report["warnings"]))
        self.assertIn("2330", "\n".join(report["warnings"]))

    def test_build_daily_report_surfaces_ambiguous_fill_guard_counts_and_warnings(self) -> None:
        root = self._case_dir("daily-report-ambiguous-fill-guard")
        trade_date = date(2026, 4, 24)
        (root / trade_date.isoformat()).mkdir(parents=True, exist_ok=True)
        (root / trade_date.isoformat() / "state.json").write_text(
            '{"buy_loop":{"ambiguous_fill_guard_count":2,"broker_underheld_guard_count":1,"broker_underheld_guard_stocks":["2317"]},"sell_loop":{"ambiguous_fill_guard_count":1,"excluded_position_guard_count":2,"broker_underheld_guard_count":2,"broker_underheld_guard_stocks":["2330"]}}',
            encoding="utf-8",
        )
        self._write_csv(
            root / trade_date.isoformat() / "broker_position_mismatches.csv",
            [
                {
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "broker_qty": 4,
                    "strategy_qty": 5,
                    "missing_qty": 1,
                    "reason": "broker_qty_below_strategy_qty",
                }
            ],
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
            auto_trading=AutoTradingConfig(weekly_budget=100000, overrun_tolerance=0.0, basket_profit_buffer_min_twd=0.0),
            fees=FeeConfig(),
            providers=ProviderConfig(active="manual_csv"),
            project_root=Path.cwd(),
        )
        plan = WeekTradePlan(
            anchor_date=trade_date,
            week_trade_days=[date(2026, 4, 22), trade_date],
            buy_cutoff_day=date(2026, 4, 22),
            last_trade_day=trade_date,
            calendar_missing_warning=False,
            source_path=None,
        )
        with patch("sinopac_auto_trading.cli.resolve_week_trade_plan", return_value=plan), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ), patch(
            "sinopac_auto_trading.cli.input_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ):
            report = _build_daily_report(settings, trade_date)
        self.assertEqual(report["overview"]["buy_ambiguous_fill_guard_count"], 2)
        self.assertEqual(report["overview"]["sell_ambiguous_fill_guard_count"], 1)
        self.assertEqual(report["overview"]["ambiguous_fill_guard_count"], 3)
        self.assertEqual(report["overview"]["excluded_position_guard_count"], 2)
        self.assertEqual(report["overview"]["buy_broker_underheld_guard_count"], 1)
        self.assertEqual(report["overview"]["sell_broker_underheld_guard_count"], 2)
        self.assertEqual(report["overview"]["broker_underheld_guard_count"], 3)
        self.assertEqual(report["broker_underheld_rows"][0]["missing_qty"], 1)
        warnings_text = "\n".join(report["warnings"])
        self.assertIn("buy loop 因 ambiguous fills 尚待人工 reconciliation，已阻擋 2 筆 strategy lot 的新 live submit。", warnings_text)
        self.assertIn("sell loop 因 ambiguous fills 尚待人工 reconciliation，已阻擋 1 筆 strategy lot 的新 live submit。", warnings_text)
        self.assertIn("sell loop 已阻擋 2 筆 strategy lot 的新 live submit，因為券商持股包含同檔股票的 excluded 非策略部位。", warnings_text)
        self.assertIn("buy loop 已阻擋 1 筆 strategy lot 的新 live submit，因為券商持股低於策略部位", warnings_text)
        self.assertIn("broker-underheld rows", warnings_text)
        self.assertIn("券商持股低於策略部位", warnings_text)
        self.assertIn("2330", warnings_text)

    def test_build_daily_report_surfaces_fallback_position_quality_and_source_date(self) -> None:
        root = self._case_dir("daily-report-fallback-quality")
        trade_date = date(2026, 4, 24)
        self._write_csv(
            root / date(2026, 4, 22).isoformat() / "positions.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-22:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "source": "A",
                    "basket_tag": "main",
                    "holding_qty": 3,
                    "buy_avg_price": 100.0,
                    "buy_total_cost": 300.0,
                    "current_price": 118.0,
                    "status": "strategy_fill_scoped",
                }
            ],
        )
        self._write_csv(
            root / date(2026, 4, 22).isoformat() / "quote_snapshots.csv",
            [
                {
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "timestamp": "2026-04-22T13:00:00+08:00",
                    "last_price": 121.0,
                    "bid1": 120.5,
                    "ask1": 121.0,
                }
            ],
        )
        self._write_csv(
            root / trade_date.isoformat() / "sell_decisions.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-22:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "basket_tag": "main",
                    "sell_decision": "sell",
                    "sell_order_status": "filled_or_partially_filled",
                    "sold_qty": 1,
                    "remaining_qty": 2,
                    "realized_pnl": "",
                    "sell_pnl_source": "local_sell_order_fallback",
                }
            ],
        )
        (root / trade_date.isoformat() / "state.json").write_text("{}", encoding="utf-8")
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
            auto_trading=AutoTradingConfig(weekly_budget=100000, overrun_tolerance=0.0, basket_profit_buffer_min_twd=0.0),
            fees=FeeConfig(),
            providers=ProviderConfig(active="manual_csv"),
            project_root=Path.cwd(),
        )
        plan = WeekTradePlan(
            anchor_date=trade_date,
            week_trade_days=[date(2026, 4, 22), trade_date],
            buy_cutoff_day=date(2026, 4, 22),
            last_trade_day=trade_date,
            calendar_missing_warning=False,
            source_path=None,
        )
        with patch("sinopac_auto_trading.cli.resolve_week_trade_plan", return_value=plan), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ), patch(
            "sinopac_auto_trading.cli.input_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ):
            report = _build_daily_report(settings, trade_date)
        self.assertEqual(report["overview"]["position_data_quality"], "fallback")
        self.assertEqual(report["overview"]["positions_source_date"], "2026-04-22")
        self.assertIn("fallback reconstruction", "\n".join(report["warnings"]))
        self.assertIn("已實現賣出 PnL 有 1 筆 strategy lot 先使用 local order fallback", "\n".join(report["warnings"]))
        self.assertIn("2026-04-22", "\n".join(report["warnings"]))

    def test_build_daily_report_overview_includes_realized_pnl_and_realized_cost_basis(self) -> None:
        root = self._case_dir("daily-report-overview-realized")
        trade_date = date(2026, 4, 24)
        self._write_csv(
            root / trade_date.isoformat() / "positions.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-22:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "source": "A",
                    "basket_tag": "main",
                    "holding_qty": 2,
                    "buy_avg_price": 100.0,
                    "buy_total_cost": 200.0,
                    "current_price": 121.0,
                    "status": "strategy_fill_scoped",
                }
            ],
        )
        self._write_csv(
            root / trade_date.isoformat() / "sell_decisions.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-22:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "basket_tag": "main",
                    "sell_decision": "sell",
                    "sell_order_status": "filled_or_partially_filled",
                    "sold_qty": 1,
                    "remaining_qty": 2,
                    "allocated_buy_cost": 100.0,
                    "realized_pnl": 19.0,
                    "sell_pnl_source": "live_fill_reconciled",
                }
            ],
        )
        (root / trade_date.isoformat() / "state.json").write_text("{}", encoding="utf-8")
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
            auto_trading=AutoTradingConfig(weekly_budget=100000, overrun_tolerance=0.0, basket_profit_buffer_min_twd=0.0),
            fees=FeeConfig(),
            providers=ProviderConfig(active="manual_csv"),
            project_root=Path.cwd(),
        )
        plan = WeekTradePlan(
            anchor_date=trade_date,
            week_trade_days=[trade_date],
            buy_cutoff_day=trade_date,
            last_trade_day=trade_date,
            calendar_missing_warning=False,
            source_path=None,
        )
        with patch("sinopac_auto_trading.cli.resolve_week_trade_plan", return_value=plan), patch(
            "sinopac_auto_trading.cli.auto_trading_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ), patch(
            "sinopac_auto_trading.cli.input_dir_for",
            side_effect=lambda value: root / value.isoformat(),
        ):
            report = _build_daily_report(settings, trade_date)

        self.assertEqual(report["overview"]["used_cash"], 200.0)
        self.assertEqual(report["overview"]["current_equity"], 242.0)
        self.assertEqual(report["overview"]["unrealized_pnl"], 42.0)
        self.assertEqual(report["overview"]["realized_pnl"], 19.0)
        self.assertEqual(report["overview"]["strategy_pnl_after_fee_tax"], 61.0)
        self.assertAlmostEqual(report["overview"]["strategy_return"], 61.0 / 300.0)

    def test_sell_fill_stats_by_stock_calculates_realized_profit_and_remaining_qty(self) -> None:
        fills_rows = [
            {"strategy_lot_id": "run-1:2330", "stock_id": "2330", "side": "Sell", "fill_qty": 2, "fill_price": 110.0},
            {"strategy_lot_id": "run-1:2330", "stock_id": "2330", "side": "Sell", "fill_qty": 1, "fill_price": 112.0},
            {"strategy_lot_id": "run-1:2330", "stock_id": "2330", "side": "Buy", "fill_qty": 1, "fill_price": 90.0},
        ]
        positions = [
            StrategyPosition(
                strategy_lot_id="run-1:2330",
                stock_id="2330",
                stock_name="TSMC",
                holding_qty=5,
                buy_avg_price=100.0,
                buy_total_cost=500.0,
                source="A",
            )
        ]
        stats = _sell_fill_stats_by_stock(
            fills_rows=fills_rows,
            positions=positions,
            fees=FeeConfig(minimum_commission=0.0),
        )
        self.assertIn("run-1:2330", stats)
        stock_stats = stats["run-1:2330"]
        self.assertEqual(stock_stats["sold_qty"], 3)
        self.assertEqual(stock_stats["remaining_qty"], 3)
        self.assertAlmostEqual(stock_stats["fill_avg_price"], 332.0 / 3.0)
        self.assertAlmostEqual(stock_stats["realized_pnl"], 30.5309, places=4)


if __name__ == "__main__":
    unittest.main()
