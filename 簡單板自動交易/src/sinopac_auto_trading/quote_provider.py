from __future__ import annotations

import csv
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(slots=True)
class QuoteSnapshot:
    stock_id: str
    timestamp: datetime
    open_price: float
    last_price: float
    bid1: float | None = None
    ask1: float | None = None
    high_price: float | None = None
    low_price: float | None = None
    volume_ratio: float | None = None
    vwap_position: float | None = None
    twii_return: float | None = None


class QuoteProvider(ABC):
    @abstractmethod
    def get_snapshot(self, stock_id: str, at_time: datetime | None = None) -> QuoteSnapshot | None:
        raise NotImplementedError

    @abstractmethod
    def history_between(self, stock_id: str, start: datetime, end: datetime) -> list[QuoteSnapshot]:
        raise NotImplementedError


class MemoryQuoteProvider(QuoteProvider):
    def __init__(self, snapshots: list[QuoteSnapshot] | None = None) -> None:
        self.snapshots = list(snapshots or [])

    def get_snapshot(self, stock_id: str, at_time: datetime | None = None) -> QuoteSnapshot | None:
        candidates = [item for item in self.snapshots if item.stock_id == stock_id]
        if at_time is not None:
            candidates = [item for item in candidates if item.timestamp <= at_time]
        return max(candidates, key=lambda item: item.timestamp, default=None)

    def history_between(self, stock_id: str, start: datetime, end: datetime) -> list[QuoteSnapshot]:
        return [
            item
            for item in self.snapshots
            if item.stock_id == stock_id and start <= item.timestamp <= end
        ]


def load_fake_quotes_csv(path: Path) -> MemoryQuoteProvider:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        snapshots: list[QuoteSnapshot] = []
        for row in reader:
            snapshots.append(
                QuoteSnapshot(
                    stock_id=row["stock_id"],
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    open_price=float(row["open_price"]),
                    last_price=float(row["last_price"]),
                    bid1=float(row["bid1"]) if row.get("bid1") else None,
                    ask1=float(row["ask1"]) if row.get("ask1") else None,
                    high_price=float(row["high_price"]) if row.get("high_price") else None,
                    low_price=float(row["low_price"]) if row.get("low_price") else None,
                    volume_ratio=float(row["volume_ratio"]) if row.get("volume_ratio") else None,
                    vwap_position=float(row["vwap_position"]) if row.get("vwap_position") else None,
                    twii_return=float(row["twii_return"]) if row.get("twii_return") else None,
                )
            )
    return MemoryQuoteProvider(snapshots)
