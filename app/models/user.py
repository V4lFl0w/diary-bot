from __future__ import annotations

from datetime import time, datetime, timezone
from typing import Optional, List, TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, String, Time, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Text  # если ещё нет

from app.db import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.event import AnalyticsEvent

from app.models.event import AnalyticsEvent


# Более дружелюбный PK для SQLite/Postgres
SQLITE_INT_PK = BigInteger().with_variant(Integer, "sqlite")


class User(TimestampMixin, Base):
    """
    Каноничная модель пользователя для всего проекта:

    - locale/lang/language для i18n совместимости
    - policy_accepted/consent_accepted_at для privacy-гейта
    - is_premium/premium_until/premium_trial_given для премиума
    - morning_auto/morning_time для утреннего ассистента
    - events — связь с аналитикой

    ВАЖНО:
    - Используй user.has_premium как ЕДИНЫЙ источник правды о премиуме.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        SQLITE_INT_PK,
        primary_key=True,
        autoincrement=True,
    )

    tg_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        index=True,
        nullable=False,
    )

    # -------------------- telegram profile --------------------

    username: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )

    first_name: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
    )

    last_name: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
    )

    # -------------------- moderation --------------------

    is_banned: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        index=True,
    )

    # -------------------- activity --------------------

    last_seen_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    assistant_prev_response_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    assistant_last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    assistant_profile_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    assistant_profile_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # -------------------- i18n --------------------

    locale: Mapped[str] = mapped_column(
        String(8),
        default="ru",
        nullable=False,
    )

    lang: Mapped[Optional[str]] = mapped_column(
        String(8),
        nullable=True,
    )

    tz: Mapped[str] = mapped_column(
        String(64),
        default="Europe/Kyiv",
        nullable=False,
    )

    # -------------------- privacy / consent --------------------

    policy_accepted: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    consent_accepted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # -------------------- admin (optional) --------------------

    is_admin: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    # -------------------- premium core --------------------

    is_premium: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    premium_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    premium_trial_given: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    # -------------------- premium tiers & trials --------------------
    # tier: basic/pro (для ассистента и фич)
    premium_plan: Mapped[str] = mapped_column(
        String(16),
        default="basic",
        nullable=False,
    )

    basic_trial_given: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    pro_trial_given: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    # -------------------- morning assistant --------------------

    morning_auto: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    morning_time: Mapped[time] = mapped_column(
        Time(timezone=False),
        default=time(9, 30),
        nullable=False,
    )

    morning_last_sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # -------------------- evening assistant --------------------

    evening_auto: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    evening_time: Mapped[time] = mapped_column(
        Time(timezone=False),
        default=time(21, 30),
        nullable=False,
    )

    evening_last_sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # ----- proactive assistant -----

    evening_auto: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    evening_time: Mapped[Optional[time]] = mapped_column(
        Time(timezone=False),
        default=time(21, 30),
        nullable=True,
    )

    morning_last_sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    evening_last_sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # -------------------- relations --------------------

    events: Mapped[List["AnalyticsEvent"]] = relationship(
        "AnalyticsEvent",
        back_populates="user",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    # -------------------- compatibility helpers --------------------

    @property
    def language(self) -> str:
        return self.locale

    @language.setter
    def language(self, v: str) -> None:
        v = (v or "ru").strip().lower()
        self.locale = v
        self.lang = v

    @property
    def premium_trial_granted(self) -> bool:
        return bool(self.premium_trial_given)

    @premium_trial_granted.setter
    def premium_trial_granted(self, v: bool) -> None:
        self.premium_trial_given = bool(v)

    @property
    def has_premium(self) -> bool:
        try:
            if bool(self.is_premium):
                return True
        except Exception:
            pass

        try:
            if self.premium_until is None:
                return False

            now = datetime.now(timezone.utc)

            pu = self.premium_until
            if pu.tzinfo is None:
                pu = pu.replace(tzinfo=timezone.utc)

            return pu > now
        except Exception:
            return False

    @property
    def is_premium_active(self) -> bool:
        return self.has_premium

    def __repr__(self) -> str:
        premium_flag = "1" if self.has_premium else "0"
        return (
            f"<User id={self.id} tg_id={self.tg_id} "
            f"premium={premium_flag} policy={self.policy_accepted}>"
        )

__all__ = ["User"]
