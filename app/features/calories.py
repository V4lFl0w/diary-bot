from __future__ import annotations

import os
import re
from typing import Dict, Optional, Any

import httpx
from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.i18n import t
from app.keyboards import (
    get_main_kb,
    is_calories_btn,
    # матчеры главного меню
    is_root_journal_btn,
    is_root_reminders_btn,
    is_root_calories_btn,
    is_root_stats_btn,
    is_root_assistant_btn,
    is_root_media_btn,
    is_root_premium_btn,
    is_root_settings_btn,
    is_report_bug_btn,
    is_admin_btn,
    is_back_btn,
)

try:
    from app.handlers.admin import is_admin_tg
except Exception:
    def is_admin_tg(_: int) -> bool:
        return False

from app.models.user import User

# v2-feature gating (канон)
try:
    from app.services.features_v2 import require_feature_v2
except Exception:
    require_feature_v2 = None  # type: ignore


router = Router(name="calories")

FEATURE_CAL_PHOTO = "calories_photo"

SUPPORTED_LANGS = {"ru", "uk", "en"}


# -------------------- FSM --------------------

class CaloriesFSM(StatesGroup):
    waiting_input = State()


# -------------------- i18n helpers --------------------

def _normalize_lang(code: Optional[str]) -> str:
    c = (code or "ru").strip().lower()
    if c.startswith(("ua", "uk")):
        c = "uk"
    elif c.startswith("en"):
        c = "en"
    else:
        c = "ru"
    if c not in SUPPORTED_LANGS:
        c = "ru"
    return c


def _tr(lang: str, ru: str, uk: str, en: str) -> str:
    l = _normalize_lang(lang)
    return uk if l == "uk" else en if l == "en" else ru


async def _get_user(session: AsyncSession, tg_id: int) -> Optional[User]:
    return (
        await session.execute(select(User).where(User.tg_id == tg_id))
    ).scalar_one_or_none()


def _user_lang(user: Optional[User], fallback: Optional[str], tg_lang: Optional[str] = None) -> str:
    return _normalize_lang(
        getattr(user, "locale", None)
        or getattr(user, "lang", None)
        or fallback
        or tg_lang
        or "ru"
    )


# -------------------- fallback nutrition база --------------------

FALLBACK: Dict[str, Dict[str, float]] = {
    # milk
    "молок": dict(kcal=60, p=3.2, f=3.2, c=4.7),
    "milk": dict(kcal=60, p=3.2, f=3.2, c=4.7),

    # banana
    "банан": dict(kcal=89, p=1.1, f=0.3, c=23.0),
    "banana": dict(kcal=89, p=1.1, f=0.3, c=23.0),

    # peanuts
    "арахис": dict(kcal=567, p=26.0, f=49.0, c=16.0),
    "арахіс": dict(kcal=567, p=26.0, f=49.0, c=16.0),
    "peanut": dict(kcal=567, p=26.0, f=49.0, c=16.0),

    # buckwheat
    "греч": dict(kcal=343, p=13.3, f=3.4, c=71.5),
    "гречк": dict(kcal=343, p=13.3, f=3.4, c=71.5),
    "buckwheat": dict(kcal=343, p=13.3, f=3.4, c=71.5),

    # eggs
    "яйц": dict(kcal=143, p=13.0, f=10.0, c=1.1),
    "egg": dict(kcal=143, p=13.0, f=10.0, c=1.1),

    # bread
    "хлеб": dict(kcal=250, p=9.0, f=3.0, c=49.0),
    "хліб": dict(kcal=250, p=9.0, f=3.0, c=49.0),
    "bread": dict(kcal=250, p=9.0, f=3.0, c=49.0),

    # cheese
    "сыр": dict(kcal=350, p=26.0, f=27.0, c=3.0),
    "сир": dict(kcal=350, p=26.0, f=27.0, c=3.0),
    "cheese": dict(kcal=350, p=26.0, f=27.0, c=3.0),

    # sausage
    "сосиск": dict(kcal=300, p=12.0, f=27.0, c=2.0),
    "sausage": dict(kcal=300, p=12.0, f=27.0, c=2.0),

    # chicken
    "куриц": dict(kcal=190, p=29.0, f=7.0, c=0.0),
    "курк": dict(kcal=190, p=29.0, f=7.0, c=0.0),
    "chicken": dict(kcal=190, p=29.0, f=7.0, c=0.0),

    # meat / pork / shashlik
    "свинин": dict(kcal=260, p=26.0, f=18.0, c=0.0),
    "шашлык": dict(kcal=250, p=22.0, f=18.0, c=0.0),
    "мяс":    dict(kcal=230, p=23.0, f=15.0, c=0.0),
}

PIECE_GRAMS: Dict[str, int] = {
    "яйц": 50,
    "egg": 50,
    "банан": 120,
    "banana": 120,
    "хлеб": 30,
    "хліб": 30,
    "bread": 30,
    "сыр": 30,
    "сир": 30,
    "cheese": 30,
    "сосиск": 50,
    "sausage": 50,
    "куриц": 80,
    "курк": 80,
    "chicken": 80,
}

CAL_KEYS = list(FALLBACK.keys())


def _looks_like_food(text: Optional[str]) -> bool:
    tl_raw = (text or "").strip()
    if not tl_raw:
        return False
    if tl_raw.startswith("/"):
        return False
    # не трогаем клики по меню
    if _is_root_menu_text(tl_raw):
        return False

    tl = tl_raw.lower()
    return any(k in tl for k in CAL_KEYS)


def _strip_cmd_prefix(text: str) -> str:
    s = (text or "").strip()
    s = re.sub(r"^/(calories|kcal)\s*", "", s, flags=re.IGNORECASE)
    return s.strip()


# -------------------- anti-UX-bug helpers --------------------

MENU_LIKE_TEXTS = {
    # RU
    "🌐 язык", "язык",
    "📓 журнал", "журнал",
    "⏰ напоминания", "напоминания",
    "📊 статистика", "статистика",
    "🤖 помощник", "помощник",
    "🧘 медиа", "медиа",
    "💎 премиум", "премиум",
    "⚙️ настройки", "настройки",
    "🔎 поиск", "🔍 поиск", "поиск",
    "📜 история", "история",
    "📅 диапазон", "диапазон",
    "сегодня", "неделя",

    # UK
    "🌐 мова", "мова",
    "щоденник",
    "нагадування",
    "статистика",
    "помічник",
    "медіа",
    "преміум",
    "налаштування",
    "пошук",
    "історія",
    "діапазон",
    "сьогодні", "тиждень",

    # EN
    "🌐 language", "language",
    "journal",
    "reminders",
    "stats",
    "assistant",
    "media",
    "premium",
    "settings",
    "search",
    "history",
    "range",
    "today", "week",
}


def _is_root_menu_text(text: str) -> bool:
    """
    Проверяет, что текст – одна из кнопок главного меню
    (с учётом всех языков и иконок), используя матчеры из keyboards.py
    """
    return any(
        fn(text)
        for fn in (
            is_root_journal_btn,
            is_root_reminders_btn,
            is_root_calories_btn,
            is_root_stats_btn,
            is_root_assistant_btn,
            is_root_media_btn,
            is_root_premium_btn,
            is_root_settings_btn,
            is_report_bug_btn,
            is_admin_btn,
            is_back_btn,
        )
    )


def _is_menu_like_text(text: str) -> bool:
    """
    Мягкая защита: либо явная кнопка меню через матчеры,
    либо строка похожа на подпись кнопки.
    """
    if _is_root_menu_text(text):
        return True
    low = (text or "").strip().lower()
    return low in MENU_LIKE_TEXTS


def _is_foreign_command(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low.startswith("/"):
        return False
    # внутри калорий разрешаем только эти
    return not low.startswith(("/calories", "/kcal", "/cancel"))


# -------------------- analyze text --------------------

async def analyze_text(text: str) -> Dict[str, float]:
    """
    1) Пробуем Api Ninjas, если задан ключ.
    2) Если не удалось — считаем грубо по FALLBACK.
    """
    key = os.getenv("NINJAS_API_KEY") or os.getenv("NUTRITION_API_KEY")
    if key:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    "https://api.api-ninjas.com/v1/nutrition",
                    params={"query": text},
                    headers={"X-Api-Key": key},
                )
                resp.raise_for_status()
                items = resp.json()
                if isinstance(items, list) and items:
                    kcal = sum(float(i.get("calories", 0) or 0) for i in items)
                    p = sum(float(i.get("protein_g", 0) or 0) for i in items)
                    f = sum(float(i.get("fat_total_g", 0) or 0) for i in items)
                    c = sum(float(i.get("carbohydrates_total_g", 0) or 0) for i in items)
                    return {
                        "kcal": round(kcal),
                        "p": round(p, 1),
                        "f": round(f, 1),
                        "c": round(c, 1),
                    }
        except Exception:
            pass

    low = text.lower()
    grams_info: list[tuple[float, Dict[str, float]]] = []

    num = r"(\d+(?:[.,]\d+)?)"
    unit_re = r"(г|g|гр|ml|мл)"

    for name, meta in FALLBACK.items():
        safe_name = re.escape(name)
        pattern = rf"{num}\s*{unit_re}?\s*{safe_name}"

        for m in re.finditer(pattern, low):
            qty_raw = m.group(1).replace(",", ".")
            try:
                qty = float(qty_raw)
            except ValueError:
                continue

            unit = (m.group(2) or "").lower()

            # MVP: мл считаем как граммы (1:1)
            if unit in ("г", "g", "гр", "ml", "мл"):
                g = qty
            else:
                piece_g = PIECE_GRAMS.get(name)
                g = qty * piece_g if piece_g else qty

            grams_info.append((float(g), meta))

        # если продукт упомянут без количества и это штука
        if name in PIECE_GRAMS and name in low and not re.search(pattern, low):
            grams_info.append((float(PIECE_GRAMS[name]), meta))

    kcal = p = f = c = 0.0
    for g, meta in grams_info:
        factor = g / 100.0
        kcal += meta["kcal"] * factor
        p += meta["p"] * factor
        f += meta["f"] * factor
        c += meta["c"] * factor

    return {
        "kcal": round(kcal),
        "p": round(p, 1),
        "f": round(f, 1),
        "c": round(c, 1),
    }


# -------------------- premium gate --------------------

async def _require_photo_premium(
    message: types.Message,
    session: AsyncSession,
    user: User,
    lang_code: str,
    *,
    source: str,
    props: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Если features_v2 есть — используем канон.
    Иначе закрываем доступ без дыр.
    """
    if require_feature_v2 is None:
        await message.answer(
            _tr(
                lang_code,
                "📸 Подсчёт по фото доступен в 💎 Премиум.",
                "📸 Підрахунок по фото доступний у 💎 Преміум.",
                "📸 Photo calories are available in 💎 Premium.",
            )
        )
        return False

    ok = await require_feature_v2(
        message,
        session,
        user,
        FEATURE_CAL_PHOTO,
        event_on_fail="calories_photo_locked",
        props={"source": source, **(props or {})},
    )
    return bool(ok)


# -------------------- entrypoints --------------------

@router.message(Command("calories"))
@router.message(Command("kcal"))
async def cal_cmd(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    """
    /calories <text> -> считаем сразу
    /calories -> включаем режим ожидания
    """
    tg_lang = getattr(getattr(message, "from_user", None), "language_code", None)
    user = await _get_user(session, message.from_user.id)
    lang_code = _user_lang(user, lang, tg_lang)

    raw = (message.text or "").strip()
    query = _strip_cmd_prefix(raw)

    if query:
        res = await analyze_text(query)
        await message.answer(
            t("cal_total", lang_code, kcal=res["kcal"], p=res["p"], f=res["f"], c=res["c"])
        )
        return

    await state.set_state(CaloriesFSM.waiting_input)

    example = "250 мл молока, банан, 40 г арахиса"
    await message.answer(
        t("cal_send", lang_code, example=example),
        reply_markup=get_main_kb(
            lang_code,
            is_premium=bool(getattr(user, "is_premium", False)),
            is_admin=is_admin_tg(message.from_user.id if message.from_user else 0),
        ),
    )


@router.message(F.text.func(is_calories_btn))
async def cal_btn(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    tg_lang = getattr(getattr(message, "from_user", None), "language_code", None)
    user = await _get_user(session, message.from_user.id)
    lang_code = _user_lang(user, lang, tg_lang)

    await state.set_state(CaloriesFSM.waiting_input)

    example = "250 мл молока, банан, 40 г арахиса"
    await message.answer(
        t("cal_send", lang_code, example=example),
        reply_markup=get_main_kb(
            lang_code,
            is_premium=bool(getattr(user, "is_premium", False)),
            is_admin=is_admin_tg(message.from_user.id if message.from_user else 0),
        ),
    )


@router.message(Command("cancel"))
async def cal_cancel_global(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    tg_lang = getattr(getattr(message, "from_user", None), "language_code", None)
    user = await _get_user(session, message.from_user.id)
    lang_code = _user_lang(user, lang, tg_lang)

    await state.clear()
    await message.answer(
        _tr(lang_code, "Ок, отменил.", "Ок, скасував.", "Ok, cancelled."),
        reply_markup=get_main_kb(
            lang_code,
            is_premium=bool(getattr(user, "is_premium", False)),
            is_admin=is_admin_tg(message.from_user.id if message.from_user else 0),
        ),
    )


# -------------------- MODE: waiting_input --------------------

@router.message(CaloriesFSM.waiting_input, F.text)
async def cal_text_in_mode(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    text = (message.text or "").strip()
    if not text:
        return

    # если в режиме калорий пришла кнопка меню/чужая команда —
    # очищаем FSM и пропускаем дальше (журнал, медиа, настройки и т.п.)
    if _is_menu_like_text(text) or _is_foreign_command(text):
        await state.clear()
        return

    tg_lang = getattr(getattr(message, "from_user", None), "language_code", None)
    user = await _get_user(session, message.from_user.id)
    lang_code = _user_lang(user, lang, tg_lang)

    text = _strip_cmd_prefix(text)
    if not text:
        return

    res = await analyze_text(text)
    await message.answer(
        t("cal_total", lang_code, kcal=res["kcal"], p=res["p"], f=res["f"], c=res["c"])
    )
    # остаёмся в режиме ожидания


@router.message(CaloriesFSM.waiting_input, F.photo)
async def cal_photo_in_mode(
    message: types.Message,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    """
    Ключевой UX-фикс:
    Если юзер открыл Calories-режим, мы реагируем на фото даже без подписи.
    """

    tg_lang = getattr(getattr(message, "from_user", None), "language_code", None)
    user = await _get_user(session, message.from_user.id)
    lang_code = _user_lang(user, lang, tg_lang)

    if not user:
        await message.answer(_tr(lang_code, "Нажми /start", "Натисни /start", "Press /start"))
        return

    ok = await _require_photo_premium(
        message, session, user, lang_code,
        source="calories_waiting_input",
        props={"has_caption": bool(message.caption)},
    )
    if not ok:
        return

    await message.answer(
        _tr(
            lang_code,
            "📸 Подсчёт калорий по фото открыт ✅\n\n"
            "Скоро добавим распознавание еды и порций прямо с изображения.",
            "📸 Підрахунок калорій по фото відкрито ✅\n\n"
            "Скоро додамо розпізнавання їжі та порцій прямо з зображення.",
            "📸 Photo calories are unlocked ✅\n\n"
            "Food and portion recognition is coming soon.",
        )
    )


# -------------------- free text autodetect --------------------

@router.message(F.text.func(_looks_like_food))
async def cal_text_free_autodetect(
    message: types.Message,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    """
    Авто-детект еды вне режима /calories.
    """
    text = (message.text or "").strip()
    if not text:
        return

    # на всякий случай не трогаем меню и чужие команды
    if _is_menu_like_text(text) or _is_foreign_command(text):
        return

    tg_lang = getattr(getattr(message, "from_user", None), "language_code", None)
    user = await _get_user(session, message.from_user.id)
    lang_code = _user_lang(user, lang, tg_lang)

    res = await analyze_text(text)
    await message.answer(
        t("cal_total", lang_code, kcal=res["kcal"], p=res["p"], f=res["f"], c=res["c"])
    )


# -------------------- photo with caption trigger --------------------

@router.message(F.photo)
async def cal_photo_caption_trigger(
    message: types.Message,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    """
    Фото с подписью /calories или похожим списком еды.
    Работает вне FSM.
    """

    caption = (message.caption or "").strip()
    if not caption:
        return

    low = caption.lower()
    is_cmd = low.startswith(("/calories", "/kcal"))
    is_food_caption = _looks_like_food(caption)

    if not (is_cmd or is_food_caption):
        return

    tg_lang = getattr(getattr(message, "from_user", None), "language_code", None)
    user = await _get_user(session, message.from_user.id)
    lang_code = _user_lang(user, lang, tg_lang)

    if not user:
        await message.answer(_tr(lang_code, "Нажми /start", "Натисни /start", "Press /start"))
        return

    ok = await _require_photo_premium(
        message, session, user, lang_code,
        source="photo_caption_trigger",
        props={
            "has_caption": True,
            "caption_is_cmd": is_cmd,
            "caption_food_like": is_food_caption,
        },
    )
    if not ok:
        return

    payload_text = _strip_cmd_prefix(caption) if is_cmd else caption
    payload_text = payload_text.strip()

    if payload_text and _looks_like_food(payload_text):
        res = await analyze_text(payload_text)
        await message.answer(
            _tr(
                lang_code,
                "📸 Режим по фото активен ✅\n"
                "Пока распознавание еды с картинки в разработке,\n"
                "но я уже посчитал по подписи:\n\n"
                f"Калории: {res['kcal']:.0f} ккал\n"
                f"Белки: {res['p']:.1f} г\n"
                f"Жиры: {res['f']:.1f} г\n"
                f"Углеводы: {res['c']:.1f} г",
                "📸 Режим по фото активний ✅\n"
                "Поки розпізнавання їжі з картинки в розробці,\n"
                "але я вже порахував по підпису:\n\n"
                f"Калорії: {res['kcal']:.0f} ккал\n"
                f"Білки: {res['p']:.1f} г\n"
                f"Жири: {res['f']:.1f} г\n"
                f"Вуглеводи: {res['c']:.1f} г",
                "📸 Photo mode is active ✅\n"
                "Image recognition is coming soon,\n"
                "but I already counted from your caption:\n\n"
                f"Calories: {res['kcal']:.0f} kcal\n"
                f"Protein: {res['p']:.1f} g\n"
                f"Fat: {res['f']:.1f} g\n"
                f"Carbs: {res['c']:.1f} g",
            )
        )
        return

    await message.answer(
        _tr(
            lang_code,
            "📸 Подсчёт калорий по фото открыт ✅\n\n"
            "Скоро добавим распознавание еды и порций прямо с изображения.",
            "📸 Підрахунок калорій по фото відкрито ✅\n\n"
            "Скоро додамо розпізнавання їжі та порцій прямо з зображення.",
            "📸 Photo calories are unlocked ✅\n\n"
            "Food and portion recognition is coming soon.",
        )
    )


__all__ = ["router"]