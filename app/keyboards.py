from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


import re
from typing import Dict, Optional

import os
from app.webapp.urls import versioned_url, WEBAPP_PREMIUM_ENTRY
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, WebAppInfo


WEBAPP_PREMIUM_URL = os.getenv("WEBAPP_PREMIUM_URL") or WEBAPP_PREMIUM_ENTRY


# -------------------------------------------------------------------
# I18N helper (безопасный доступ к t())
# -------------------------------------------------------------------

_BAD_I18N = re.compile(r"^\[[a-z]{2}\]$")


def _t(lang: Optional[str], key: str, fallback: Dict[str, str]) -> str:
    """
    Пытаемся взять строку из i18n.
    Если i18n отдаёт мусор/ключ/плейсхолдер — используем fallback.

    Защита от:
    - возвращает "[ru]" / "[uk]" / "[en]"
    - возвращает сам key
    - возвращает служебные ключи menu_/btn_/cmd_
    """
    loc = (lang or "ru").strip().lower()
    try:
        from app.i18n import t as _real

        v = _real(key, loc)
        if isinstance(v, str):
            vv = v.strip()
            low = vv.lower()

            if vv and not _BAD_I18N.match(vv) and low != key.lower() and not low.startswith(("menu_", "btn_", "cmd_")):
                return vv
    except Exception:
        # В проде i18n не должен падать, но мы не кладём бота из-за текстов.
        pass

    # fallback по первым 2 буквам
    lang2 = loc[:2]
    if lang2 == "ua":
        lang2 = "uk"
    return fallback.get(lang2, fallback.get("ru", key))


# -------------------------------------------------------------------
# Premium бейдж
# -------------------------------------------------------------------


def _premium_badge(is_premium: bool) -> str:
    """
    Для бесплатных — добавляем 💎 перед премиум-фичами.
    Для премиум — показываем тот же текст, но без бейджа.
    """
    return "" if is_premium else "💎 "


# -------------------------------------------------------------------
# ROOT: главная клавиатура
# -------------------------------------------------------------------


def get_main_kb(
    lang: str,
    is_premium: bool = False,
    is_admin: bool = False,
    **_: object,
) -> ReplyKeyboardMarkup:
    """
    Главный экран:

    1) Основное:
       📓 Журнал      | ⏰ Напоминания
    2) Инструменты:
       🔥 Калории     | 📊 Статистика
    3) Мозг и фокус:
       🤖 Помощник    | 🧘 Медиа (медитация/музыка)
    4) Деньги и настройки:
       💎 Премиум     | ⚙️ Настройки
    5) (опционально) Админ
    6) 🧩 Баг-репорт
    """
    # Журнал / Напоминания
    row_main = [
        KeyboardButton(
            text=_t(
                lang,
                "menu_journal_root",
                {"ru": "📓 Журнал", "uk": "📓 Журнал", "en": "📓 Journal"},
            )
        ),
        KeyboardButton(
            text=_t(
                lang,
                "menu_reminders_root",
                {"ru": "⏰ Напоминания", "uk": "⏰ Нагадування", "en": "⏰ Reminders"},
            )
        ),
    ]

    # Калории / Статистика
    row_tools = [
        KeyboardButton(
            text=_t(
                lang,
                "btn_calories_root",
                {"ru": "🔥 Калории", "uk": "🔥 Калорії", "en": "🔥 Calories"},
            )
        ),
        KeyboardButton(
            text=_t(
                lang,
                "menu_stats_root",
                {"ru": "📊 Статистика", "uk": "📊 Статистика", "en": "📊 Stats"},
            )
        ),
    ]

    row_proactive = [
        KeyboardButton(
            text=_t(
                lang,
                "menu_proactive_root",
                {
                    "ru": "⚡️ Проактивность",
                    "uk": "⚡️ Проактивність",
                    "en": "⚡️ Proactivity",
                },
            )
        ),
        KeyboardButton(
            text=_t(
                lang,
                "menu_motivation_root",
                {"ru": "🥇 Мотивация", "uk": "🥇 Мотивація", "en": "🥇 Motivation"},
            )
        ),
    ]

    # Помощник / Медиа
    row_brain = [
        KeyboardButton(
            text=_t(
                lang,
                "btn_assistant_root",
                {"ru": "🤖 Помощник", "uk": "🤖 Помічник", "en": "🤖 Assistant"},
            )
        ),
        KeyboardButton(
            text=_t(
                lang,
                "menu_media_root",
                {"ru": "🧘 Медиа", "uk": "🧘 Медіа", "en": "🧘 Media"},
            )
        ),
    ]

    # Премиум / Настройки
    row_money = [
        KeyboardButton(
            text=_t(
                lang,
                "btn_premium_root",
                {"ru": "💎 Премиум", "uk": "💎 Преміум", "en": "💎 Premium"},
            )
        ),
        KeyboardButton(
            text=_t(
                lang,
                "menu_settings_root",
                {"ru": "⚙️ Настройки", "uk": "⚙️ Налаштування", "en": "⚙️ Settings"},
            )
        ),
    ]

    # Админ (опционально)
    admin_row = [
        KeyboardButton(
            text=_t(
                lang,
                "btn_admin",
                {"ru": "🛡 Админ", "uk": "🛡 Адмін", "en": "🛡 Admin"},
            )
        ),
    ]

    # Баг-репорт
    bug_row = [
        KeyboardButton(
            text=_t(
                lang,
                "btn_report_bug",
                {
                    "ru": "🧩 Баг-репорт",
                    "uk": "🧩 Баг-репорт",
                    "en": "🧩 Report a bug",
                },
            )
        ),
    ]

    rows = [
        row_main,
        row_tools,
        row_proactive,
        row_brain,
        row_money,
    ]

    if is_admin:
        rows.append(admin_row)

    rows.append(bug_row)

    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=rows)


main_menu_kb = get_main_kb


# -------------------------------------------------------------------
# SUBMENUS
# -------------------------------------------------------------------


def get_journal_menu_kb(lang: str) -> ReplyKeyboardMarkup:
    """
    Подменю Журнала:
    ✍️ Запись  | 🧾 Сегодня
    📅 Неделя  | 🕘 История
    🔍 Поиск   | 🗓 Диапазон
    ⬅️ Назад
    """
    row0 = [
        KeyboardButton(
            text=_t(
                lang,
                "menu_journal_add",
                {"ru": "✍️ Запись", "uk": "✍️ Запис", "en": "✍️ Entry"},
            )
        ),
        KeyboardButton(
            text=_t(
                lang,
                "menu_today",
                {"ru": "🧾 Сегодня", "uk": "🧾 Сьогодні", "en": "🧾 Today"},
            )
        ),
    ]

    row1 = [
        KeyboardButton(
            text=_t(
                lang,
                "menu_week",
                {"ru": "📅 Неделя", "uk": "📅 Тиждень", "en": "📅 Week"},
            )
        ),
        KeyboardButton(
            text=_t(
                lang,
                "menu_history",
                {"ru": "🕘 История", "uk": "🕘 Історія", "en": "🕘 History"},
            )
        ),
    ]

    row2 = [
        KeyboardButton(
            text=_t(
                lang,
                "menu_journal_search",
                {"ru": "🔍 Поиск", "uk": "🔍 Пошук", "en": "🔍 Search"},
            )
        ),
        KeyboardButton(
            text=_t(
                lang,
                "menu_journal_range",
                {"ru": "🗓 Диапазон", "uk": "🗓 Діапазон", "en": "🗓 Range"},
            )
        ),
    ]

    row_back = [
        KeyboardButton(
            text=_t(
                lang,
                "btn_back",
                {"ru": "⬅️ Назад", "uk": "⬅️ Назад", "en": "⬅️ Back"},
            )
        )
    ]

    return ReplyKeyboardMarkup(
        resize_keyboard=True,
        keyboard=[row0, row1, row2, row_back],
    )


def get_media_menu_kb(lang: str) -> ReplyKeyboardMarkup:
    """
    Подменю Медиа:
    🧘 Медитация | 🎵 Музыка
    ⬅️ Назад
    """
    row1 = [
        KeyboardButton(
            text=_t(
                lang,
                "menu_meditation",
                {"ru": "🧘 Медитация", "uk": "🧘 Медитація", "en": "🧘 Meditation"},
            )
        ),
        KeyboardButton(
            text=_t(
                lang,
                "menu_music",
                {"ru": "🎵 Музыка", "uk": "🎵 Музика", "en": "🎵 Music"},
            )
        ),
    ]
    row_back = [
        KeyboardButton(
            text=_t(
                lang,
                "btn_back",
                {"ru": "⬅️ Назад", "uk": "⬅️ Назад", "en": "⬅️ Back"},
            )
        )
    ]
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[row1, row_back])


def get_premium_menu_kb(lang: str, is_premium: bool = False, tg_id: int | None = None) -> ReplyKeyboardMarkup:
    """
    Подменю Премиума:
    💎 О премиуме        | 💳 Оплатить картой
    💫 Оплатить через Stars
    ❌ Отменить подписку  (только если is_premium=True)
    ⬅️ Назад

    Stars тут — это способ доплатить/оплатить премиум, а НЕ отдельная подписка.
    """
    p = _premium_badge(is_premium)

    tg_id = tg_id or 0
    row1 = [
        KeyboardButton(
            text=_t(
                lang,
                "btn_premium_info",
                {
                    "ru": "💎 О премиуме",
                    "uk": "💎 Про преміум",
                    "en": "💎 About premium",
                },
            )
        ),
        KeyboardButton(
            text=p
            + _t(
                lang,
                "btn_premium_card",
                {
                    "ru": "💳 Оплатить картой",
                    "uk": "💳 Оплатити карткою",
                    "en": "💳 Pay by card",
                },
            ),
            web_app=WebAppInfo(url=versioned_url(f"{WEBAPP_PREMIUM_URL}?tg_id={tg_id}")),
        ),
    ]
    row2 = [
        KeyboardButton(
            text=p
            + _t(
                lang,
                "btn_premium_stars",
                {
                    "ru": "💫 Оплатить через Stars",
                    "uk": "💫 Оплатити через Stars",
                    "en": "💫 Pay via Stars",
                },
            )
        ),
    ]

    keyboard = [row1, row2]

    if is_premium:
        keyboard.append(
            [
                KeyboardButton(
                    text=_t(
                        lang,
                        "btn_premium_cancel",
                        {
                            "ru": "❌ Отменить подписку",
                            "uk": "❌ Скасувати підписку",
                            "en": "❌ Cancel subscription",
                        },
                    )
                )
            ]
        )

    keyboard.append(
        [
            KeyboardButton(
                text=_t(
                    lang,
                    "btn_premium_refund",
                    {
                        "ru": "💸 Возврат средств",
                        "uk": "💸 Повернення коштів",
                        "en": "💸 Refund",
                    },
                )
            )
        ]
    )

    keyboard.append([KeyboardButton(text=_t(lang, "btn_back", {"ru": "⬅️ Назад", "uk": "⬅️ Назад", "en": "⬅️ Back"}))])

    logger.info(
        "PAY_KB(premium_menu): lang=%s is_premium=%s keyboard=%s",
        lang,
        is_premium,
        [[b.text for b in row] for row in keyboard],
    )

    return ReplyKeyboardMarkup(
        resize_keyboard=True,
        keyboard=keyboard,
    )


def get_settings_menu_kb(lang: str) -> ReplyKeyboardMarkup:
    """
    Подменю Настроек:
    🌐 Язык | 🔒 Политика
    ⬅️ Назад
    """
    row1 = [
        KeyboardButton(
            text=_t(
                lang,
                "btn_language",
                {"ru": "🌐 Язык", "uk": "🌐 Мова", "en": "🌐 Language"},
            )
        ),
        KeyboardButton(
            text=_t(
                lang,
                "btn_privacy",
                {"ru": "🔒 Политика", "uk": "🔒 Політика", "en": "🔒 Privacy"},
            )
        ),
    ]
    row2 = [
        KeyboardButton(
            text=_t(
                lang,
                "btn_data_privacy",
                {
                    "ru": "🔐 Данные и приватность",
                    "uk": "🔐 Дані та приватність",
                    "en": "🔐 Data & Privacy",
                },
            )
        ),
        KeyboardButton(
            text=_t(
                lang, 
                "btn_about", 
                {
                    "ru": "ℹ️ О боте", 
                    "uk": "ℹ️ Про бота", 
                    "en": "ℹ️ About"
                }
            )
        )
    ]

    row_back = [
        KeyboardButton(
            text=_t(
                lang,
                "btn_back",
                {"ru": "⬅️ Назад", "uk": "⬅️ Назад", "en": "⬅️ Back"},
            )
        )
    ]
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[row1, row2, row_back])


# -------------------------------------------------------------------
# Нормализация текстов кнопок
# -------------------------------------------------------------------


def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().replace("ё", "е").split())


def _norm_btn(s: str) -> str:
    """
    Нормализация именно для кликов по меню:
    - чистим пробелы/регистр
    - убираем ведущий премиум-бейдж 💎 (чтобы клики совпадали у free/premium)
    """
    t = _norm(s)
    if t.startswith("💎 "):
        t = t[2:].strip()
    elif t.startswith("💎"):
        t = t[1:].strip()
    return t


# -------------------------------------------------------------------
# Button matchers (ROOT + SUBMENUS)
# -------------------------------------------------------------------

# root
ROOT_JOURNAL_TXT = {_norm_btn(x) for x in ("📓 журнал", "журнал", "📓 journal", "journal")}
ROOT_REMINDERS_TXT = {_norm_btn(x) for x in ("⏰ напоминания", "напоминания", "⏰ reminders", "reminders")}
ROOT_CALORIES_TXT = {
    _norm_btn(x)
    for x in (
        "🔥 калории",
        "калории",
        "🔥 калорії",
        "калорії",
        "🔥 calories",
        "calories",
    )
}
ROOT_STATS_TXT = {_norm_btn(x) for x in ("📊 статистика", "статистика", "📊 stats", "stats")}
ROOT_ASSISTANT_TXT = {
    _norm_btn(x)
    for x in (
        "🤖 помощник",
        "помощник",
        "🤖 помічник",
        "помічник",
        "🤖 assistant",
        "assistant",
    )
}
ROOT_MEDIA_TXT = {_norm_btn(x) for x in ("🧘 медиа", "медиа", "🧘 медіа", "медіа", "🧘 media", "media")}
ROOT_PREMIUM_TXT = {_norm_btn(x) for x in ("💎 премиум", "премиум", "💎 преміум", "преміум", "💎 premium", "premium")}
ROOT_SETTINGS_TXT = {
    _norm_btn(x)
    for x in (
        "⚙️ настройки",
        "настройки",
        "⚙️ налаштування",
        "налаштування",
        "⚙️ settings",
        "settings",
    )
}
ROOT_PROACTIVE_TXT = {
    _norm_btn(x)
    for x in (
        "⚡️ проактивность",
        "проактивность",
        "⚡️ проактивність",
        "проактивність",
        "⚡️ proactivity",
        "proactivity",
    )
}
REPORT_TXT = {
    _norm_btn(x)
    for x in (
        "🧩 баг-репорт",
        "баг-репорт",
        "🧩 report a bug",
        "report a bug",
        "report bug",
        "🛠 сообщить о баге",
        "сообщить о баге",
        "🛠 сообщить об ошибке",
        "сообщить об ошибке",
        "🛠 повідомити про баг",
        "повідомити про баг",
    )
}
ADMIN_TXT = {_norm_btn(x) for x in ("🛡 админ", "админ", "🛡 адмін", "адмін", "🛡 admin", "admin")}

# journal submenu
HISTORY_TXT = {
    _norm_btn(x)
    for x in (
        "🕘 история",
        "история",
        "🕘 історія",
        "історія",
        "🕘 history",
        "history",
    )
}
TODAY_TXT = {
    _norm_btn(x)
    for x in (
        "🧾 сегодня",
        "сегодня",
        "🧾 сьогодні",
        "сьогодні",
        "🧾 today",
        "today",
    )
}
WEEK_TXT = {
    _norm_btn(x)
    for x in (
        "📅 неделя",
        "неделя",
        "📅 тиждень",
        "тиждень",
        "📅 week",
        "week",
    )
}
SEARCH_TXT = {
    _norm_btn(x)
    for x in (
        "🔍 поиск",
        "поиск",
        "🔍 пошук",
        "пошук",
        "🔍 search",
        "search",
    )
}
RANGE_TXT = {
    _norm_btn(x)
    for x in (
        "🗓 диапазон",
        "диапазон",
        "🗓 діапазон",
        "діапазон",
        "🗓 range",
        "range",
    )
}

# media submenu
MEDITATION_TXT = {
    _norm_btn(x)
    for x in (
        "🧘 медитация",
        "медитация",
        "🧘 медитація",
        "медитація",
        "🧘 meditation",
        "meditation",
    )
}
MUSIC_TXT = {
    _norm_btn(x)
    for x in (
        "🎵 музыка",
        "музыка",
        "🎵 музика",
        "музика",
        "🎵 music",
        "music",
    )
}

# premium submenu
PREMIUM_INFO_TXT = {
    _norm_btn(x)
    for x in (
        "💎 о премиуме",
        "о премиуме",
        "💎 про преміум",
        "про преміум",
        "💎 about premium",
        "about premium",
    )
}
PREMIUM_CARD_TXT = {
    _norm_btn(x)
    for x in (
        "💳 оплатить картой",
        "оплатить картой",
        "💳 оплатити карткою",
        "оплатити карткою",
        "💳 pay by card",
        "pay by card",
    )
}
PREMIUM_STARS_TXT = {
    _norm_btn(x)
    for x in (
        "💫 оплатить через stars",
        "оплатить через stars",
        "💫 оплатити через stars",
        "оплатити через stars",
        "💫 pay via stars",
        "pay via stars",
    )
}

# settings submenu
LANGUAGE_TXT = {
    _norm_btn(x)
    for x in (
        "🌐 язык",
        "язык",
        "🌐 мова",
        "мова",
        "🌐 language",
        "language",
    )
}
PRIVACY_TXT = {
    _norm_btn(x)
    for x in (
        "🔒 политика",
        "политика",
        "🔒 політика",
        "політика",
        "🔒 privacy",
        "privacy",
    )
}

DATA_PRIVACY_TXT = {
    _norm_btn(x)
    for x in (
        "🔐 данные и приватность",
        "данные и приватность",
        "🔐 дані та приватність",
        "дані та приватність",
        "🔐 data & privacy",
        "data & privacy",
        "data privacy",
    )
}

BACK_TXT = {_norm_btn(x) for x in ("⬅️ назад", "назад", "⬅️ back", "back")}


# ---------------- root matchers ----------------


def is_root_journal_btn(text: str) -> bool:
    return _norm_btn(text) in ROOT_JOURNAL_TXT


def is_root_reminders_btn(text: str) -> bool:
    return _norm_btn(text) in ROOT_REMINDERS_TXT


def is_root_calories_btn(text: str) -> bool:
    return _norm_btn(text) in ROOT_CALORIES_TXT


def is_root_stats_btn(text: str) -> bool:
    return _norm_btn(text) in ROOT_STATS_TXT


def is_root_assistant_btn(text: str) -> bool:
    return _norm_btn(text) in ROOT_ASSISTANT_TXT


def is_root_media_btn(text: str) -> bool:
    return _norm_btn(text) in ROOT_MEDIA_TXT


def is_root_premium_btn(text: str) -> bool:
    return _norm_btn(text) in ROOT_PREMIUM_TXT


def is_root_settings_btn(text: str) -> bool:
    return _norm_btn(text) in ROOT_SETTINGS_TXT


def is_root_proactive_btn(text: str) -> bool:
    return _norm_btn(text) in ROOT_PROACTIVE_TXT


def is_report_bug_btn(text: str) -> bool:
    return _norm_btn(text) in REPORT_TXT


def is_admin_btn(text: str) -> bool:
    return _norm_btn(text) in ADMIN_TXT


# -------------- journal submenu texts --------------
# ✅ “сам журнал” / “новая запись” — чтобы в подменю снова была кнопка записи
ADD_TXT = {
    _norm_btn(x)
    for x in (
        "✍️ запись",
        "📝 запись",
        "➕ запись",
        "✍️ новая запись",
        "📝 новая запись",
        "✍️ запис",
        "📝 запис",
        "➕ запис",  # uk
        "✍️ entry",
        "📝 entry",
        "➕ entry",  # en
        "new entry",
    )
}


# -------------- journal submenu matchers --------------


def is_journal_add_btn(text: str) -> bool:
    return _norm_btn(text) in ADD_TXT


def is_journal_today_btn(text: str) -> bool:
    return _norm_btn(text) in TODAY_TXT


def is_journal_week_btn(text: str) -> bool:
    return _norm_btn(text) in WEEK_TXT


def is_journal_history_btn(text: str) -> bool:
    return _norm_btn(text) in HISTORY_TXT


def is_journal_search_btn(text: str) -> bool:
    return _norm_btn(text) in SEARCH_TXT


def is_journal_range_btn(text: str) -> bool:
    return _norm_btn(text) in RANGE_TXT


# -------------- media submenu matchers --------------


def is_meditation_btn(text: str) -> bool:
    return _norm_btn(text) in MEDITATION_TXT


def is_music_btn(text: str) -> bool:
    return _norm_btn(text) in MUSIC_TXT


# -------------- premium submenu matchers --------------


def is_premium_info_btn(text: str) -> bool:
    return _norm_btn(text) in PREMIUM_INFO_TXT


def is_premium_card_btn(text: str) -> bool:
    return _norm_btn(text) in PREMIUM_CARD_TXT


def is_premium_stars_btn(text: str) -> bool:
    return _norm_btn(text) in PREMIUM_STARS_TXT


# -------------- settings submenu matchers --------------


def is_language_btn(text: str) -> bool:
    return _norm_btn(text) in LANGUAGE_TXT


def is_privacy_btn(text: str) -> bool:
    return _norm_btn(text) in PRIVACY_TXT


def is_data_privacy_btn(text: str) -> bool:
    return _norm_btn(text) in DATA_PRIVACY_TXT


def is_policy_btn(text: str) -> bool:
    """Legacy-алиас, если где-то использовался is_policy_btn"""
    return is_privacy_btn(text)


# -------------- legacy aliases (root + журнал + прочее) --------------


def is_journal_btn(text: str) -> bool:
    """
    Legacy-алиас: в старых хендлерах is_journal_btn обычно означал root-кнопку "📓 Журнал".
    Теперь корректно мапим на root "Журнал".
    А "✍️ Запись" — отдельный matcher is_journal_add_btn.
    """
    return is_root_journal_btn(text)


def is_today_btn(text: str) -> bool:
    return is_journal_today_btn(text)


def is_week_btn(text: str) -> bool:
    return is_journal_week_btn(text)


def is_history_btn(text: str) -> bool:
    return is_journal_history_btn(text)


def is_search_btn(text: str) -> bool:
    return is_journal_search_btn(text)


def is_range_btn(text: str) -> bool:
    return is_journal_range_btn(text)


def is_stats_btn(text: str) -> bool:
    # /stats — это root-кнопка статистики
    return is_root_stats_btn(text)


def is_reminders_btn(text: str) -> bool:
    return is_root_reminders_btn(text)


def is_calories_btn(text: str) -> bool:
    """
    Алиас для старого импорта:
    features/calories.py ожидает is_calories_btn,
    а внутри мы используем новую логику root-кнопки калорий.
    """
    return is_root_calories_btn(text)


def is_premium_btn(text: str) -> bool:
    """Legacy-алиас для открытия премиума."""
    return is_root_premium_btn(text)


def is_settings_btn(text: str) -> bool:
    """Legacy-алиас для открытия настроек."""
    return is_root_settings_btn(text)


def is_assistant_btn(text: str) -> bool:
    """
    Legacy-алиас: старые хендлеры ожидают is_assistant_btn,
    внутри используем новую логику root-кнопки помощника.
    """
    return is_root_assistant_btn(text)


def is_media_btn(text: str) -> bool:
    """Legacy-алиас: если где-то использовался is_media_btn."""
    return is_root_media_btn(text)


# -------------- shared --------------


def is_back_btn(text: str) -> bool:
    return _norm_btn(text) in BACK_TXT


PRIVACY_LABELS = {
    "ru": "🔒 Политика",
    "uk": "🔒 Політика",
    "en": "🔒 Privacy",
}


def is_report_btn(text: str) -> bool:
    return is_report_bug_btn(text)


__all__ = [
    # root kb
    "get_main_kb",
    "main_menu_kb",
    # submenus
    "get_journal_menu_kb",
    "get_media_menu_kb",
    "get_premium_menu_kb",
    "get_settings_menu_kb",
    # root matchers
    "is_root_journal_btn",
    "is_root_reminders_btn",
    "is_root_calories_btn",
    "is_root_stats_btn",
    "is_root_assistant_btn",
    "is_root_media_btn",
    "is_root_premium_btn",
    "is_root_settings_btn",
    "is_root_proactive_btn",
    "is_report_bug_btn",
    "is_report_btn",
    "is_admin_btn",
    # legacy root aliases
    "is_stats_btn",
    "is_reminders_btn",
    "is_calories_btn",
    "is_premium_btn",
    "is_settings_btn",
    "is_assistant_btn",
    "is_media_btn",
    # journal submenu
    "is_journal_add_btn",
    "is_journal_today_btn",
    "is_journal_week_btn",
    "is_journal_history_btn",
    "is_journal_search_btn",
    "is_journal_range_btn",
    # legacy journal aliases
    "is_journal_btn",
    "is_today_btn",
    "is_week_btn",
    "is_history_btn",
    "is_search_btn",
    "is_range_btn",
    # media submenu
    "is_meditation_btn",
    "is_music_btn",
    # premium submenu
    "is_premium_info_btn",
    "is_premium_card_btn",
    "is_premium_stars_btn",
    # settings submenu
    "is_language_btn",
    "is_privacy_btn",
    "is_policy_btn",
    # shared
    "is_back_btn",
    "PRIVACY_LABELS",
]
