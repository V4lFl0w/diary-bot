import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base


def _normalize_url(u: str) -> str:
    if u.startswith("sqlite:///") and "+aiosqlite" not in u:
        return u.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return u


DB_URL = _normalize_url(os.getenv("DB_URL", "sqlite:///dev.db"))

engine = create_async_engine(DB_URL, future=True, echo=False)
Base = declarative_base()
async_session = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
Session = async_session


async def init_db():
    async with engine.begin() as conn:
        # создадим ORM-таблицы, если они есть
        await conn.run_sync(Base.metadata.create_all)
        # и гарантированно создадим критичные таблицы для бота
        await conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER UNIQUE,
            tz TEXT DEFAULT 'Europe/Kyiv'
        )""")
        await conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS reminder (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT,
            cron TEXT,
            next_run TEXT,
            is_active INTEGER DEFAULT 1,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )""")
