"""
Compat module.
Do NOT create engines/sessions here.
Re-export everything from app.db (single source of truth).
"""
from . import *  # noqa: F401,F403
