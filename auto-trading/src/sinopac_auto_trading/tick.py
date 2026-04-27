from __future__ import annotations

import math


def tick_size(price: float) -> float:
    if price < 10:
        return 0.01
    if price < 50:
        return 0.05
    if price < 100:
        return 0.1
    if price < 500:
        return 0.5
    if price < 1000:
        return 1.0
    return 5.0


def normalize_price_to_valid_tick(price: float) -> float:
    if price <= 0:
        raise ValueError("Price must be positive.")
    size = tick_size(price)
    normalized = math.floor((price + 1e-9) / size) * size
    return round(normalized, 2)


def _next_tick(price: float) -> float:
    current = normalize_price_to_valid_tick(price)
    return round(current + tick_size(current), 2)


def _previous_tick(price: float) -> float:
    current = normalize_price_to_valid_tick(price)
    if current <= 10:
        step = 0.01
    elif current <= 50:
        step = 0.05
    elif current <= 100:
        step = 0.1
    elif current <= 500:
        step = 0.5
    elif current <= 1000:
        step = 1.0
    else:
        step = 5.0
    previous = round(current - step, 2)
    if previous <= 0:
        raise ValueError("Price cannot tick below zero.")
    return normalize_price_to_valid_tick(previous)


def tick_up(price: float, n: int = 1) -> float:
    current = normalize_price_to_valid_tick(price)
    for _ in range(max(n, 0)):
        current = _next_tick(current)
    return current


def tick_down(price: float, n: int = 1) -> float:
    current = normalize_price_to_valid_tick(price)
    for _ in range(max(n, 0)):
        current = _previous_tick(current)
    return current


def price_to_tick_index(price: float) -> int:
    normalized = normalize_price_to_valid_tick(price)
    if normalized < 10:
        return int(round(normalized / 0.01))
    if normalized < 50:
        return 1000 + int(round((normalized - 10) / 0.05))
    if normalized < 100:
        return 1800 + int(round((normalized - 50) / 0.1))
    if normalized < 500:
        return 2300 + int(round((normalized - 100) / 0.5))
    if normalized < 1000:
        return 3100 + int(round((normalized - 500) / 1.0))
    return 3600 + int(round((normalized - 1000) / 5.0))


def tick_distance(price_a: float, price_b: float) -> int:
    return abs(price_to_tick_index(price_a) - price_to_tick_index(price_b))
