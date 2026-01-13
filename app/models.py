from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import Integer, String, ForeignKey, Index
class Base(DeclarativeBase):
    pass
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg_id: Mapped[int] = mapped_column(Integer, unique=True)
    tz: Mapped[str] = mapped_column(String, default="Europe/Kyiv")
    reminders: Mapped[list["Reminder"]] = relationship(back_populates="user", cascade="all, delete-orphan")
class Reminder(Base):
    __tablename__ = "reminder"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str | None] = mapped_column(String)
    cron: Mapped[str | None] = mapped_column(String)
    next_run: Mapped[str | None] = mapped_column(String)
    is_active: Mapped[int] = mapped_column(Integer, default=1)
    user: Mapped["User"] = relationship(back_populates="reminders")
Index("ix_reminder_user_next", Reminder.user_id, Reminder.next_run)
