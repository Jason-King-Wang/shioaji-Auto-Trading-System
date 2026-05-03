from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


AUTO_TRADE_SCHEMA = """
CREATE TABLE IF NOT EXISTS auto_trade_runs (
    run_id TEXT PRIMARY KEY,
    week_id TEXT,
    start_date TEXT,
    last_trade_day TEXT,
    buy_cutoff_day TEXT,
    mode TEXT,
    selection_provider TEXT,
    weekly_budget REAL,
    overrun_tolerance REAL,
    hard_budget REAL,
    status TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS auto_trade_watchlist (
    run_id TEXT,
    trade_date TEXT,
    stock_id TEXT,
    stock_name TEXT,
    source TEXT,
    source_weight REAL,
    a_flag INTEGER,
    b_flag INTEGER,
    role_level TEXT,
    theme TEXT,
    catalyst_flag INTEGER,
    model_score REAL,
    finalizer_score REAL,
    final_flag INTEGER,
    provider_name TEXT,
    reason TEXT
);
CREATE TABLE IF NOT EXISTS auto_trade_orders (
    run_id TEXT,
    strategy_lot_id TEXT,
    stock_id TEXT,
    side TEXT,
    order_id TEXT,
    order_status TEXT,
    order_price REAL,
    order_qty INTEGER,
    filled_qty INTEGER,
    remaining_qty INTEGER,
    order_mode TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS auto_trade_fills (
    run_id TEXT,
    strategy_lot_id TEXT,
    stock_id TEXT,
    side TEXT,
    fill_price REAL,
    fill_qty INTEGER,
    fee REAL,
    tax REAL,
    fill_time TEXT,
    broker_fill_id TEXT
);
CREATE TABLE IF NOT EXISTS auto_trade_positions (
    run_id TEXT,
    strategy_lot_id TEXT,
    stock_id TEXT,
    stock_name TEXT,
    source TEXT,
    holding_qty INTEGER,
    buy_avg_price REAL,
    buy_total_cost REAL,
    status TEXT
);
CREATE TABLE IF NOT EXISTS auto_trade_pnl_snapshots (
    run_id TEXT,
    trade_date TEXT,
    snapshot_time TEXT,
    strategy_equity REAL,
    cash_used REAL,
    unrealized_pnl REAL,
    realized_pnl REAL,
    total_pnl_after_fee_tax REAL,
    strategy_return REAL,
    twii_return REAL,
    tsmc_return REAL
);
CREATE TABLE IF NOT EXISTS auto_trade_sell_decisions (
    run_id TEXT,
    trade_date TEXT,
    stock_id TEXT,
    sell_decision TEXT,
    sell_decision_reason TEXT,
    conservative_sell_price REAL,
    conservative_profit REAL
);
CREATE TABLE IF NOT EXISTS auto_trade_event_log (
    run_id TEXT,
    timestamp TEXT,
    level TEXT,
    event_type TEXT,
    stock_id TEXT,
    message TEXT,
    metadata_json TEXT
);
"""


def _serialize(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _serialize(item) for key, item in asdict(value).items()}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    return value


def _merge_dict(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


class SQLiteStateStore:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.run_dir / "state.sqlite"
        self.state_json_path = self.run_dir / "state.json"
        self.event_log_path = self.run_dir / "event_log.jsonl"

    def initialize(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(AUTO_TRADE_SCHEMA)

    def write_state_json(self, payload: dict[str, Any]) -> None:
        self.state_json_path.write_text(json.dumps(_serialize(payload), indent=2, ensure_ascii=False), encoding="utf-8")

    def read_state_json(self) -> dict[str, Any]:
        if not self.state_json_path.exists():
            return {}
        raw = self.state_json_path.read_text(encoding="utf-8").strip()
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def merge_state_json(self, patch: dict[str, Any]) -> dict[str, Any]:
        merged = _merge_dict(self.read_state_json(), _serialize(patch))
        self.write_state_json(merged)
        return merged

    def append_event(
        self,
        *,
        run_id: str,
        timestamp: str,
        level: str,
        event_type: str,
        stock_id: str = "",
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        record = {
            "run_id": run_id,
            "timestamp": timestamp,
            "level": level,
            "event_type": event_type,
            "stock_id": stock_id,
            "message": message,
            "metadata": _serialize(metadata or {}),
        }
        with self.event_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO auto_trade_event_log
                (run_id, timestamp, level, event_type, stock_id, message, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    timestamp,
                    level,
                    event_type,
                    stock_id,
                    message,
                    json.dumps(_serialize(metadata or {}), ensure_ascii=False),
                ),
            )

    def write_rows_csv(self, filename: str, rows: list[dict[str, Any]]) -> Path:
        path = self.run_dir / filename
        if not rows:
            path.write_text("", encoding="utf-8")
            return path
        fieldnames = list(rows[0].keys())
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(_serialize(row))
        return path
