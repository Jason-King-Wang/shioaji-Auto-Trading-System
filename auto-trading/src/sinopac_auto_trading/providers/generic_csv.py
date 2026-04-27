from __future__ import annotations

from datetime import date
from pathlib import Path

from ..selection_provider import SelectionItem, SelectionProvider
from .manual_csv import _load_selection_csv


class GenericCsvSelectionProvider(SelectionProvider):
    def __init__(self, *, preselect_path: str | Path | None = None, final_list_path: str | Path | None = None) -> None:
        self.preselect_path = Path(preselect_path) if preselect_path else None
        self.final_list_path = Path(final_list_path) if final_list_path else None

    def load_preselect(self, trade_date: date) -> list[SelectionItem]:
        if not self.preselect_path:
            raise FileNotFoundError("Generic CSV provider missing preselect_path.")
        if not self.preselect_path.exists():
            raise FileNotFoundError(f"Generic preselect CSV not found: {self.preselect_path}")
        return _load_selection_csv(self.preselect_path)

    def load_final_list(self, trade_date: date) -> list[SelectionItem] | None:
        if not self.final_list_path:
            return None
        if not self.final_list_path.exists():
            return None
        return _load_selection_csv(self.final_list_path)

    def provider_name(self) -> str:
        return "generic_csv"
