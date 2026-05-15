from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Union


def is_premium_active(user: Any) -> bool:
    """Return True if the user currently has an active premium subscription.

    Accepts either a User ORM model instance or a plain dict (as returned by
    premium.py's _fetch_user).  Logic:
      - is_premium must be truthy, AND
      - premium_until must be None (unlimited) OR in the future.
    """
    if user is None:
        return False

    if isinstance(user, dict):
        is_prem = bool(user.get("is_premium", False))
        premium_until = user.get("premium_until")
    else:
        is_prem = bool(getattr(user, "is_premium", False))
        premium_until = getattr(user, "premium_until", None)

    if not is_prem:
        return False

    if premium_until is None:
        return True

    try:
        if getattr(premium_until, "tzinfo", None) is None:
            premium_until = premium_until.replace(tzinfo=timezone.utc)
        return premium_until > datetime.now(timezone.utc)
    except Exception:
        return False


__all__ = ["is_premium_active"]
