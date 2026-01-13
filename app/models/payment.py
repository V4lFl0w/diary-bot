from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    ForeignKey,
    String,
    Integer,
    Text,
    TIMESTAMP,
    CheckConstraint,
    Index,
    text,
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.mixins import TimestampMixin


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    REFUNDED = "refunded"
    FAILED = "failed"


class PaymentProvider(str, enum.Enum):
    MONO = "mono"
    CRYPTO = "crypto"
    STARS = "stars"
    TEST = "test"


class PaymentPlan(str, enum.Enum):
    TRIAL = "trial"
    MONTH = "month"
    YEAR = "year"
    LIFETIME = "lifetime"
    TOPUP = "topup"


class Payment(TimestampMixin, Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ✅ валидируем на уровне SQLAlchemy (в БД может быть CHECK/enum, зависит от миграций)
    provider: Mapped[PaymentProvider] = mapped_column(
        SAEnum(PaymentProvider, name="payment_provider"),
        nullable=False,
    )
    plan: Mapped[PaymentPlan] = mapped_column(
        SAEnum(PaymentPlan, name="payment_plan"),
        nullable=False,
    )
    status: Mapped[PaymentStatus] = mapped_column(
        SAEnum(PaymentStatus, name="payment_status"),
        nullable=False,
        default=PaymentStatus.PENDING,
    )

    # сумма: для фиата — cents, для Stars (XTR) — количество Stars (units)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")

    external_id: Mapped[Optional[str]] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )

    payload: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ✅ единое поле времени успешной оплаты
    paid_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint("amount_cents >= 0", name="ck_payments_amount_nonneg"),
        Index("ix_payments_provider_status", "provider", "status"),
        # ✅ unique по external_id только если не NULL — работает и в Postgres, и в SQLite
        Index(
            "uq_payments_external_id",
            "external_id",
            unique=True,
            sqlite_where=text("external_id IS NOT NULL"),
            postgresql_where=text("external_id IS NOT NULL"),
        ),
    )

    # ---------- хелперы ----------

    @property
    def amount(self) -> float:
        """
        Если это Stars (XTR) — amount_cents хранит КОЛИЧЕСТВО Stars (units), не cents.
        Если это фиат — amount_cents это cents.
        """
        if (self.currency or "").upper() == "XTR":
            return float(self.amount_cents or 0)
        return (self.amount_cents or 0) / 100

    @property
    def is_paid(self) -> bool:
        return self.status == PaymentStatus.PAID

    @property
    def is_pending(self) -> bool:
        return self.status == PaymentStatus.PENDING

    @property
    def is_failed(self) -> bool:
        return self.status == PaymentStatus.FAILED

    @property
    def is_refunded(self) -> bool:
        return self.status == PaymentStatus.REFUNDED

    @property
    def is_stars(self) -> bool:
        return self.provider == PaymentProvider.STARS

    @property
    def is_subscription(self) -> bool:
        return self.plan in {
            PaymentPlan.MONTH,
            PaymentPlan.YEAR,
            PaymentPlan.LIFETIME,
            PaymentPlan.TRIAL,
        }

    @property
    def is_topup(self) -> bool:
        return self.plan == PaymentPlan.TOPUP

    def mark_paid(self, *, paid_at: Optional[datetime] = None) -> None:
        self.status = PaymentStatus.PAID
        self.paid_at = paid_at or datetime.now(timezone.utc)

    def mark_refunded(self) -> None:
        from datetime import datetime, timezone
        self.status = PaymentStatus.REFUNDED
        if hasattr(self, "refunded_at"):
            self.refunded_at = datetime.now(timezone.utc)
        if hasattr(self, "refund_status"):
            self.refund_status = "approved"

    def mark_failed(self) -> None:
        self.status = PaymentStatus.FAILED

    # ---------- фабрики ----------

    @classmethod
    def create_stars_subscription(
        cls,
        *,
        user_id: Optional[int],
        plan: PaymentPlan,
        stars_amount: int,
        tx_id: Optional[str],
        currency: str = "XTR",
    ) -> "Payment":
        return cls(
            user_id=user_id,
            provider=PaymentProvider.STARS,
            plan=plan,
            amount_cents=stars_amount,  # units
            currency=currency,
            status=PaymentStatus.PENDING,
            external_id=tx_id,
        )

    @classmethod
    def create_mono_subscription(
        cls,
        *,
        user_id: Optional[int],
        plan: PaymentPlan,
        amount_cents: int,
        currency: str = "UAH",
        external_id: Optional[str] = None,
    ) -> "Payment":
        return cls(
            user_id=user_id,
            provider=PaymentProvider.MONO,
            plan=plan,
            amount_cents=amount_cents,
            currency=currency,
            status=PaymentStatus.PENDING,
            external_id=external_id,
        )

    def __repr__(self) -> str:
        return (
            f"<Payment id={self.id} user_id={self.user_id} "
            f"{self.provider.value} {self.status.value} {self.amount}{self.currency}>"
        )