from __future__ import annotations

from datetime import date

from ..selection_provider import SelectionItem, SelectionProvider


class StaticListSelectionProvider(SelectionProvider):
    def __init__(
        self,
        *,
        preselect_items: list[SelectionItem] | None = None,
        final_list_items: list[SelectionItem] | None = None,
        provider_label: str = "static_list",
    ) -> None:
        self.preselect_items = list(preselect_items or [])
        self.final_list_items = list(final_list_items) if final_list_items is not None else None
        self.provider_label = provider_label

    def load_preselect(self, trade_date: date) -> list[SelectionItem]:
        return list(self.preselect_items)

    def load_final_list(self, trade_date: date) -> list[SelectionItem] | None:
        return list(self.final_list_items) if self.final_list_items is not None else None

    def provider_name(self) -> str:
        return self.provider_label
