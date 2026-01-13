from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, time
from typing import Optional, Literal, List
from zoneinfo import ZoneInfo

# =======================
# Public API
# =======================

@dataclass
class ParsedReminder:
    """
    Результат создания напоминания.
    - одноразовое: next_run_utc заполнен
    - повторяющееся: cron заполнен (формат CRON: "m h d * dow")
    """
    what: str
    raw_when: str
    next_run_utc: Optional[datetime] = None
    cron: Optional[str] = None


@dataclass
class ToggleRequest:
    """
    Запрос на включение/выключение напоминаний.
    - action: 'enable' | 'disable'
    - query: часть названия (case-insensitive), если адресное
    - all: true, если нужно применить ко всем
    """
    action: Literal["enable", "disable"]
    query: Optional[str] = None
    all: bool = False


@dataclass
class ParseResult:
    """
    Унифицированный результат любой NLP-команды:
      - intent: 'create' | 'enable' | 'disable'
      - reminder: для intent='create'
      - toggle:   для intent in {'enable','disable'}
    """
    intent: Literal["create", "enable", "disable"]
    reminder: Optional[ParsedReminder] = None
    toggle: Optional[ToggleRequest] = None


def parse_any(text: str, user_tz: str = "Europe/Kyiv", now: Optional[datetime] = None) -> Optional[ParseResult]:
    """
    Пытается распарсить:
      1) включение/выключение (enable/disable)
      2) создание напоминания (create)
    Возвращает ParseResult или None.
    """
    tgl = parse_toggle(text)
    if tgl:
        return ParseResult(intent=tgl.action, toggle=tgl)

    rem = parse_remind(text, user_tz=user_tz, now=now)
    if rem:
        return ParseResult(intent="create", reminder=rem)

    return None


# =======================
# Reminder create (RU/UK/EN)
# =======================

def parse_remind(text: str, user_tz: str = "Europe/Kyiv", now: Optional[datetime] = None) -> Optional[ParsedReminder]:
    """
    Понимает:
      RU:  "напомни позвонить маме через 15 минут"
           "напомни оплатить счет завтра в 09:30"
           "напомни треню каждый день в 7:00"
           "напомни воду по будням в 10"
           "напомни отчёт каждую среду в 18:30"
           "напомни уборку по выходным в 12"
           "напомни платеж каждого 5-го числа в 10"
      UK:  "нагадай мені воду щодня о 9", "у вихідні о 12", "щомісяця 5 числа о 10"
      EN:  "remind me to call mom in 2 hours", "every wednesday at 6:30pm",
           "weekends at noon", "every month on the 5th at 10"
    """
    tz = ZoneInfo(user_tz)
    now = now or datetime.now(tz)

    text_norm = _normalize(text)

    # Сначала — повторяющиеся (cron)
    cron = _parse_recurring_cron(text_norm)
    if cron:
        what = _extract_what(text_norm, recurring=True)
        if not what:
            return None
        return ParsedReminder(what=what, raw_when=text.strip(), cron=cron)

    # Одноразовые
    dt = _parse_once_datetime(text_norm, now, tz)
    if dt:
        what = _extract_what(text_norm, recurring=False)
        if not what:
            return None
        return ParsedReminder(
            what=what,
            raw_when=text.strip(),
            next_run_utc=dt.astimezone(ZoneInfo("UTC")),
        )

    return None


# =======================
# Toggle (enable/disable)
# =======================

_TOGGLE_ON_WORDS  = r"(?:включи|вкл|увімкни|увімк|on|enable)"
_TOGGLE_OFF_WORDS = r"(?:выключи|выкл|відключи|вимкни|вимк|off|disable)"
_REMINDER_WORDS = r"(?:напоминани(?:е|я)|нагадування|reminder(?:s)?)"
_ALL_WORDS = r"(?:все|усі|всі|all)"

_RE_TOGGLE = re.compile(
    rf"(?i)\b(?P<act>{_TOGGLE_ON_WORDS}|{_TOGGLE_OFF_WORDS})\b"
    rf"(?:\s+{_REMINDER_WORDS})?"
    rf"(?:\s+(?:про|на|по|about|for))?"
    rf"(?:\s+(?P<all>{_ALL_WORDS}))?"
    rf"(?:\s*(?P<query>.+))?$"
)

def parse_toggle(text: str) -> Optional[ToggleRequest]:
    """Парсит включение/выключение напоминаний (RU/UK/EN)."""
    s = _normalize(text)
    m = _RE_TOGGLE.match(s)
    if not m:
        return None

    act = m.group("act")
    action: Literal["enable", "disable"] = "enable" if re.search(rf"^{_TOGGLE_ON_WORDS}$", act, flags=re.I) else "disable"

    is_all = bool(m.group("all") and m.group("all").strip())
    query = (m.group("query") or "").strip()

    if is_all:
        return ToggleRequest(action=action, query=None, all=True)
    if not query:
        return ToggleRequest(action=action, query=None, all=True)

    return ToggleRequest(action=action, query=query, all=False)


# =======================
# Internals
# =======================

# Триггеры создания
_TRIGGERS = r"(?:напомни|нагадай|remind(?:\s+me\s+to)?)"

# Служебные «сцепки»
_RE_IN = r"(?:через|за|in)"
_RE_AT = r"(?:в|о|at|on)"  # 'on' для конструкций 'on Monday at 6pm'
_RE_TODAY = r"(?:сегодня|сьогодні|today)"
_RE_TOMORROW = r"(?:завтра|tomorrow)"
_RE_EVERY = r"(?:кажд\w+|щодня|щотижня|щос\w+|every|weekdays|daily|ежедневно)"

# Дни недели
_DOW_MAP = {
    # RU
    "понедельник": 1, "вторник": 2, "среда": 3, "четверг": 4, "пятница": 5, "суббота": 6, "воскресенье": 0, "воскресение": 0,
    # UK
    "понеділок": 1, "вівторок": 2, "середа": 3, "четвер": 4, "пʼятниця": 5, "п'ятниця": 5, "субота": 6, "неділя": 0,
    # EN
    "monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4, "friday": 5, "saturday": 6, "sunday": 0,
}
_WEEKDAY_SET = set(_DOW_MAP.keys())

# Время: "9", "9:05", "21:30", "6pm", "6:30pm", "полночь", "полдень"
_RE_TIME = re.compile(r"\b(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ampm>am|pm)?\b", re.I)
_SPECIAL_TIME = {
    "полдень": (12, 0),
    "полудень": (12, 0),
    "noon": (12, 0),
    "полночь": (0, 0),
    "опівночі": (0, 0),
    "midnight": (0, 0),
}

# Даты
_RE_DATE_DOT = re.compile(r"\b(?P<d>\d{1,2})\.(?P<m>\d{1,2})(?:\.(?P<y>\d{4}))?\b")
_RE_DATE_ISO = re.compile(r"\b(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})\b")

# Относительные интервалы
_RE_REL = re.compile(
    rf"\b{_RE_IN}\s+"
    r"(?:(?P<hours>\d+)\s*(?:час(?:а|ов)?|год(?:ини)?|h|hours?)\s*)?"
    r"(?:(?P<minutes>\d+)\s*(?:минут(?:ы)?|хв(?:илин)?|m|mins?|minutes?)\s*)?"
    r"(?:(?P<days>\d+)\s*(?:дн(?:я|ей|ів)?|days?)\s*)?"
    r"(?:(?P<weeks>\d+)\s*(?:недел(?:я|и|ь)|тижн(?:і|ів)|weeks?)\s*)?"
    r"\b",
    re.I,
)

# Ежемесячные конструкции: "каждого 5-го (числа) в 10", "every month on the 5th at 10"
_RE_MONTHLY = re.compile(
    r"""(?ix)
    \b(?:
        каждый\s+месяц|каждого\s+месяца|щомісяця|every\s+month
    )\b
    (?:\s+(?:на|on))?
    (?:\s*(?:\b(?P<dom1>\d{1,2})(?:-?(?:го|е|th|st|nd|rd))?\b|\b(?P<dom2>\d{1,2})\s+числа\b))?
    """,
)

# Weekends: "по выходным", "на выходных", "у вихідні", "на вихідних", "weekends"
_RE_WEEKENDS = re.compile(r"\b(?:по\s+выходн(?:ым|ых)|на\s+выходных|у\s+вихідні|на\s+вихідних|weekends?)\b", re.I)

def _normalize(text: str) -> str:
    t = text.strip().lower()
    t = re.sub(r"\s+", " ", t)
    t = t.replace("’", "'")
    return t

def _extract_what(text_norm: str, recurring: bool) -> Optional[str]:
    """
    Берём всё после триггера до маркера времени/периодичности.
    """
    markers = [
        r"\b" + _RE_IN + r"\b",
        r"\b" + _RE_AT + r"\b",
        r"\b" + _RE_TODAY + r"\b",
        r"\b" + _RE_TOMORROW + r"\b",
        r"\b" + _RE_EVERY + r"\b",
        r"\b" + "|".join(map(re.escape, _WEEKDAY_SET)) + r"\b",
        r"\bпо будням\b",
        r"\bweekdays\b",
        r"\bdaily\b",
        r"\bщодня\b",
        r"\bweekends?\b",
        r"\bпо выходн(?:ым|ых)\b",
        r"\bщомісяця\b",
        r"\bкаждый месяц\b",
        r"\bevery month\b",
    ]
    m = re.search(rf"{_TRIGGERS}\s+(?:me\s+to\s+)?", text_norm)
    if not m:
        return None
    start = m.end()

    end = len(text_norm)
    for mk in markers:
        mm = re.search(mk, text_norm[start:])
        if mm:
            end = min(end, start + mm.start())

    what = text_norm[start:end].strip(" ,.;:—-\"'«»")
    return what or None

def _parse_time_fragment(s: str) -> Optional[time]:
    # спец-слова
    for key, (hh, mm) in _SPECIAL_TIME.items():
        if re.search(rf"\b{re.escape(key)}\b", s, flags=re.I):
            return time(hour=hh, minute=mm)

    m = _RE_TIME.search(s)
    if not m:
        return None
    h = int(m.group("h"))
    mnt = int(m.group("m") or 0)
    ampm = (m.group("ampm") or "").lower()
    if ampm == "pm" and 1 <= h <= 11:
        h += 12
    if ampm == "am" and h == 12:
        h = 0
    if not (0 <= h <= 23 and 0 <= mnt <= 59):
        return None
    return time(hour=h, minute=mnt)

def _apply_time(base: datetime, t: time) -> datetime:
    return base.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)

def _parse_once_datetime(text_norm: str, now: datetime, tz: ZoneInfo) -> Optional[datetime]:
    # "через X ..."
    m = _RE_REL.search(text_norm)
    if m and any(m.group(g) for g in ("minutes", "hours", "days", "weeks")):
        dt = now
        if m.group("minutes"): dt += timedelta(minutes=int(m.group("minutes")))
        if m.group("hours"):   dt += timedelta(hours=int(m.group("hours")))
        if m.group("days"):    dt += timedelta(days=int(m.group("days")))
        if m.group("weeks"):   dt += timedelta(weeks=int(m.group("weeks")))
        return dt

    # конкретная дата
    date_dt = None
    mi = _RE_DATE_ISO.search(text_norm)
    md = _RE_DATE_DOT.search(text_norm)
    if mi:
        y, mo, d = int(mi.group("y")), int(mi.group("m")), int(mi.group("d"))
        date_dt = datetime(y, mo, d, tzinfo=tz)
    elif md:
        d, mo = int(md.group("d")), int(md.group("m"))
        y = int(md.group("y")) if md.group("y") else now.year
        date_dt = datetime(y, mo, d, tzinfo=tz)

    tm = _parse_time_fragment(text_norm)
    if date_dt:
        dt = _apply_time(date_dt, tm or time(9, 0))
        return dt

    # today / tomorrow
    if re.search(rf"\b{_RE_TODAY}\b", text_norm):
        dt = _apply_time(now, tm or time(9, 0))
        if tm is None and dt < now:
            dt = dt + timedelta(hours=1)
        return dt

    if re.search(rf"\b{_RE_TOMORROW}\b", text_norm):
        base = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        if tm:
            base = _apply_time(base, tm)
        return base

    # просто "в 14:30" — ближайшее такое время
    if tm:
        dt = _apply_time(now, tm)
        if dt <= now:
            dt += timedelta(days=1)
        return dt

    return None

def _parse_recurring_cron(text_norm: str) -> Optional[str]:
    """
    Возвращает cron:
      - daily / каждый день / щодня / ежедневно  -> "m h * * *"
      - weekdays / по будням                      -> "m h * * 1-5"
      - weekends / по выходным                    -> "m h * * 6,0"
      - specific weekday(s)                       -> "m h * * dow[,dow...]"
      - monthly day-of-month                      -> "m h d * *"
    Требует указания времени.
    """
    tm = _parse_time_fragment(text_norm)
    if not tm:
        return None
    minute = tm.minute
    hour = tm.hour

    # daily
    if re.search(r"\b(daily|щодня|каждый день|ежедневно)\b", text_norm):
        return f"{minute} {hour} * * *"

    # weekdays
    if re.search(r"\b(weekdays|по будням)\b", text_norm):
        return f"{minute} {hour} * * 1-5"

    # weekends
    if _RE_WEEKENDS.search(text_norm):
        return f"{minute} {hour} * * 6,0"

    # monthly (every month on Nth ...)
    mm = _RE_MONTHLY.search(text_norm)
    if mm:
        dom_raw = mm.group("dom1") or mm.group("dom2")
        if dom_raw:
            d = int(dom_raw)
            if 1 <= d <= 31:
                return f"{minute} {hour} {d} * *"
        # если день не указан, не строим крон (чтобы не гадать)

    # every/каждую/щосереди + weekday(s)
    if re.search(r"\b(кажд\w+|щос\w+|every|on)\b", text_norm):
        dows = _find_all_weekdays(text_norm)
        if dows:
            unique = sorted(set(dows))
            dow_str = ",".join(str(x) for x in unique)
            return f"{minute} {hour} * * {dow_str}"

    # просто «среду в 18:00» (без "каждую"), либо "monday at 9"
    dows = _find_all_weekdays(text_norm)
    if dows and re.search(rf"\b{_RE_AT}\b", text_norm):
        unique = sorted(set(dows))
        dow_str = ",".join(str(x) for x in unique)
        return f"{minute} {hour} * * {dow_str}"

    return None

def _find_weekday(text_norm: str) -> Optional[int]:
    pos = -1
    val = None
    for name, dow in _DOW_MAP.items():
        m = re.search(rf"\b{name}\b", text_norm)
        if m and m.start() > pos:
            pos = m.start()
            val = dow
    return val

def _find_all_weekdays(text_norm: str) -> List[int]:
    """Находит все упоминания дней недели в строке (для 'понедельник и среду')."""
    found: List[int] = []
    for name, dow in _DOW_MAP.items():
        if re.search(rf"\b{name}\b", text_norm):
            found.append(dow)
    return found


__all__ = [
    "ParsedReminder",
    "ToggleRequest",
    "ParseResult",
    "parse_any",
    "parse_remind",
    "parse_toggle",
]