"""
Deprecated wrapper.

We keep this module to avoid breaking old imports,
but the canonical proactive loop lives in:
    app/services/proactive_loop.py
"""
from app.services.proactive_loop import proactive_loop

__all__ = ["proactive_loop"]
