from __future__ import annotations

from dataclasses import dataclass

from .basket import DEFAULT_BASKET_TAG, normalize_basket_tag
from .config import AutoTradingConfig, FeeConfig
from .tick import tick_down


@dataclass(slots=True)
class StrategyPosition:
    strategy_lot_id: str
    stock_id: str
    stock_name: str
    holding_qty: int
    buy_avg_price: float
    buy_total_cost: float
    source: str = "unknown"
    basket_tag: str = "main"


@dataclass(slots=True)
class SellQuote:
    last_price: float
    bid1: float | None = None
    ask1: float | None = None


@dataclass(slots=True)
class SellDecision:
    stock_id: str
    stock_name: str
    can_sell_flag: bool
    sell_decision: str
    sell_decision_reason: str
    conservative_sell_price: float
    conservative_profit: float
    estimated_sell_net_proceeds: float


@dataclass(slots=True)
class BasketRecommendation:
    basket_conservative_profit: float
    threshold: float
    loser_loss_ratio: float
    recommendation: str


@dataclass(slots=True)
class SellSubmissionGate:
    allowed: bool
    status: str
    reason: str


def conservative_sell_price(quote: SellQuote, *, after_1320: bool = False) -> float:
    anchor = quote.bid1 or quote.last_price
    if after_1320:
        return min(tick_down(anchor, 3), anchor * (1 - 0.0050))
    return min(tick_down(anchor, 2), anchor * (1 - 0.0035))


def evaluate_sell_decision(
    position: StrategyPosition,
    quote: SellQuote,
    *,
    fees: FeeConfig,
    auto: AutoTradingConfig,
    after_1320: bool = False,
) -> SellDecision:
    sell_price = conservative_sell_price(quote, after_1320=after_1320)
    gross = sell_price * position.holding_qty
    sell_fee = fees.estimate_sell_fee(gross)
    sell_tax = fees.estimate_sell_tax(gross)
    sell_net = gross - sell_fee - sell_tax
    conservative_profit = sell_net - position.buy_total_cost
    threshold = max(
        position.buy_total_cost * auto.per_stock_profit_buffer_pct,
        auto.per_stock_profit_buffer_min_twd,
    )
    can_sell = conservative_profit > threshold
    return SellDecision(
        stock_id=position.stock_id,
        stock_name=position.stock_name,
        can_sell_flag=can_sell,
        sell_decision="sell" if can_sell else "hold",
        sell_decision_reason="passed conservative threshold" if can_sell else "below conservative threshold",
        conservative_sell_price=sell_price,
        conservative_profit=conservative_profit,
        estimated_sell_net_proceeds=sell_net,
    )


def basket_recommendation(decisions: list[SellDecision], positions: list[StrategyPosition], auto: AutoTradingConfig) -> BasketRecommendation:
    total_buy_cost = sum(position.buy_total_cost for position in positions)
    basket_profit = sum(decision.conservative_profit for decision in decisions)
    winners = sum(max(decision.conservative_profit, 0) for decision in decisions)
    losers = abs(sum(min(decision.conservative_profit, 0) for decision in decisions))
    threshold = max(total_buy_cost * auto.basket_profit_buffer_pct, auto.basket_profit_buffer_min_twd)
    loser_ratio = 0.0 if winners == 0 else losers / winners
    recommend = basket_profit > threshold
    return BasketRecommendation(
        basket_conservative_profit=basket_profit,
        threshold=threshold,
        loser_loss_ratio=loser_ratio,
        recommendation="recommend_exit" if recommend else "hold",
    )


def basket_recommendations_by_tag(
    decisions: list[SellDecision],
    positions: list[StrategyPosition],
    auto: AutoTradingConfig,
) -> dict[str, BasketRecommendation]:
    grouped_decisions: dict[str, list[SellDecision]] = {}
    grouped_positions: dict[str, list[StrategyPosition]] = {}
    for position, decision in zip(positions, decisions):
        basket_tag = normalize_basket_tag(position.basket_tag or DEFAULT_BASKET_TAG)
        grouped_positions.setdefault(basket_tag, []).append(position)
        grouped_decisions.setdefault(basket_tag, []).append(decision)
    return {
        basket_tag: basket_recommendation(grouped_decisions[basket_tag], grouped_positions[basket_tag], auto)
        for basket_tag in sorted(grouped_positions)
    }


def effective_basket_sell_signal(decision: SellDecision, basket: BasketRecommendation) -> tuple[str, str]:
    if basket.recommendation == "recommend_exit":
        if decision.sell_decision == "sell":
            return "sell", "basket_exit_threshold_passed; individual_signal=passed"
        return "sell", f"basket_exit_sells_all; individual_signal={decision.sell_decision_reason}"
    return "hold", f"basket_hold_below_threshold; individual_signal={decision.sell_decision_reason}"


def live_sell_submission_gate(
    decision: SellDecision,
    basket: BasketRecommendation,
    *,
    auto: AutoTradingConfig,
    live_sell_window: bool,
    quote_is_fresh: bool = True,
) -> SellSubmissionGate:
    if not live_sell_window:
        return SellSubmissionGate(
            allowed=False,
            status="waiting_1300",
            reason="live_sell_window_not_open",
        )
    if not quote_is_fresh:
        return SellSubmissionGate(
            allowed=False,
            status="stale_quote",
            reason="quote_stale",
        )
    if not auto.basket_recommendation_enabled:
        return SellSubmissionGate(
            allowed=False,
            status="basket_model_disabled",
            reason="basket_recommendation_disabled",
        )
    if basket.recommendation != "recommend_exit":
        return SellSubmissionGate(
            allowed=False,
            status="basket_hold",
            reason="basket_recommendation_hold",
        )
    if not auto.basket_auto_exit_enabled:
        return SellSubmissionGate(
            allowed=False,
            status="auto_exit_disabled",
            reason="basket_auto_exit_disabled",
        )
    return SellSubmissionGate(
        allowed=True,
        status="ready_to_submit",
        reason="basket_recommendation_passed",
    )
