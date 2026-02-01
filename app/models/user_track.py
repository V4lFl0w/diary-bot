from __future__ import annotations

from typing import Optional

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.mixins import TimestampMixin

SQLITE_INT_PK = BigInteger().with_variant(Integer, "sqlite")
SQLITE_TG_ID = BigInteger().with_variant(Integer, "sqlite")


class UserTrack(TimestampMixin, Base):
    __tablename__ = "user_tracks"

    __table_args__ = (
        UniqueConstraint("user_id", "file_id", name="ux_user_tracks_user_file"),
        Index("ix_user_tracks_user_id", "user_id"),
        Index("ix_user_tracks_tg_id", "tg_id"),
    )

    id: Mapped[int] = mapped_column(
        SQLITE_INT_PK,
        primary_key=True,
        autoincrement=True,
    )

    # ✅ ВАЖНО: tg_id обязателен в твоей текущей SQLite таблице
    tg_id: Mapped[int] = mapped_column(
        SQLITE_TG_ID,
        nullable=False,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    title: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )

    file_id: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )

    user = relationship("User", lazy="selectin")

    def __repr__(self) -> str:
        return f"<UserTrack id={self.id} user_id={self.user_id} title={self.title!r}>"
