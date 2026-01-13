from __future__ import annotations

from typing import AsyncIterator

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from app.config import settings


class Base(DeclarativeBase):
    pass


def _normalize_url(url: str) -> str:
    """Приводим URL к async-драйверам по умолчанию."""
    if not url:
        return "sqlite+aiosqlite:///./dev.db"
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("sqlite:///") and "+aiosqlite" not in url:
        url = url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    return url


DATABASE_URL = _normalize_url(settings.database_url)

engine = create_async_engine(
    DATABASE_URL,
    echo=bool(getattr(settings, "sqlalchemy_echo", False)),
    pool_pre_ping=True,
    future=True,
)

async_session_maker = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


def async_session() -> AsyncSession:
    """Использование: `async with async_session() as s:`"""
    return async_session_maker()


# Для FastAPI Depends, если потребуется
async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session() as s:
        yield s


async def init_models() -> None:
    """
    Создаёт таблицы (идемпотентно).
    Импортируем модели, чтобы они оказались в Base.metadata.
    """
    try:
        # подхватываем все декларации таблиц
        from app.models import user as _user  # noqa: F401
        from app.models import payment as _payment  # noqa: F401
    except Exception:
        # если структура модулей иная — просто пропускаем
        pass

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


__all__ = [
    "Base",
    "engine",
    "async_session_maker",
    "async_session",
    "get_session",
    "init_models",
    "DATABASE_URL",
]