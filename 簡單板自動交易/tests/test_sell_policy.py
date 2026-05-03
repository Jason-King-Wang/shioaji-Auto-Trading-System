from __future__ import annotations

import unittest

from sinopac_auto_trading.config import AutoTradingConfig, FeeConfig
from sinopac_auto_trading.sell_policy import (
    BasketRecommendation,
    SellDecision,
    StrategyPosition,
    SellQuote,
    basket_recommendation,
    basket_recommendations_by_tag,
    effective_basket_sell_signal,
    evaluate_sell_decision,
    live_sell_submission_gate,
)


class SellPolicyTests(unittest.TestCase):
    def test_small_profit_below_buffer_holds(self) -> None:
        position = StrategyPosition("lot-1", "2330", "TSMC", 1, 100.0, 100.0)
        decision = evaluate_sell_decision(
            position,
            SellQuote(last_price=101.0, bid1=101.0, ask1=101.5),
            fees=FeeConfig(minimum_commission=0),
            auto=AutoTradingConfig(weekly_budget=100000, overrun_tolerance=50000),
        )
        self.assertEqual(decision.sell_decision, "hold")

    def test_profit_above_threshold_sells(self) -> None:
        position = StrategyPosition("lot-1", "2330", "TSMC", 10, 100.0, 1000.0)
        decision = evaluate_sell_decision(
            position,
            SellQuote(last_price=120.0, bid1=120.0, ask1=120.5),
            fees=FeeConfig(minimum_commission=0),
            auto=AutoTradingConfig(weekly_budget=100000, overrun_tolerance=50000),
        )
        self.assertEqual(decision.sell_decision, "sell")

    def test_basket_recommendation_is_calculated(self) -> None:
        auto = AutoTradingConfig(weekly_budget=100000, overrun_tolerance=50000)
        positions = [
            StrategyPosition("lot-1", "2330", "TSMC", 1, 100.0, 100.0),
            StrategyPosition("lot-2", "2454", "MediaTek", 1, 100.0, 100.0),
        ]
        decisions = [
            evaluate_sell_decision(
                positions[0],
                SellQuote(last_price=130.0, bid1=130.0, ask1=130.5),
                fees=FeeConfig(minimum_commission=0),
                auto=auto,
            ),
            evaluate_sell_decision(
                positions[1],
                SellQuote(last_price=95.0, bid1=95.0, ask1=95.5),
                fees=FeeConfig(minimum_commission=0),
                auto=auto,
            ),
        ]
        basket = basket_recommendation(decisions, positions, auto)
        self.assertIn(basket.recommendation, {"hold", "recommend_exit"})

    def test_basket_recommendation_uses_total_profit_threshold_only(self) -> None:
        auto = AutoTradingConfig(
            weekly_budget=100000,
            overrun_tolerance=50000,
            basket_profit_buffer_pct=0.008,
            basket_profit_buffer_min_twd=0.0,
            max_loser_loss_ratio_to_winner_profit=0.35,
        )
        positions = [
            StrategyPosition("lot-1", "2330", "TSMC", 10, 100.0, 1000.0),
            StrategyPosition("lot-2", "2454", "MediaTek", 10, 100.0, 1000.0),
        ]
        decisions = [
            SellDecision("2330", "TSMC", True, "sell", "passed conservative threshold", 120.0, 200.0, 1200.0),
            SellDecision("2454", "MediaTek", False, "hold", "below conservative threshold", 90.0, -100.0, 900.0),
        ]
        basket = basket_recommendation(decisions, positions, auto)
        self.assertEqual(basket.threshold, 16.0)
        self.assertGreater(basket.loser_loss_ratio, auto.max_loser_loss_ratio_to_winner_profit)
        self.assertEqual(basket.recommendation, "recommend_exit")

    def test_basket_recommendations_are_split_by_basket_tag(self) -> None:
        auto = AutoTradingConfig(
            weekly_budget=100000,
            overrun_tolerance=50000,
            basket_profit_buffer_min_twd=50.0,
            per_stock_profit_buffer_min_twd=10.0,
        )
        fees = FeeConfig(minimum_commission=0)
        positions = [
            StrategyPosition("lot-main", "2330", "TSMC", 10, 100.0, 1000.0, basket_tag="main"),
            StrategyPosition("lot-add", "2454", "MediaTek", 10, 100.0, 1000.0, basket_tag="secondary_add"),
        ]
        decisions = [
            evaluate_sell_decision(
                positions[0],
                SellQuote(last_price=120.0, bid1=120.0, ask1=120.5),
                fees=fees,
                auto=auto,
            ),
            evaluate_sell_decision(
                positions[1],
                SellQuote(last_price=101.0, bid1=101.0, ask1=101.5),
                fees=fees,
                auto=auto,
            ),
        ]
        grouped = basket_recommendations_by_tag(decisions, positions, auto)
        self.assertEqual(grouped["main"].recommendation, "recommend_exit")
        self.assertEqual(grouped["secondary_add"].recommendation, "hold")

    def test_effective_basket_sell_signal_sells_loser_when_basket_exits(self) -> None:
        decision = SellDecision(
            "2454",
            "MediaTek",
            False,
            "hold",
            "below conservative threshold",
            90.0,
            -100.0,
            900.0,
        )
        effective_decision, reason = effective_basket_sell_signal(
            decision,
            BasketRecommendation(
                basket_conservative_profit=100.0,
                threshold=16.0,
                loser_loss_ratio=0.5,
                recommendation="recommend_exit",
            ),
        )
        self.assertEqual(effective_decision, "sell")
        self.assertIn("basket_exit_sells_all", reason)

    def test_live_sell_gate_blocks_when_basket_recommendation_is_hold(self) -> None:
        decision = evaluate_sell_decision(
            StrategyPosition("lot-1", "2330", "TSMC", 10, 100.0, 1000.0),
            SellQuote(last_price=120.0, bid1=120.0, ask1=120.5),
            fees=FeeConfig(minimum_commission=0),
            auto=AutoTradingConfig(weekly_budget=100000, overrun_tolerance=50000),
        )
        gate = live_sell_submission_gate(
            decision,
            BasketRecommendation(
                basket_conservative_profit=500.0,
                threshold=3000.0,
                loser_loss_ratio=0.0,
                recommendation="hold",
            ),
            auto=AutoTradingConfig(weekly_budget=100000, overrun_tolerance=50000),
            live_sell_window=True,
        )
        self.assertFalse(gate.allowed)
        self.assertEqual(gate.status, "basket_hold")

    def test_live_sell_gate_allows_individual_hold_when_basket_exits(self) -> None:
        decision = SellDecision(
            "2454",
            "MediaTek",
            False,
            "hold",
            "below conservative threshold",
            90.0,
            -100.0,
            900.0,
        )
        gate = live_sell_submission_gate(
            decision,
            BasketRecommendation(
                basket_conservative_profit=100.0,
                threshold=16.0,
                loser_loss_ratio=0.5,
                recommendation="recommend_exit",
            ),
            auto=AutoTradingConfig(
                weekly_budget=100000,
                overrun_tolerance=50000,
                basket_auto_exit_enabled=True,
                basket_recommendation_enabled=True,
            ),
            live_sell_window=True,
        )
        self.assertTrue(gate.allowed)
        self.assertEqual(gate.status, "ready_to_submit")

    def test_live_sell_gate_allows_submission_only_after_basket_exit_signal(self) -> None:
        decision = evaluate_sell_decision(
            StrategyPosition("lot-1", "2330", "TSMC", 10, 100.0, 1000.0),
            SellQuote(last_price=120.0, bid1=120.0, ask1=120.5),
            fees=FeeConfig(minimum_commission=0),
            auto=AutoTradingConfig(weekly_budget=100000, overrun_tolerance=50000),
        )
        gate = live_sell_submission_gate(
            decision,
            BasketRecommendation(
                basket_conservative_profit=4000.0,
                threshold=3000.0,
                loser_loss_ratio=0.1,
                recommendation="recommend_exit",
            ),
            auto=AutoTradingConfig(
                weekly_budget=100000,
                overrun_tolerance=50000,
                basket_auto_exit_enabled=True,
                basket_recommendation_enabled=True,
            ),
            live_sell_window=True,
        )
        self.assertTrue(gate.allowed)
        self.assertEqual(gate.status, "ready_to_submit")

    def test_live_sell_gate_blocks_when_basket_model_is_disabled(self) -> None:
        decision = evaluate_sell_decision(
            StrategyPosition("lot-1", "2330", "TSMC", 10, 100.0, 1000.0),
            SellQuote(last_price=120.0, bid1=120.0, ask1=120.5),
            fees=FeeConfig(minimum_commission=0),
            auto=AutoTradingConfig(weekly_budget=100000, overrun_tolerance=50000),
        )
        gate = live_sell_submission_gate(
            decision,
            BasketRecommendation(
                basket_conservative_profit=4000.0,
                threshold=3000.0,
                loser_loss_ratio=0.1,
                recommendation="recommend_exit",
            ),
            auto=AutoTradingConfig(
                weekly_budget=100000,
                overrun_tolerance=50000,
                basket_auto_exit_enabled=True,
                basket_recommendation_enabled=False,
            ),
            live_sell_window=True,
        )
        self.assertFalse(gate.allowed)
        self.assertEqual(gate.status, "basket_model_disabled")

    def test_live_sell_gate_blocks_when_auto_exit_is_disabled(self) -> None:
        decision = evaluate_sell_decision(
            StrategyPosition("lot-1", "2330", "TSMC", 10, 100.0, 1000.0),
            SellQuote(last_price=120.0, bid1=120.0, ask1=120.5),
            fees=FeeConfig(minimum_commission=0),
            auto=AutoTradingConfig(weekly_budget=100000, overrun_tolerance=50000),
        )
        gate = live_sell_submission_gate(
            decision,
            BasketRecommendation(
                basket_conservative_profit=4000.0,
                threshold=3000.0,
                loser_loss_ratio=0.1,
                recommendation="recommend_exit",
            ),
            auto=AutoTradingConfig(
                weekly_budget=100000,
                overrun_tolerance=50000,
                basket_auto_exit_enabled=False,
                basket_recommendation_enabled=True,
            ),
            live_sell_window=True,
        )
        self.assertFalse(gate.allowed)
        self.assertEqual(gate.status, "auto_exit_disabled")

    def test_live_sell_gate_blocks_when_quote_is_stale(self) -> None:
        decision = evaluate_sell_decision(
            StrategyPosition("lot-1", "2330", "TSMC", 10, 100.0, 1000.0),
            SellQuote(last_price=120.0, bid1=120.0, ask1=120.5),
            fees=FeeConfig(minimum_commission=0),
            auto=AutoTradingConfig(weekly_budget=100000, overrun_tolerance=50000),
        )
        gate = live_sell_submission_gate(
            decision,
            BasketRecommendation(
                basket_conservative_profit=4000.0,
                threshold=3000.0,
                loser_loss_ratio=0.1,
                recommendation="recommend_exit",
            ),
            auto=AutoTradingConfig(
                weekly_budget=100000,
                overrun_tolerance=50000,
                basket_auto_exit_enabled=True,
                basket_recommendation_enabled=True,
            ),
            live_sell_window=True,
            quote_is_fresh=False,
        )
        self.assertFalse(gate.allowed)
        self.assertEqual(gate.status, "stale_quote")


if __name__ == "__main__":
    unittest.main()
