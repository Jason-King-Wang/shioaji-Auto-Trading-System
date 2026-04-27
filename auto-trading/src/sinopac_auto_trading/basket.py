from __future__ import annotations

import hashlib
from datetime import date

DEFAULT_BASKET_TAG = "main"


def normalize_basket_tag(raw: object) -> str:
    text = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    return text or DEFAULT_BASKET_TAG


def strategy_lot_id_for(trade_date: date, stock_id: str, basket_tag: object = DEFAULT_BASKET_TAG) -> str:
    normalized_tag = normalize_basket_tag(basket_tag)
    normalized_stock = str(stock_id).strip()
    if normalized_tag == DEFAULT_BASKET_TAG:
        return f"auto-{trade_date.isoformat()}:{normalized_stock}"
    return f"auto-{trade_date.isoformat()}:{normalized_tag}:{normalized_stock}"


def basket_tag_from_strategy_lot_id(strategy_lot_id: object) -> str:
    text = str(strategy_lot_id or "").strip()
    parts = text.split(":")
    if len(parts) >= 3:
        return normalize_basket_tag(parts[-2])
    return DEFAULT_BASKET_TAG


def broker_custom_field_for_strategy_lot(strategy_lot_id: object, prefix: object = "AT") -> str:
    text = str(strategy_lot_id or "").strip()
    compact_prefix = "".join(char for char in str(prefix or "").upper() if char.isalnum())[:1]
    if not text:
        return (compact_prefix or "A") + "00000"
    digest = hashlib.blake2s(text.encode("utf-8"), digest_size=4).hexdigest().upper()
    token = f"{compact_prefix}{digest[:5]}" if compact_prefix else digest[:6]
    return token[:6]
