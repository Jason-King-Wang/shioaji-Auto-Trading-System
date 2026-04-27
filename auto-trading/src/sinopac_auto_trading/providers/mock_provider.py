from __future__ import annotations

from datetime import date

from ..selection_provider import SelectionItem
from .static_list import StaticListSelectionProvider


class MockSelectionProvider(StaticListSelectionProvider):
    def __init__(self) -> None:
        super().__init__(
            preselect_items=[
                SelectionItem(stock_id="2330", stock_name="TSMC", source="mock", model_score=95.0, user_priority=1),
                SelectionItem(stock_id="2317", stock_name="Hon Hai", source="mock", model_score=88.0, user_priority=2),
            ],
            final_list_items=None,
            provider_label="mock_provider",
        )

    def load_preselect(self, trade_date: date) -> list[SelectionItem]:
        return super().load_preselect(trade_date)
