from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, func
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class BugReport(Base):
    __tablename__ = "bug_reports"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    text: Mapped[str] = mapped_column(Text, nullable=False)

    # new / in_progress / done / rejected
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=sql_text("'new'"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_bug_reports_user_created", "user_id", "created_at"),
        Index("ix_bug_reports_status_created", "status", "created_at"),
    )
