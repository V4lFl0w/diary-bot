import re

from aiogram.filters import BaseFilter
from aiogram.types import Message

from app.i18n import t

_rx = re.compile(r"[^a-zA-Zа-яА-ЯёЁіІїЇєЄґҐ0-9]+", re.U)


def _norm(x: str) -> str:
    return _rx.sub("", (x or "").strip().lower())


class Btn(BaseFilter):
    def __init__(self, key: str):
        self.key = key

    async def __call__(self, message: Message) -> bool:
        txt = _norm(getattr(message, "text", "") or "")
        if not txt:
            return False
        variants = {
            _norm(t(self.key, "ru")),
            _norm(t(self.key, "uk")),
            _norm(t(self.key, "en")),
        }
        if self.key in {"btn_premium", "menu_premium"}:
            variants |= {"premium", "премиум", "преміум"}
            if (message.text or "").lstrip().startswith("/premium"):
                return True
        return txt in variants
