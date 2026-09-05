"""
ScenarioSpec: the parameters controlling one synthetic dataset generation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time


@dataclass(frozen=True)
class ScenarioSpec:
    start_date: date
    days: int
    orders_per_day: tuple[int, int]      # (min, max) inclusive, uniform
    method_mix: dict[str, float]         # e.g. {'upi': 0.6, 'card': 0.35, 'amex': 0.05}
    rate_card_bps: dict[str, int]        # e.g. {'upi': 0, 'card': 200, 'amex': 300}
    failure_rate: float = 0.03           # fraction of orders that fail at capture
    refund_rate: float = 0.02            # fraction of captured payments refunded
    dispute_rate: float = 0.01           # fraction of captured payments disputed
    dispute_fee: int = 10_000            # paise, flat -- Rs 100 [VERIFY real fee]
    t_plus: int = 2                      # settlement cycle
    cutoff: time = time(23, 0)
    seed: int = 42                       # ALWAYS set -- reproducibility is not optional

    def __post_init__(self) -> None:
        total = sum(self.method_mix.values())
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"method_mix must sum to 1.0, got {total}")
        missing = set(self.method_mix) - set(self.rate_card_bps)
        if missing:
            raise ValueError(f"rate_card_bps missing methods present in method_mix: {missing}")
        if self.orders_per_day[0] > self.orders_per_day[1]:
            raise ValueError("orders_per_day min must be <= max")
        for rate_name, rate_value in (
            ("failure_rate", self.failure_rate),
            ("refund_rate", self.refund_rate),
            ("dispute_rate", self.dispute_rate),
        ):
            if not (0.0 <= rate_value <= 1.0):
                raise ValueError(f"{rate_name} must be between 0 and 1, got {rate_value}")


def default_spec(seed: int = 42) -> ScenarioSpec:
    """A reasonable small scenario for tests and quick runs."""
    return ScenarioSpec(
        start_date=date(2026, 2, 2),   # a Monday
        days=5,
        orders_per_day=(15, 25),
        method_mix={"upi": 0.6, "card": 0.35, "amex": 0.05},
        rate_card_bps={"upi": 0, "card": 200, "amex": 300},
        seed=seed,
    )
