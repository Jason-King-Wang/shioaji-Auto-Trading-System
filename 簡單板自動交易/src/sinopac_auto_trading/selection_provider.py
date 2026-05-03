from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date

from .basket import DEFAULT_BASKET_TAG, normalize_basket_tag


@dataclass(slots=True)
class SelectionItem:
    stock_id: str
    stock_name: str
    source: str = "unknown"
    basket_tag: str = DEFAULT_BASKET_TAG
    source_weight: float | None = None
    a_flag: bool | None = None
    b_flag: bool | None = None
    role_level: str | None = None
    theme: str | None = None
    catalyst_flag: bool | None = None
    model_rank: int | None = None
    model_score: float | None = None
    user_priority: int | None = None
    force_include: bool | None = None
    force_exclude: bool | None = None
    target_weight: float | None = None
    target_qty: int | None = None
    reference_price: float | None = None
    note: str = ""

    def normalized_source_weight(self) -> float:
        if self.source_weight is not None:
            return float(self.source_weight)
        if self.source.upper() in {"A+B", "AB", "DUAL"} or (self.a_flag and self.b_flag):
            return 2.0
        return 1.0

    def normalized_basket_tag(self) -> str:
        return normalize_basket_tag(self.basket_tag)


class SelectionProvider(ABC):
    @abstractmethod
    def load_preselect(self, trade_date: date) -> list[SelectionItem]:
        raise NotImplementedError

    @abstractmethod
    def load_final_list(self, trade_date: date) -> list[SelectionItem] | None:
        raise NotImplementedError

    @abstractmethod
    def provider_name(self) -> str:
        raise NotImplementedError
