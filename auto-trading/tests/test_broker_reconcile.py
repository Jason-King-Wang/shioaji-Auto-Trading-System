from __future__ import annotations

import csv
import json
import shutil
import unittest
import uuid
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sinopac_auto_trading.broker_adapter import PositionSnapshot
from sinopac_auto_trading.calendar import WeekTradePlan
from sinopac_auto_trading.cli import _reconcile_broker_state, _reconcile_target_stock_ids
from sinopac_auto_trading.order_engine import QuoteState


class BrokerReconcileTests(unittest.TestCase):
    def _case_dir(self, name: str) -> Path:
        path = Path(__file__).resolve().parent / "_tmp" / f"{name}-{uuid.uuid4().hex}"
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    @staticmethod
    def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def _read_csv(path: Path) -> list[dict[str, str]]:
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def _patch_calendar_and_dirs(self, root: Path, trade_date: date):
        plan = WeekTradePlan(
            anchor_date=trade_date,
            week_trade_days=[trade_date],
            buy_cutoff_day=trade_date,
            last_trade_day=trade_date,
            calendar_missing_warning=False,
            source_path=None,
        )
        return patch.multiple(
            "sinopac_auto_trading.cli",
            auto_trading_dir_for=lambda value: root / value.isoformat(),
            resolve_week_trade_plan=lambda value: plan,
        ), patch.multiple(
            "sinopac_auto_trading.ledger",
            auto_trading_dir_for=lambda value: root / value.isoformat(),
            resolve_week_trade_plan=lambda value: plan,
        )

    def test_reconcile_broker_state_writes_fills_positions_and_pnl_without_orders(self) -> None:
        root = self._case_dir("broker-reconcile")
        trade_date = date(2026, 4, 24)
        run_dir = root / trade_date.isoformat()
        self._write_csv(
            run_dir / "orders.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-24:2330",
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "source": "A",
                    "basket_tag": "main",
                    "order_id": "BUY-2330-1",
                    "broker_custom_field": "BL2330",
                }
            ],
        )

        class _Broker:
            def get_positions(self):
                return [PositionSnapshot(stock_id="2330", stock_name="TSMC", quantity=1, avg_price=2090.0)]

            def get_fills(self, since=None):
                return [
                    {
                        "order_id": "BUY-2330-1",
                        "stock_id": "2330",
                        "side": "Buy",
                        "fill_qty": 1,
                        "fill_price": 2090.0,
                        "fill_time": "2026-04-24T09:10:10+08:00",
                        "broker_custom_field": "BL2330",
                    }
                ]

            def get_quote_state(self, stock_id: str):
                return QuoteState(last_price=2095.0, bid1=2090.0, ask1=2095.0), "TSMC", "TSE", "2026-04-24T09:11:00+08:00"

            def place_buy_order(self, *args, **kwargs):
                raise AssertionError("reconcile must not place buy orders")

            def place_sell_order(self, *args, **kwargs):
                raise AssertionError("reconcile must not place sell orders")

            def cancel_order(self, *args, **kwargs):
                raise AssertionError("reconcile must not cancel orders")

        cli_patch, ledger_patch = self._patch_calendar_and_dirs(root, trade_date)
        with cli_patch, ledger_patch:
            result = _reconcile_broker_state(
                settings=SimpleNamespace(),
                trade_date=trade_date,
                broker=_Broker(),
                target_stock_ids={"2330"},
            )

        self.assertEqual(result.fills_count, 1)
        self.assertEqual(result.positions_count, 1)
        self.assertEqual(result.ambiguous_fill_count, 0)

        fills = self._read_csv(run_dir / "fills.csv")
        self.assertEqual(fills[0]["strategy_lot_id"], "auto-2026-04-24:2330")
        self.assertEqual(fills[0]["fill_assignment_status"], "resolved_order_id")

        positions = self._read_csv(run_dir / "positions.csv")
        self.assertEqual(positions[0]["holding_qty"], "1")
        self.assertEqual(positions[0]["status"], "strategy_fill_scoped")
        self.assertAlmostEqual(float(positions[0]["current_price"]), 2095.0)

        snapshots = self._read_csv(run_dir / "pnl_snapshots.csv")
        self.assertEqual(len(snapshots), 1)
        self.assertAlmostEqual(float(snapshots[0]["cash_used"]), 2090.0)
        self.assertAlmostEqual(float(snapshots[0]["strategy_equity"]), 2095.0)

        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "broker_reconciled")
        self.assertEqual(state["broker_reconcile"]["fills_count"], 1)
        self.assertEqual(state["broker_reconcile"]["positions_count"], 1)
        self.assertAlmostEqual(float(state["pnl_snapshot"]["strategy_equity"]), 2095.0)

    def test_reconcile_broker_state_does_not_reapply_same_day_positions(self) -> None:
        root = self._case_dir("broker-reconcile-idempotent")
        trade_date = date(2026, 4, 24)
        run_dir = root / trade_date.isoformat()
        self._write_csv(
            run_dir / "positions.csv",
            [
                {
                    "run_id": "auto-2026-04-24",
                    "strategy_lot_id": "auto-2026-04-24:3044",
                    "stock_id": "3044",
                    "stock_name": "Tripod",
                    "source": "A",
                    "basket_tag": "main",
                    "holding_qty": 1,
                    "buy_avg_price": 500.0,
                    "buy_total_cost": 500.0,
                    "current_price": 500.0,
                    "status": "strategy_fill_scoped",
                }
            ],
        )
        self._write_csv(
            run_dir / "orders.csv",
            [
                {
                    "strategy_lot_id": "auto-2026-04-24:3044",
                    "stock_id": "3044",
                    "stock_name": "Tripod",
                    "source": "A",
                    "basket_tag": "main",
                    "order_id": "BUY-3044-1",
                    "broker_custom_field": "BL3044",
                }
            ],
        )

        class _Broker:
            def get_positions(self):
                return [PositionSnapshot(stock_id="3044", stock_name="Tripod", quantity=1, avg_price=500.0)]

            def get_fills(self, since=None):
                return [
                    {
                        "order_id": "BUY-3044-1",
                        "stock_id": "3044",
                        "side": "Buy",
                        "fill_qty": 1,
                        "fill_price": 500.0,
                        "fill_time": "2026-04-24T09:10:10+08:00",
                        "broker_custom_field": "BL3044",
                    }
                ]

            def get_quote_state(self, stock_id: str):
                return QuoteState(last_price=497.5, bid1=497.0, ask1=497.5), "Tripod", "TSE", "2026-04-24T09:11:00+08:00"

            def place_buy_order(self, *args, **kwargs):
                raise AssertionError("reconcile must not place buy orders")

            def place_sell_order(self, *args, **kwargs):
                raise AssertionError("reconcile must not place sell orders")

            def cancel_order(self, *args, **kwargs):
                raise AssertionError("reconcile must not cancel orders")

        cli_patch, ledger_patch = self._patch_calendar_and_dirs(root, trade_date)
        with cli_patch, ledger_patch:
            result = _reconcile_broker_state(
                settings=SimpleNamespace(),
                trade_date=trade_date,
                broker=_Broker(),
                target_stock_ids={"3044"},
            )

        self.assertEqual(result.fills_count, 1)
        self.assertEqual(result.positions_count, 1)
        positions = self._read_csv(run_dir / "positions.csv")
        self.assertEqual(positions[0]["holding_qty"], "1")
        self.assertAlmostEqual(float(positions[0]["buy_total_cost"]), 500.0)
        self.assertEqual(self._read_csv(run_dir / "excluded_positions.csv"), [])
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["broker_reconcile"]["opening_positions_source_date"], "")
        self.assertTrue(state["broker_reconcile"]["ignored_same_day_positions"])
        self.assertAlmostEqual(float(state["pnl_snapshot"]["cash_used"]), 500.0)

    def test_reconcile_target_stock_ids_include_allowed_live_artifacts(self) -> None:
        root = self._case_dir("broker-reconcile-targets")
        trade_date = date(2026, 4, 24)
        run_dir = root / trade_date.isoformat()
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "allowed_live_order_2330_task.json").write_text("{}", encoding="utf-8")
        (run_dir / "chase_2330.json").write_text(
            json.dumps({"stock_id": "2330", "stock_name": "TSMC"}),
            encoding="utf-8",
        )
        self._write_csv(
            run_dir / "sizing.csv",
            [
                {
                    "stock_id": "2317",
                    "stock_name": "Hon Hai",
                }
            ],
        )

        cli_patch, ledger_patch = self._patch_calendar_and_dirs(root, trade_date)
        with cli_patch, ledger_patch:
            target_stock_ids = _reconcile_target_stock_ids(trade_date, ["2454"])

        self.assertEqual(target_stock_ids, {"2317", "2330", "2454"})


if __name__ == "__main__":
    unittest.main()
