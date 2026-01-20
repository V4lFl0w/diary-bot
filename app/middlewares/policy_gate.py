from __future__ import annotations

from typing import Any, Dict, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery

from app.services.policy_state import is_policy_accepted


ALLOWED_COMMANDS = {
    "start",
    "privacy",
    "language",
    "premium",
    "policy",      # ✅ важно
}

ALLOWED_CALLBACK_PREFIXES = (
    "policy:",     # ✅ важно (policy:agree / policy:disagree)
    "privacy:",
    "language:",
    "premium:",
)

ALLOWED_TEXT_BUTTONS = {
    "🔐 Данные и приватность",
    "🔐 Дані та приватність",
    "🔐 Data & Privacy",
    # policy
    "🔐 Политика",
    "⚠️ Политика",
    "🔒 Политика",
    "🔒 Політика",
    "🔒 Privacy",

    # settings / navigation
    "⚙️ Настройки",
    "⬅️ Назад",
    "🏠 Главное меню",

    # premium
    "💎 Премиум",
    "💎 Преміум",
    "💎 Premium",

    # language
    "🌐 Язык",
    "🌐 Мова",
    "🌐 Language",

    # continue (если вдруг это reply-кнопка)
    "Продолжить",
    "Продовжити",
    "Continue",
    "📓 Журнал",
    "📓 Journal",
    "📓 Щоденник",

    "🧘 Медиа",
    "🧘 Media",
    "🧘 Медіа",

    "🥇 Мотивация",
    "🥇 Мотивація",
    "🥇 Motivation",

    "⚡️ Проактивность",
    "⚡ Проактивность",
    "Проактивность",
    "⚡️ Проактивність",
    "⚡ Проактивність",
    "Проактивність",
    "⚡️ Proactive",
    "⚡ Proactive",
    "Proactive",
}

class PolicyGateMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        session = data.get("session")
        user = getattr(event, "from_user", None)

        if not user:
            return await handler(event, data)

        accepted = await is_policy_accepted(session, user.id)

        if accepted:
            return await handler(event, data)

        # -------- MESSAGE --------
        if isinstance(event, Message):
            text = (event.text or "").strip()

            if text.startswith("/"):
                cmd = text.lstrip("/").split()[0].split("@")[0]  # ✅ с учётом /cmd@bot
                if cmd in ALLOWED_COMMANDS:
                    return await handler(event, data)

            # ✅ разрешаем кнопки меню до принятия политики
            if text in ALLOWED_TEXT_BUTTONS:
                return await handler(event, data)

            await event.answer("🔒 Нужно принять политику, чтобы пользоваться ботом.\n\nГде найти:\n• Кнопка: ⚠️ Политика\n• Меню: ⚙️ Настройки → 🔒 Политика\n• Команда: /policy")
            return

        # -------- CALLBACK --------
        if isinstance(event, CallbackQuery):
            if event.data:
                for p in ALLOWED_CALLBACK_PREFIXES:
                    if event.data.startswith(p):
                        return await handler(event, data)

            await event.answer(
                "🔒 Сначала прими политику",
                show_alert=True,
            )
            return

        return await handler(event, data)
