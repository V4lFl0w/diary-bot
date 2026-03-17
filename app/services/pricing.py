from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlanSpec:
    tier: str  # "basic" | "pro" | "max"
    period: str  # "trial" | "month" | "quarter" | "year"
    days: int
    usd: float
    uah: int
    stars: int


PRICE: dict[str, PlanSpec] = {
    # TRIALS 24h
    "basic_trial": PlanSpec("basic", "trial", 1, 0.00, 0, 0),
    "pro_trial": PlanSpec("pro", "trial", 1, 0.00, 0, 0),
    "max_trial": PlanSpec("max", "trial", 1, 0.00, 0, 0),
    # BASIC (База: 199 UAH / 199 Stars)
    "basic_month": PlanSpec("basic", "month", 30, 4.99, 199, 199),
    "basic_quarter": PlanSpec("basic", "quarter", 90, 13.49, 537, 537),
    "basic_year": PlanSpec("basic", "year", 365, 44.99, 1791, 1791),
    # PRO (База: 499 UAH / 449 Stars)
    "pro_month": PlanSpec("pro", "month", 30, 11.99, 499, 449),
    "pro_quarter": PlanSpec("pro", "quarter", 90, 32.39, 1347, 1212),
    "pro_year": PlanSpec("pro", "year", 365, 107.99, 4491, 4041),
    # MAX (База: 999 UAH / 999 Stars)
    "max_month": PlanSpec("max", "month", 30, 24.99, 999, 999),
    "max_quarter": PlanSpec("max", "quarter", 90, 67.49, 2697, 2697),
    "max_year": PlanSpec("max", "year", 365, 224.99, 8991, 8991),
}


def get_spec(sku: str) -> PlanSpec | None:
    return PRICE.get((sku or "").strip().lower())
