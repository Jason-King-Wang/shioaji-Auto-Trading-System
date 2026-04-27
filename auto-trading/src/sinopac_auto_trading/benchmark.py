from __future__ import annotations

from dataclasses import dataclass


def normalized_return(base_value: float, current_value: float) -> float:
    if base_value == 0:
        return 0.0
    return current_value / base_value - 1.0


@dataclass(slots=True)
class BenchmarkSnapshot:
    strategy_equity: float
    strategy_capital_deployed: float
    twii_base: float
    twii_current: float
    tsmc_base: float
    tsmc_current: float

    @property
    def strategy_return(self) -> float:
        return normalized_return(self.strategy_capital_deployed or 1.0, self.strategy_equity)

    @property
    def twii_return(self) -> float:
        return normalized_return(self.twii_base, self.twii_current)

    @property
    def tsmc_return(self) -> float:
        return normalized_return(self.tsmc_base, self.tsmc_current)
