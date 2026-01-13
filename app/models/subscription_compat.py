"""
Backward-compat: старые импорты, которые ожидали Payment из subscription.

Раньше было:
    from app.models.subscription import Payment

Теперь нужно:
    from app.models.subscription_compat import Payment
"""

from app.models.payment import Payment, PaymentStatus

__all__ = ["Payment", "PaymentStatus"]