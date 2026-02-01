from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

try:
    from sqlalchemy import JSON as SAJSON
    from sqlalchemy.dialects.postgresql import JSONB

    JSONType = SAJSON().with_variant(JSONB, "postgresql")
except Exception:
    from sqlalchemy import JSON as JSONType  # fallback


class ProactiveEntry(Base):
    __tablename__ = "proactive_entries"
    table_args = (UniqueConstraint("user_id", "kind", "local_date", name="ux_proactive_entry_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # morning / evening
    local_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)

    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", backref="proactive_entries")
