from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


def _parse_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    return float(text)


def _parse_int(raw: str | None) -> int | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    return int(float(text))


def _pick_first_float(row: dict[str, str], columns: tuple[str, ...]) -> float | None:
    for column in columns:
        value = _parse_float(row.get(column))
        if value is not None:
            return value
    return None


def _pick_first_text(row: dict[str, str], columns: tuple[str, ...]) -> str:
    for column in columns:
        value = (row.get(column) or "").strip()
        if value:
            return value
    return ""


def _normalize_side(raw: str | None, default_side: str) -> str:
    if not raw:
        return default_side
    value = raw.strip().lower()
    if value in {"buy", "long", "bullish", "up", "strong_up"}:
        return "Buy"
    if value in {"sell", "short", "bearish", "down", "strong_down"}:
        return "Sell"
    return default_side


@dataclass(slots=True)
class PredictionSignal:
    stock_id: str
    stock_name: str
    exchange_hint: str
    side: str
    reference_price: float
    confidence: float | None
    model_rank: int | None
    stage_1_price: float | None
    stage_2_price: float | None
    target_price: float | None
    note: str


def load_prediction_signals(csv_path: Path, default_side: str = "Buy") -> list[PredictionSignal]:
    signals: list[PredictionSignal] = []

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            stock_id = (row.get("stock_id") or row.get("symbol") or "").strip()
            stock_name = (row.get("stock_name") or row.get("name") or "").strip()
            if not stock_id:
                continue

            reference_price = _pick_first_float(
                row,
                (
                    "reference_close",
                    "stage_2_price",
                    "stage_1_price",
                    "pred_high_price",
                    "pred_peak_price",
                    "price",
                ),
            )
            if reference_price is None:
                continue

            side = _normalize_side(
                row.get("order_side") or row.get("side") or row.get("direction_pred"),
                default_side=default_side,
            )
            note = _pick_first_text(
                row,
                ("prediction_reason", "key_observation", "signal_summary", "note"),
            )
            signals.append(
                PredictionSignal(
                    stock_id=stock_id,
                    stock_name=stock_name or stock_id,
                    exchange_hint=_pick_first_text(row, ("exchange", "exchange_hint", "market")),
                    side=side,
                    reference_price=reference_price,
                    confidence=_pick_first_float(row, ("pred_confidence", "confidence", "direction_score")),
                    model_rank=_parse_int(row.get("model_rank")),
                    stage_1_price=_pick_first_float(row, ("stage_1_price",)),
                    stage_2_price=_pick_first_float(row, ("stage_2_price", "pred_peak_price")),
                    target_price=_pick_first_float(row, ("stage_2_price", "stage_1_price", "pred_peak_price")),
                    note=note,
                )
            )

    return signals
