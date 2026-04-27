from __future__ import annotations

from datetime import date
from pathlib import Path

from ..selection_provider import SelectionItem, SelectionProvider
from .manual_csv import _load_selection_csv


class StockModelVaultExportSelectionProvider(SelectionProvider):
    def __init__(
        self,
        *,
        export_dir: str | Path,
        preselect_filename: str = "auto_trade_preselect.csv",
        final_list_filename: str = "auto_trade_final_list.csv",
    ) -> None:
        self.export_dir = Path(export_dir)
        self.preselect_filename = preselect_filename
        self.final_list_filename = final_list_filename

    def _resolve_path(self, filename: str) -> Path:
        return self.export_dir / filename

    def load_preselect(self, trade_date: date) -> list[SelectionItem]:
        path = self._resolve_path(self.preselect_filename)
        if not path.exists():
            raise FileNotFoundError(
                f"StockModelVault export not found: {path}. "
                "Use manual_csv or place exported CSV files in the configured export directory."
            )
        return _load_selection_csv(path)

    def load_final_list(self, trade_date: date) -> list[SelectionItem] | None:
        path = self._resolve_path(self.final_list_filename)
        if not path.exists():
            return None
        return _load_selection_csv(path)

    def provider_name(self) -> str:
        return "stock_model_vault_export"
