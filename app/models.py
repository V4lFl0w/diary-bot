"""
Compat module.
Prefer importing from app.models.<module> directly.
This file only re-exports the most common models to avoid breaking legacy imports.
"""
from app.models.user import User  # noqa: F401
from app.models.journal import JournalEntry  # noqa: F401
from app.models.reminder import Reminder  # noqa: F401

# If you have Base defined in app.db
from app.db import Base  # noqa: F401
