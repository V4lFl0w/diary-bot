from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class PlanSpec:
    tier: str          # "basic" | "pro"
    period: str        # "trial" | "month" | "quarter" | "year"
    days: int
    usd: float
    uah: int
    stars: int

# твои месячные цены (USD)
BASIC_M_USD = 5.73
PRO_M_USD   = 10.99

# скидки (рационально)
Q_DISC = 0.15   # -15% на квартал (3 месяца)
Y_DISC = 0.30   # -30% на год (12 месяцев)

def _q(price_m: float) -> float:
    return round(price_m * 3 * (1 - Q_DISC), 2)

def _y(price_m: float) -> float:
    return round(price_m * 12 * (1 - Y_DISC), 2)

# Mono (UAH) — поставь свои реальные значения, если уже решил прайс
# сейчас оставил 0, чтобы не ломать, но чтобы таблица была готова
# Stars пакеты — поставил дефолтные, потом подстроим под экономику
PRICE: dict[str, PlanSpec] = {
    # trials 24h
    "basic_trial":   PlanSpec("basic", "trial",   1, 0.00, 0, 0),
    "pro_trial":     PlanSpec("pro",   "trial",   1, 0.00, 0, 0),

    # BASIC
    "basic_month":   PlanSpec("basic", "month",   30, BASIC_M_USD, 0, 299),
    "basic_quarter": PlanSpec("basic", "quarter", 90, _q(BASIC_M_USD), 0, 799),
    "basic_year":    PlanSpec("basic", "year",   365, _y(BASIC_M_USD), 0, 2499),

    # PRO
    "pro_month":     PlanSpec("pro",   "month",   30, PRO_M_USD, 0, 499),
    "pro_quarter":   PlanSpec("pro",   "quarter", 90, _q(PRO_M_USD), 0, 1299),
    "pro_year":      PlanSpec("pro",   "year",   365, _y(PRO_M_USD), 0, 3999),
}

def get_spec(sku: str) -> PlanSpec | None:
    return PRICE.get((sku or "").strip().lower())
