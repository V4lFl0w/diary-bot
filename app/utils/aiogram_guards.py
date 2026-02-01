from __future__ import annotations

from typing import Any, Optional, Protocol, TypeGuard, runtime_checkable, cast

# ---------- runtime classes for isinstance (must be real "type") ----------

try:
    from aiogram.types import Message as _AiogramMessage  # runtime class

    _MESSAGE_CLS: type[Any] = _AiogramMessage
except Exception:  # pragma: no cover

    class _FallbackMessage:
        pass

    _MESSAGE_CLS: type[Any] = _FallbackMessage


try:
    from aiogram.types import (
        InaccessibleMessage as _AiogramInaccessibleMessage,
    )  # runtime class

    _INACCESSIBLE_CLS: type[Any] = _AiogramInaccessibleMessage
except Exception:  # pragma: no cover
    try:
        from aiogram.types.inaccessible_message import (  # type: ignore
            InaccessibleMessage as _AiogramInaccessibleMessage,
        )

        _INACCESSIBLE_CLS: type[Any] = _AiogramInaccessibleMessage
    except Exception:  # pragma: no cover

        class _FallbackInaccessibleMessage:
            pass

        _INACCESSIBLE_CLS: type[Any] = _FallbackInaccessibleMessage


# ---------- structural types (Protocols) ----------


@runtime_checkable
class _ChatLike(Protocol):
    id: int


@runtime_checkable
class MessageLike(Protocol):
    chat: _ChatLike
    message_id: int

    async def edit_text(self, text: str, *, reply_markup: Any = None, **kwargs: Any) -> Any: ...
    async def delete(self) -> Any: ...


@runtime_checkable
class InaccessibleMessageLike(Protocol):
    chat_id: int
    message_id: int


@runtime_checkable
class BotLike(Protocol):
    async def send_message(self, chat_id: int, text: str, *, reply_markup: Any = None, **kwargs: Any) -> Any: ...
    async def edit_message_text(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: Any = None,
        **kwargs: Any,
    ) -> Any: ...
    async def delete_message(self, *, chat_id: int, message_id: int) -> Any: ...


# ---------- type guards ----------


def is_message(m: Any) -> TypeGuard[MessageLike]:
    return isinstance(m, _MESSAGE_CLS)


def is_inaccessible(m: Any) -> bool:
    return isinstance(m, _INACCESSIBLE_CLS)


def require_message(m: Any, *, context: str = "") -> MessageLike:
    if not is_message(m):
        raise RuntimeError(f"CallbackQuery message is not accessible{': ' + context if context else ''}")
    return cast(MessageLike, m)


def require_bot(b: Any, *, context: str = "") -> BotLike:
    if b is None:
        raise RuntimeError(f"Bot is None{': ' + context if context else ''}")
    return cast(BotLike, b)


# ---------- id helpers (Message / InaccessibleMessage / alike) ----------


def safe_chat_id(obj: Any) -> int:
    """
    Works for Message-like and InaccessibleMessage-like objects.
    Raises ValueError if chat id can't be resolved.
    """
    chat = getattr(obj, "chat", None)
    cid = getattr(chat, "id", None)
    if cid is None:
        cid = getattr(obj, "chat_id", None)
    if cid is None:
        raise ValueError("Cannot resolve chat_id from message-like object")
    return int(cid)


def safe_message_id(obj: Any) -> int:
    mid = getattr(obj, "message_id", None)
    if mid is None:
        raise ValueError("Cannot resolve message_id from message-like object")
    return int(mid)


# ---------- callback-safe helpers ----------


async def cb_reply(cb: Any, text: str, *, reply_markup: Any = None, **kwargs: Any) -> None:
    """
    Safe alternative for: await cb.message.answer(...)
    If cb.message is missing, sends to cb.from_user.id.
    """
    m = getattr(cb, "message", None)

    chat_id: Optional[int] = None
    if m is not None:
        try:
            chat_id = safe_chat_id(m)
        except Exception:
            chat_id = None

    if chat_id is None:
        chat_id = int(getattr(getattr(cb, "from_user", None), "id", 0) or 0)

    if not chat_id:
        return

    bot = getattr(cb, "bot", None)
    if bot is None:
        return

    await cast(BotLike, bot).send_message(chat_id, text, reply_markup=reply_markup, **kwargs)


async def cb_edit(cb: Any, text: str, *, reply_markup: Any = None, **kwargs: Any) -> None:
    """
    Safe alternative for: await cb.message.edit_text(...)
    If can't edit (no message / inaccessible), falls back to sending a new message.
    """
    bot = getattr(cb, "bot", None)
    if bot is None:
        return
    bot2 = cast(BotLike, bot)

    m = getattr(cb, "message", None)
    if m is None:
        await cb_reply(cb, text, reply_markup=reply_markup, **kwargs)
        return

    # Accessible Message: can edit directly
    if is_message(m):
        try:
            await cast(MessageLike, m).edit_text(text, reply_markup=reply_markup, **kwargs)
            return
        except Exception:
            await cb_reply(cb, text, reply_markup=reply_markup, **kwargs)
            return

    # InaccessibleMessage-like: edit via bot.* with ids
    try:
        await bot2.edit_message_text(
            chat_id=safe_chat_id(m),
            message_id=safe_message_id(m),
            text=text,
            reply_markup=reply_markup,
            **kwargs,
        )
    except Exception:
        await cb_reply(cb, text, reply_markup=reply_markup, **kwargs)


async def cb_delete(cb: Any) -> None:
    """
    Safe alternative for: await cb.message.delete()
    """
    bot = getattr(cb, "bot", None)
    if bot is None:
        return
    bot2 = cast(BotLike, bot)

    m = getattr(cb, "message", None)
    if m is None:
        return

    if is_message(m):
        try:
            await cast(MessageLike, m).delete()
        except Exception:
            pass
        return

    try:
        await bot2.delete_message(chat_id=safe_chat_id(m), message_id=safe_message_id(m))
    except Exception:
        pass
