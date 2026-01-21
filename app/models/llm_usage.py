from __future__ import annotations

from datetime import datetime
from sqlalchemy import Integer, String, DateTime, ForeignKey, JSON, BigInteger
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base


class LLMUsage(Base):
    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    feature: Mapped[str] = mapped_column(String(50), default="assistant", index=True)  # assistant/vision/...
    model: Mapped[str] = mapped_column(String(80), default="", index=True)
    plan: Mapped[str] = mapped_column(String(20), default="basic", index=True)

    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)

    response_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)

    cost_usd_micros: Mapped[int] = mapped_column(BigInteger, default=0)  # без float
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
