from __future__ import annotations

from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import BigInteger, Integer, String, DateTime, UniqueConstraint

from app.db import Base


class QuotaUsage(Base):
    """
    Usage по feature, храним "units" (НЕ токены), чтобы лимитить внешние API и дорогие шаги.
    bucket_date используется как месячный бакет: YYYY-MM (UTC), чтобы одинаково работало в Postgres и SQLite.
    """

    __tablename__ = "quota_usage"
    __table_args__ = (UniqueConstraint("user_id", "feature", "bucket_date", name="uq_quota_usage_user_feature_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    feature: Mapped[str] = mapped_column(String(64), index=True)
    bucket_date: Mapped[str] = mapped_column(String(10), index=True)  # "2026-02"
    used_units: Mapped[int] = mapped_column(Integer, default=0)

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
