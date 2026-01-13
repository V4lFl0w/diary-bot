import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    pass


def _pick_db_url() -> str:
    env = (getattr(settings, "environment", None) or os.getenv("ENV") or "dev").strip().lower()
    env_key = env.upper()

    candidates = [
        os.getenv(f"DATABASE_URL_{env_key}"),
        os.getenv(f"DB_URL_{env_key}"),
        os.getenv(f"DB_URI_{env_key}"),

        os.getenv("DATABASE_URL"),
        os.getenv("DB_URL"),
        os.getenv("DB_URI"),

        getattr(settings, "database_url", "") if hasattr(settings, "database_url") else "",
    ]

    for c in candidates:
        if c and str(c).strip():
            return str(c).strip()

    if env == "prod":
        return "sqlite+aiosqlite:///app/prod.db"
    return "sqlite+aiosqlite:///app/dev.db"


DATABASE_URL = _pick_db_url()

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
)

async_session = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

__all__ = ["Base", "engine", "async_session", "DATABASE_URL"]
