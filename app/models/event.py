from __future__ import annotations

from typing import TYPE_CHECKING, Optional
from datetime import datetime, timezone
import json

from sqlalchemy import Integer, BigInteger, Text, TIMESTAMP, ForeignKey, text, DateTime, JSON, Identity
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator, TEXT

from app.db import Base

if TYPE_CHECKING:
    from app.models.user import User


class JSONText(TypeDecorator):
    """
    SQLite не умеет биндинг dict/list напрямую -> храним как TEXT(JSON string).
    На Postgres этот тип НЕ будет использоваться (ниже стоит with_variant(JSONB, 'postgresql')).
    """
    impl = TEXT
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        # сюда попадём только на sqlite
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return value  # если уже строка

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, str):
            try:
                return json.loads(value)
            except Exception:
                return value
        return value


class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)

    user: Mapped[Optional["User"]] = relationship("User", back_populates="events")

    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    event: Mapped[str] = mapped_column(Text, nullable=False)

    # ✅ sqlite: TEXT(JSON string)  |  postgres: JSONB (и будет биндинг dict, не VARCHAR)
    props: Mapped[dict | None] = mapped_column(
        JSONText().with_variant(JSONB, "postgresql"),
        nullable=True,
    )


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    tg_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str | None] = mapped_column(
        TIMESTAMP, server_default=text("CURRENT_TIMESTAMP")
    )