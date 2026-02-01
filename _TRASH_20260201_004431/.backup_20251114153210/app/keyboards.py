# app/keyboards.py
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

# Тексты кнопок по локалям
_LOCALE_TEXTS = {
    "ru": {
        "language": "🌐 Язык",
        "privacy":  "🔒 Политика",
        "report":   "🛠️ Сообщить об ошибке",
        "premium":  "💎 Премиум",
        "journal":  "📝 Новая запись",
        "reminder": "⏰ Создать напоминание",
        "stats":    "📊 Статистика",
        "placeholder": "Напишите сообщение…",
    },
    "uk": {
        "language": "🌐 Мова",
        "privacy":  "🔒 Політика",
        "report":   "🛠️ Повідомити про баг",
        "premium":  "💎 Преміум",
        "journal":  "📝 Новий запис",
        "reminder": "⏰ Створити нагадування",
        "stats":    "📊 Статистика",
        "placeholder": "Напишіть повідомлення…",
    },
    "en": {
        "language": "🌐 Language",
        "privacy":  "🔒 Privacy",
        "report":   "🛠️ Report bug",
        "premium":  "💎 Premium",
        "journal":  "📝 New entry",
        "reminder": "⏰ Create reminder",
        "stats":    "📊 Stats",
        "placeholder": "Write a message…",
    },
}

def _build_kb(loc: str) -> ReplyKeyboardMarkup:
    t = _LOCALE_TEXTS.get(loc, _LOCALE_TEXTS["ru"])
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t["journal"]), KeyboardButton(text=t["reminder"])],
            [KeyboardButton(text=t["stats"]),   KeyboardButton(text=t["premium"])],
            [KeyboardButton(text=t["language"]), KeyboardButton(text=t["privacy"])],
            [KeyboardButton(text=t["report"])],
        ],
        resize_keyboard=True,
        input_field_placeholder=t["placeholder"],
    )

def get_main_kb(locale: str | None):
    """Вернуть основную клавиатуру по локали ('ru'|'uk'|'en')."""
    loc = (locale or "ru").lower()
    return _build_kb(loc)

# Совместимость со старым импортом
get_main_kb = get_main_kb

# Наборы лейблов (для фильтров в хендлерах)
PRIVACY_LABELS  = { _LOCALE_TEXTS[k]["privacy"]  for k in _LOCALE_TEXTS }
LANGUAGE_LABELS = { _LOCALE_TEXTS[k]["language"] for k in _LOCALE_TEXTS }
REPORT_LABELS   = { _LOCALE_TEXTS[k]["report"]   for k in _LOCALE_TEXTS }
PREMIUM_LABELS  = { _LOCALE_TEXTS[k]["premium"]  for k in _LOCALE_TEXTS }
JOURNAL_LABELS  = { _LOCALE_TEXTS[k]["journal"]  for k in _LOCALE_TEXTS }
REMINDER_LABELS = { _LOCALE_TEXTS[k]["reminder"] for k in _LOCALE_TEXTS }
STATS_LABELS    = { _LOCALE_TEXTS[k]["stats"]    for k in _LOCALE_TEXTS }

# Удобные хелперы
def is_privacy_btn(text: str)  -> bool: return (text or "").strip() in PRIVACY_LABELS
def is_language_btn(text: str) -> bool: return (text or "").strip() in LANGUAGE_LABELS
def is_report_btn(text: str)   -> bool: return (text or "").strip() in REPORT_LABELS
def is_premium_btn(text: str)  -> bool: return (text or "").strip() in PREMIUM_LABELS
def is_journal_btn(text: str)  -> bool: return (text or "").strip() in JOURNAL_LABELS
def is_reminder_btn(text: str) -> bool: return (text or "").strip() in REMINDER_LABELS
def is_stats_btn(text: str)    -> bool: return (text or "").strip() in STATS_LABELS