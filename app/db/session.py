import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./db.sqlite3")


class Base(DeclarativeBase):
    pass


engine = create_async_engine(DATABASE_URL, echo=False, future=True)
async_session = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
