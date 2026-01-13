from __future__ import annotations

import os
from typing import Optional, Set

from app.config import settings
from app.models.user import User


def _parse_admin_ids(raw: str) -> Set[int]:
    if not raw:
        return set()
    ids: Set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            continue
    return ids


def is_admin_user(tg_id: int, user: Optional[User] = None) -> bool:
    """
    Проверка админа в порядке приоритета:
    1) флаг в БД (user.is_admin)
    2) одиночный ID из settings.bot_admin_tg_id
    3) список ID из ENV ADMIN_IDS (через запятую)
    """
    # 1) DB flag
    if user is not None and bool(getattr(user, "is_admin", False)):
        return True

    # 2) single admin id from settings
    try:
        admin_id = getattr(settings, "bot_admin_tg_id", None)
        if admin_id is not None and int(admin_id) == int(tg_id):
            return True
    except Exception:
        # не валим проверку из-за кривого конфига
        pass

    # 3) env list
    try:
        admin_ids = _parse_admin_ids(os.getenv("ADMIN_IDS", ""))
        return int(tg_id) in admin_ids
    except Exception:
        return False