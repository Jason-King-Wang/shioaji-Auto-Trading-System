from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from ..basket import normalize_basket_tag
from ..paths import input_dir_for
from ..selection_provider import SelectionItem, SelectionProvider


def _as_bool(raw: str | None) -> bool | None:
    if raw is None or not raw.strip():
        return None
    return raw.strip().lower() in {"1", "true", "yes", "y"}


def _as_int(raw: str | None) -> int | None:
    if raw is None or not raw.strip():
        return None
    return int(float(raw))


def _as_float(raw: str | None) -> float | None:
    if raw is None or not raw.strip():
        return None
    return float(raw)


def _load_selection_csv(path: Path) -> list[SelectionItem]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        items: list[SelectionItem] = []
        for row in reader:
            stock_id = (row.get("stock_id") or "").strip()
            if not stock_id:
                continue
            items.append(
                SelectionItem(
                    stock_id=stock_id,
                    stock_name=(row.get("stock_name") or stock_id).strip(),
                    source=(row.get("source") or "unknown").strip() or "unknown",
                    basket_tag=normalize_basket_tag(row.get("basket_tag")),
                    source_weight=_as_float(row.get("source_weight")),
                    a_flag=_as_bool(row.get("a_flag")),
                    b_flag=_as_bool(row.get("b_flag")),
                    role_level=(row.get("role_level") or "").strip() or None,
                    theme=(row.get("theme") or "").strip() or None,
                    catalyst_flag=_as_bool(row.get("catalyst_flag")),
                    model_rank=_as_int(row.get("model_rank")),
                    model_score=_as_float(row.get("model_score")),
                    user_priority=_as_int(row.get("user_priority")),
                    force_include=_as_bool(row.get("force_include")),
                    force_exclude=_as_bool(row.get("force_exclude")),
                    target_weight=_as_float(row.get("target_weight")),
                    target_qty=_as_int(row.get("target_qty")),
                    reference_price=_as_float(row.get("reference_price")),
                    note=(row.get("note") or "").strip(),
                )
            )
    return items


class ManualCsvSelectionProvider(SelectionProvider):
    def __init__(self, input_root: Path | None = None) -> None:
        self.input_root = input_root

    def _trade_dir(self, trade_date: date) -> Path:
        return self.input_root / trade_date.isoformat() if self.input_root else input_dir_for(trade_date)

    def load_preselect(self, trade_date: date) -> list[SelectionItem]:
        path = self._trade_dir(trade_date) / "auto_trade_preselect.csv"
        if not path.exists():
            raise FileNotFoundError(f"Manual preselect file not found: {path}")
        return _load_selection_csv(path)

    def load_final_list(self, trade_date: date) -> list[SelectionItem] | None:
        path = self._trade_dir(trade_date) / "auto_trade_final_list.csv"
        if not path.exists():
            return None
        return _load_selection_csv(path)

    def provider_name(self) -> str:
        return "manual_csv"
