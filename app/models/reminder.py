from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import text

from app.db import Base
from app.models.mixins import TimestampMixin


class Reminder(TimestampMixin, Base):
    __tablename__ = "reminder"  # оставляем в ед. числе, как в БД

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Короткий текст/название напоминания
    title: Mapped[str] = mapped_column(Text, nullable=False)

    # Либо cron, либо одиночный next_run
    cron: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Следующий запуск (UTC, aware)
    next_run: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Активно ли напоминание
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )

    __table_args__ = (
        Index("ix_reminder_due", "is_active", "next_run"),
    )

    def __repr__(self) -> str:
        return (
            f"<Reminder id={self.id} user_id={self.user_id} "
            f"title={self.title!r} active={self.is_active} next_run={self.next_run}>"
        )