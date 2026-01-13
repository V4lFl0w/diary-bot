from typing import Dict

DEFAULT_LOCALE = "ru"
SUPPORTED_LOCALES = {"ru", "uk", "en"}

# ---------------------------------------------------------
# Base texts + MAIN MENU keys (match app.keyboards)
# ---------------------------------------------------------

TEXTS: Dict[str, Dict[str, str]] = {
    "ru": {
        # --- Onboarding / system ---
        "welcome": "Привет! Это дневник-помощник. Нажми 🔒 Политика, чтобы принять условия и начать.",
        "privacy": "Это помощник-дневник, не терапия и не медицинская услуга.",
        "privacy_answer": "Ответ: Согласен / Не согласен",
        "privacy_thanks": "Спасибо! Можно начинать. Напиши первую запись: /journal",
        "privacy_declined": "Ок. Я не буду сохранять записи, пока вы не согласны.",
        "choose_lang": "Выбери язык: RU / UK / EN (можно написать: русский / українська / английский)",
        "lang_updated": "Готово. Язык обновлён.",
        "press_start": "Нажми /start",
        "main_hint": "Главное меню — внизу.",

        # --- MAIN MENU (keys used in keyboards.py) ---
        "menu_journal": "📓 Журнал",
        "menu_history": "🕘 История",
        "menu_journal_search": "🔍 Поиск",
        "menu_journal_range": "🗓 Диапазон",
        "menu_today": "🧾 Сегодня",
        "menu_week": "📅 Неделя",
        "menu_reminders": "⏰ Напоминания",
        "menu_stats": "📊 Статистика",

        "menu_meditation": "🧘 Медитация",
        "menu_music": "🎵 Музыка",

        "btn_language": "🌐 Язык",
        "btn_privacy": "🔒 Политика",
        "btn_premium": "💎 Премиум",
        "btn_calories": "🔥 Калории",
        "btn_admin": "🛡 Админ",

        # --- Bug report (match keyboards + handlers) ---
        "btn_report_bug": "🧩 Баг-репорт",
        # optional legacy alias if somewhere used
        "btn_report": "🛠 Сообщить про баг",

        # --- Admin ---
        "admin_panel_title": "🛡 Админ-панель",

        # --- Feature intros ---
        "meditations_intro": "🧘 Подборка коротких медитаций и дыхательных практик.",
        "music_intro": "🎵 Музыка для фокуса, сна и расслабления.",
        "med_choose": "Выбери режим медитации:",
        "music_choose": "Выбери плейлист:",

        # --- Calories ---
        "cal_send": "Напиши, что ты съел/выпил за раз, например: «{example}» — я посчитаю калории.",
        "cal_total": "Итого: {kcal} ккал (Б: {p} г, Ж: {f} г, У: {c} г).",
    },

    "uk": {
        "welcome": "Привіт! Це щоденник-помічник. Натисни 🔒 Політика, щоб прийняти умови і почати.",
        "privacy": "Це помічник-щоденник, не терапія і не медична послуга.",
        "privacy_answer": "Відповідь: Згоден / Не згоден",
        "privacy_thanks": "Дякую! Починаємо. Напиши перший запис: /journal",
        "privacy_declined": "Ок. Я не зберігатиму записи, поки ви не згодні.",
        "choose_lang": "Обери мову: RU / UK / EN (можна написати: українська / русский / english)",
        "lang_updated": "Готово. Мову оновлено.",
        "press_start": "Натисни /start",
        "main_hint": "Головне меню — внизу.",

        "menu_journal": "📓 Журнал",
        "menu_history": "🕘 Історія",
        "menu_journal_search": "🔍 Пошук",
        "menu_journal_range": "🗓 Діапазон",
        "menu_today": "🧾 Сьогодні",
        "menu_week": "📅 Тиждень",
        "menu_reminders": "⏰ Нагадування",
        "menu_stats": "📊 Статистика",

        "menu_meditation": "🧘 Медитація",
        "menu_music": "🎵 Музика",

        "btn_language": "🌐 Мова",
        "btn_privacy": "🔒 Політика",
        "btn_premium": "💎 Преміум",
        "btn_calories": "🔥 Калорії",
        "btn_admin": "🛡 Адмін",

        "btn_report_bug": "🧩 Баг-репорт",
        "btn_report": "🛠 Повідомити про баг",

        "admin_panel_title": "🛡 Адмін-панель",

        "meditations_intro": "🧘 Добірка коротких медитацій та дихальних практик.",
        "music_intro": "🎵 Музика для фокусу, сну та розслаблення.",
        "med_choose": "Оберіть режим медитації:",
        "music_choose": "Виберіть плейлист:",

        "cal_send": "Напиши, що ти з'їв/випив за раз, наприклад: «{example}» — я порахую калорії.",
        "cal_total": "Разом: {kcal} ккал (Б: {p} г, Ж: {f} г, В: {c} г).",
    },

    "en": {
        "welcome": "Hi! This is a diary assistant. Tap 🔒 Privacy to accept the policy and start.",
        "privacy": "This is a journal assistant, not therapy or a medical service.",
        "privacy_answer": "Reply: Agree / Disagree",
        "privacy_thanks": "Thanks! You can start. Send your first entry: /journal",
        "privacy_declined": "Okay. I won’t save entries until you agree.",
        "choose_lang": "Choose language: RU / UK / EN (you can also type: русский / українська / english)",
        "lang_updated": "Done. Language updated.",
        "press_start": "Press /start",
        "main_hint": "Main menu is below.",

        "menu_journal": "📓 Journal",
        "menu_history": "🕘 History",
        "menu_journal_search": "🔍 Search",
        "menu_journal_range": "🗓 Range",
        "menu_today": "🧾 Today",
        "menu_week": "📅 Week",
        "menu_reminders": "⏰ Reminders",
        "menu_stats": "📊 Stats",

        "menu_meditation": "🧘 Meditation",
        "menu_music": "🎵 Music",

        "btn_language": "🌐 Language",
        "btn_privacy": "🔒 Privacy",
        "btn_premium": "💎 Premium",
        "btn_calories": "🔥 Calories",
        "btn_admin": "🛡 Admin",

        "btn_report_bug": "🧩 Report a bug",
        "btn_report": "🛠 Report a bug",

        "admin_panel_title": "🛡 Admin panel",

        "meditations_intro": "🧘 Short meditations and breathing exercises.",
        "music_intro": "🎵 Music for focus, sleep and relaxation.",
        "med_choose": "Choose a meditation mode:",
        "music_choose": "Choose a playlist:",

        "cal_send": "Type what you ate / drank, e.g. “{example}” — I’ll calculate calories.",
        "cal_total": "Total: {kcal} kcal (P: {p} g, F: {f} g, C: {c} g).",
    },
}

# ---------------------------------------------------------
# Extra translations (non-menu texts, buttons in flows, etc.)
# ---------------------------------------------------------

TRANSLATIONS: Dict[str, Dict[str, str]] = {
    # premium states
    "premium_on": {
        "ru": "Премиум уже активен ✅",
        "uk": "Преміум уже активний ✅",
        "en": "Premium is already active ✅",
    },
    "premium_on_till": {
        "ru": "Премиум активен до {dt} ({tz}) ✅",
        "uk": "Преміум активний до {dt} ({tz}) ✅",
        "en": "Premium is active until {dt} ({tz}) ✅",
    },
    "subscribe_offer": {
        "ru": "Премиум не активен. Подпишись на наш канал — и получи 24 часа премиума 🎁",
        "uk": "Преміум не активний. Підпишись на наш канал — і отримай 24 години преміуму 🎁",
        "en": "Premium is off. Subscribe to our channel and get 24h of Premium 🎁",
    },
    "sub_given": {
        "ru": "Поздравляю! Подписка подтверждена — премиум активирован на 24 часа ✅",
        "uk": "Вітаю! Підписку підтверджено — преміум активовано на 24 години ✅",
        "en": "Congrats! Subscription confirmed — Premium activated for 24 hours ✅",
    },
    "sub_not_found": {
        "ru": "Не вижу подписки. Нажми «Подписаться», затем «Проверить».",
        "uk": "Не бачу підписки. Натисни «Підписатися», потім «Перевірити».",
        "en": "I can’t see your subscription. Tap “Subscribe” then “Check”.",
    },

    # premium flow buttons
    "btn_pay": {
        "ru": "Оплатить",
        "uk": "Оплатити",
        "en": "Pay",
    },
    "btn_sub": {
        "ru": "Подписаться",
        "uk": "Підписатися",
        "en": "Subscribe",
    },
    "btn_check": {
        "ru": "Проверить",
        "uk": "Перевірити",
        "en": "Check",
    },

    # bug report flow (handlers may use these)
    "bug_report_start": {
        "ru": "Опиши проблему одним сообщением и приложи скрин/видео. Или пришли /cancel.",
        "uk": "Опиши проблему одним повідомленням і додай скрін/відео. Або надішли /cancel.",
        "en": "Describe the issue in one message and attach a screenshot/video. Or send /cancel.",
    },
    "bug_report_cancel": {
        "ru": "Окей, отменил. Возвращаемся в меню.",
        "uk": "Гаразд, скасував. Повертаємось до меню.",
        "en": "Okay, cancelled. Back to menu.",
    },
    "bug_report_thanks": {
        "ru": "Спасибо! Отчёт отправлен ✅",
        "uk": "Дякуємо! Звіт надіслано ✅",
        "en": "Thanks! Report sent ✅",
    },
}

# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------

def _normalize_lang(lang: str | None) -> str:
    if not lang:
        return DEFAULT_LOCALE
    s = str(lang).lower().strip()
    s2 = s[:2]
    if s2 == "ua":
        s2 = "uk"
    return s2 if s2 in SUPPORTED_LOCALES else DEFAULT_LOCALE


def detect_lang(code: str | None) -> str:
    return _normalize_lang(code)


def t(key: str, lang: str | None = None, **kwargs) -> str:
    """
    Translation resolver:
    1) TRANSLATIONS[key][loc]
    2) TEXTS[loc][key]
    3) TEXTS[DEFAULT][key]
    4) TEXTS["en"][key]
    5) key (as fallback)
    """
    loc = _normalize_lang(lang)
    s = None

    mapping = TRANSLATIONS.get(key)
    if isinstance(mapping, dict):
        s = mapping.get(loc) or mapping.get(DEFAULT_LOCALE) or mapping.get("en")

    if s is None:
        s = TEXTS.get(loc, {}).get(key)
    if s is None:
        s = TEXTS.get(DEFAULT_LOCALE, {}).get(key)
    if s is None:
        s = TEXTS.get("en", {}).get(key)
    if s is None:
        s = key

    try:
        return s.format(**kwargs)
    except Exception:
        return s