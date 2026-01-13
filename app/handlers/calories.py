from __future__ import annotations

from typing import Optional, Set, Dict, Tuple

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.features.nutrition_api import fetch_nutrition, NutritionError
from app.models.user import User

# paywall v2 (канон)
try:
    from app.services.features_v2 import require_feature_v2
except Exception:
    require_feature_v2 = None  # type: ignore

# кнопка "Калорії" из меню (если есть)
try:
    from app.keyboards import is_calories_btn
except Exception:
    def is_calories_btn(_text: str) -> bool:  # type: ignore
        return False

# кнопка "Политика" из меню (если есть)
try:
    from app.keyboards import is_privacy_btn
except Exception:
    def is_privacy_btn(_text: str) -> bool:  # type: ignore
        return False


router = Router(name="calories")

# каноничный ключ (алиасы в features_v2 уже покрывают старые названия)
FEATURE_CAL_PHOTO = "calories_photo"

SUPPORTED_LANGS = {"ru", "uk", "en"}


# -------------------- FSM --------------------

class CaloriesFSM(StatesGroup):
    waiting_input = State()


# -------------------- i18n helpers --------------------

def _normalize_lang(code: Optional[str]) -> str:
    c = (code or "ru").strip().lower()
    if c.startswith("ua"):
        c = "uk"
    if c not in SUPPORTED_LANGS:
        c = "ru"
    return c


def _tr(lang: Optional[str], ru: str, uk: str, en: str) -> str:
    l = _normalize_lang(lang)
    if l == "uk":
        return uk
    if l == "en":
        return en
    return ru


async def _get_user(session: AsyncSession, tg_id: int) -> Optional[User]:
    return (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()


def _user_lang(user: Optional[User], fallback: Optional[str], tg_lang: Optional[str]) -> str:
    return _normalize_lang(
        getattr(user, "locale", None)
        or getattr(user, "lang", None)
        or fallback
        or tg_lang
        or "ru"
    )


# -------------------- menu text guard --------------------
# Это ключевой фикс твоего бага.

MENU_BLOCKLIST: Set[str] = {
    # RU
    "🔥 Калории",
    "📓 Журнал",
    "📜 История",
    "⏰ Напоминания",
    "💎 Премиум",
    "📊 Статистика",
    "🧘 Медитация",
    "🎵 Музыка",
    "🔎 Поиск",
    "📅 Диапазон",
    "🌐 Язык",
    "🔒 Политика",
    # UK (на всякий)
    "🔥 Калорії",
    "📓 Журнал",
    "📜 Історія",
    "⏰ Нагадування",
    "💎 Преміум",
    "📊 Статистика",
    "🧘 Медитація",
    "🎵 Музика",
    "🔎 Пошук",
    "📅 Діапазон",
    "🌐 Мова",
    "🔒 Політика",
    # EN
    "🔥 Calories",
    "📓 Journal",
    "📜 History",
    "⏰ Reminders",
    "💎 Premium",
    "📊 Stats",
    "🧘 Meditation",
    "🎵 Music",
    "🔎 Search",
    "📅 Range",
    "🌐 Language",
    "🔒 Policy",
}


def _is_menu_text(text: Optional[str]) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if t in MENU_BLOCKLIST:
        return True
    # дополнительные “умные” проверки
    if is_calories_btn(t):
        return True
    if is_privacy_btn(t):
        return True
    return False


# -------------------- food detector (для автодетекта) --------------------

CAL_KEYS: Set[str] = {
    "молок", "банан", "арахис", "арахіс", "греч", "гречк",
    "яйц", "хлеб", "хліб", "сыр", "сир", "сосиск",
    "куриц", "курк",
    "milk", "banana", "peanut", "buckwheat", "egg",
    "bread", "cheese", "sausage", "chicken",
    "рис", "rice", "овся", "oat", "йогур", "yogurt",
}


def _looks_like_food(text: Optional[str]) -> bool:
    tl = (text or "").lower().strip()
    if not tl:
        return False
    if tl.startswith("/"):
        return False
    # не триггерим на кнопки меню
    if _is_menu_text(text):
        return False
    return any(k in tl for k in CAL_KEYS)


def _strip_cmd_prefix(text: str) -> str:
    s = (text or "").strip()
    low = s.lower()
    if low.startswith("/calories"):
        return s.split(maxsplit=1)[1].strip() if len(s.split(maxsplit=1)) > 1 else ""
    if low.startswith("/kcal"):
        return s.split(maxsplit=1)[1].strip() if len(s.split(maxsplit=1)) > 1 else ""
    return s


# -------------------- core text handler --------------------

async def _handle_calories_text(
    message: Message,
    session: AsyncSession,
    lang: Optional[str],
    *,
    query: str,
) -> None:
    tg_lang = getattr(getattr(message, "from_user", None), "language_code", None)
    user = await _get_user(session, message.from_user.id)  # type: ignore[arg-type]
    lang_code = _user_lang(user, lang, tg_lang)

    try:
        total, _items = await fetch_nutrition(query)

    except NutritionError as e:
        await message.answer(
            _tr(
                lang_code,
                f"Не получилось посчитать калории: {e}",
                f"Не вдалося порахувати калорії: {e}",
                f"Couldn't calculate nutrition: {e}",
            )
        )
        return

    except Exception:
        await message.answer(
            _tr(
                lang_code,
                "Что-то пошло не так при обращении к Nutrition API.",
                "Щось пішло не так під час звернення до Nutrition API.",
                "Something went wrong while calling Nutrition API.",
            )
        )
        return

    calories = float(total.get("calories", 0) or 0)
    protein = float(total.get("protein", 0) or 0)
    fat = float(total.get("fat", 0) or 0)
    carbs = float(total.get("carbohydrates", 0) or 0)

    msg = _tr(
        lang_code,
        "Итого по запросу:\n"
        f"Калории: {calories:.0f} ккал\n"
        f"Белки: {protein:.1f} г\n"
        f"Жиры: {fat:.1f} г\n"
        f"Углеводы: {carbs:.1f} г",
        "Підсумок за запитом:\n"
        f"Калорії: {calories:.0f} ккал\n"
        f"Білки: {protein:.1f} г\n"
        f"Жири: {fat:.1f} г\n"
        f"Вуглеводи: {carbs:.1f} г",
        "Total for your query:\n"
        f"Calories: {calories:.0f} kcal\n"
        f"Protein: {protein:.1f} g\n"
        f"Fat: {fat:.1f} g\n"
        f"Carbs: {carbs:.1f} g",
    )

    await message.answer(msg)


# -------------------- premium gate helper --------------------

async def _require_photo_premium(
    message: Message,
    session: AsyncSession,
    user: User,
    lang_code: str,
    *,
    source: str,
) -> bool:
    """
    Титановый гейт:
    - если require_feature_v2 есть — используем канон
    - если нет — безопасно закрываем доступ (без дыр)
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
        props={"source": source},
    )
    return bool(ok)


# -------------------- entrypoints --------------------

@router.message(Command("calories"))
@router.message(Command("kcal"))
async def calories_command(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    """
    /calories text -> считаем
    /calories без текста -> включаем режим ожидания
    """
    text = (message.text or "").strip()
    query = _strip_cmd_prefix(text)

    if query:
        await _handle_calories_text(message, session, lang, query=query)
        return

    tg_lang = getattr(getattr(message, "from_user", None), "language_code", None)
    user = await _get_user(session, message.from_user.id)  # type: ignore[arg-type]
    lang_code = _user_lang(user, lang, tg_lang)

    await state.set_state(CaloriesFSM.waiting_input)

    await message.answer(
        _tr(
            lang_code,
            "Ок. Напиши, что ты съел/выпил одним сообщением\n"
            "или отправь фото еды.\n\n"
            "Пример: 250 мл молока, банан, 40 г арахиса\n"
            "/cancel — отменить",
            "Ок. Напиши, що ти з'їв/випив одним повідомленням\n"
            "або надішли фото їжі.\n\n"
            "Приклад: 250 мл молока, банан, 40 г арахісу\n"
            "/cancel — скасувати",
            "Ok. Send what you ate/drank in one message\n"
            "or send a food photo.\n\n"
            "Example: 250ml milk, 1 banana, 40g peanuts\n"
            "/cancel — cancel",
        )
    )


@router.message(F.text.func(is_calories_btn))
async def calories_button_prompt(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    tg_lang = getattr(getattr(message, "from_user", None), "language_code", None)
    user = await _get_user(session, message.from_user.id)  # type: ignore[arg-type]
    lang_code = _user_lang(user, lang, tg_lang)

    await state.set_state(CaloriesFSM.waiting_input)

    await message.answer(
        _tr(
            lang_code,
            "Кидай список еды одним сообщением или фото.\n"
            "Пример: «250 мл молока, банан, 40 г арахиса»",
            "Кидай список їжі одним повідомленням або фото.\n"
            "Приклад: «250 мл молока, банан, 40 г арахісу»",
            "Send your food list in one message or a photo.\n"
            "Example: “250ml milk, 1 banana, 40g peanuts”",
        )
    )


@router.message(Command("cancel"))
async def calories_cancel(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    tg_lang = getattr(getattr(message, "from_user", None), "language_code", None)
    user = await _get_user(session, message.from_user.id)  # type: ignore[arg-type]
    lang_code = _user_lang(user, lang, tg_lang)

    await state.clear()
    await message.answer(
        _tr(lang_code, "Ок, отменил.", "Ок, скасував.", "Ok, cancelled.")
    )


# -------------------- режим ожидания --------------------

@router.message(CaloriesFSM.waiting_input, F.text)
async def calories_text_in_mode(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    text = (message.text or "").strip()
    if not text:
        return

    # ✅ если это команда — пусть её обработают командные хендлеры
    if text.startswith("/"):
        return

    # ✅ если человек нажал кнопку меню — выходим из режима калорий
    if _is_menu_text(text):
        await state.clear()
        return

    await _handle_calories_text(message, session, lang, query=text)


@router.message(CaloriesFSM.waiting_input, F.photo)
async def calories_photo_in_mode(
    message: Message,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    """
    Фото без подписи после нажатия "Калории".
    """
    tg_lang = getattr(getattr(message, "from_user", None), "language_code", None)
    user = await _get_user(session, message.from_user.id)  # type: ignore[arg-type]
    lang_code = _user_lang(user, lang, tg_lang)

    if not user:
        await message.answer(_tr(lang_code, "Нажми /start", "Натисни /start", "Press /start"))
        return

    ok = await _require_photo_premium(
        message, session, user, lang_code, source="calories_waiting_input"
    )
    if not ok:
        return

    await message.answer(
        _tr(
            lang_code,
            "📸 Калории по фото открыты ✅\n\n"
            "Скоро добавим распознавание продуктов на изображении.",
            "📸 Калорії з фото відкриті ✅\n\n"
            "Скоро додамо розпізнавання продуктів на зображенні.",
            "📸 Photo calories unlocked ✅\n\n"
            "We’ll add food recognition soon.",
        )
    )


# -------------------- free text autodetect --------------------

@router.message(F.text.func(_looks_like_food))
async def calories_free_text(
    message: Message,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    text = (message.text or "").strip()
    if not text:
        return

    # ✅ на всякий: не реагируем на меню
    if _is_menu_text(text):
        return

    await _handle_calories_text(message, session, lang, query=text)


# -------------------- photo with caption trigger --------------------

@router.message(F.photo)
async def calories_photo_caption(
    message: Message,
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
    is_cmd = low.startswith("/calories") or low.startswith("/kcal")
    is_food_caption = _looks_like_food(caption)

    if not (is_cmd or is_food_caption):
        return

    tg_lang = getattr(getattr(message, "from_user", None), "language_code", None)
    user = await _get_user(session, message.from_user.id)  # type: ignore[arg-type]
    lang_code = _user_lang(user, lang, tg_lang)

    if not user:
        await message.answer(_tr(lang_code, "Нажми /start", "Натисни /start", "Press /start"))
        return

    ok = await _require_photo_premium(
        message, session, user, lang_code, source="photo_caption_trigger"
    )
    if not ok:
        return

    await message.answer(
        _tr(
            lang_code,
            "📸 Калории по фото открыты ✅\n\n"
            "Скоро добавим распознавание продуктов на изображении.",
            "📸 Калорії з фото відкриті ✅\n\n"
            "Скоро додамо розпізнавання продуктів на зображенні.",
            "📸 Photo calories unlocked ✅\n\n"
            "We’ll add food recognition soon.",
        )
    )


__all__ = ["router", "CaloriesFSM"]