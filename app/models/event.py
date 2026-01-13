# app/models/event.py
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import Integer, Text, TIMESTAMP, ForeignKey, text, DateTim, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime, timezone

from app.db import Base

if TYPE_CHECKING:
    from app.models.user import User

# вверху файла event.py
import json
from sqlalchemy.types import TypeDecorator, TEXT

class JSONText(TypeDecorator):
    impl = TEXT
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return value
        return value  # если уже строка

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        # аккуратно: если там не JSON, вернём как есть
        try:
            return json.loads(value)
        except Exception:
            return value


class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )

    # ✅ нужно для back_populates="user" в User.events
    user: Mapped[Optional["User"]] = relationship("User", back_populates="events")

    

    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )   # timestamptz
    event: Mapped[str] = mapped_column(Text, nullable=False)
    props: Mapped[dict | None] = mapped_column(JSONText, nullable=True)  # JSON string


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[int] = mapped_column(Integer, nullable=False)  # кто сделал (admin tg_id)
    name: Mapped[str] = mapped_column(Text, nullable=False)      # admin:ban и т.д.
    meta: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON string
    created_at: Mapped[str | None] = mapped_column(
        TIMESTAMP, server_default=text("CURRENT_TIMESTAMP")
    )
