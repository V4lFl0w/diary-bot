from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, time
from typing import Optional, Literal
from zoneinfo import ZoneInfo


@dataclass
class ParsedReminder:
    what: str
    raw_when: str
    next_run_utc: Optional[datetime] = None
    cron: Optional[str] = None


@dataclass
class ToggleRequest:
    action: Literal["enable", "disable"]
    query: Optional[str] = None
    all: bool = False


@dataclass
class ParseResult:
    intent: Literal["create", "enable", "disable"]
    reminder: Optional[ParsedReminder] = None
    toggle: Optional[ToggleRequest] = None


def parse_any(
    text: str,
    user_tz: str = "Europe/Kyiv",
    now: Optional[datetime] = None,
) -> Optional[ParseResult]:
    # 1) enable/disable
    tgl = parse_toggle(text)
    if tgl:
        return ParseResult(intent=tgl.action, toggle=tgl)

    # 2) create
    rem = parse_remind(text, user_tz=user_tz, now=now)
    if rem:
        return ParseResult(intent="create", reminder=rem)

    return None


def parse_remind(
    text: str,
    user_tz: str = "Europe/Kyiv",
    now: Optional[datetime] = None,
) -> Optional[ParsedReminder]:
    tz = ZoneInfo(user_tz)
    now = now or datetime.now(tz)
    text_norm = _normalize(text)

    # повторяющиеся (cron)
    cron = _parse_recurring_cron(text_norm)
    if cron:
        what = _extract_what(text_norm, recurring=True)
        if not what:
            return None
        return ParsedReminder(
            what=what,
            raw_when=text.strip(),
            cron=cron,
        )

    # разовое
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


# ---------- toggle / on/off ----------

_TOGGLE_ON_WORDS = r"(?:включи|вкл|увімкни|увімк|on|enable)"
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
    s = _normalize(text)
    m = _RE_TOGGLE.match(s)
    if not m:
        return None

    act = m.group("act")
    action: Literal["enable", "disable"] = (
        "enable"
        if re.search(rf"^{_TOGGLE_ON_WORDS}$", act, flags=re.I)
        else "disable"
    )

    is_all = bool(m.group("all") and m.group("all").strip())
    query = (m.group("query") or "").strip()

    if is_all:
        return ToggleRequest(action=action, query=None, all=True)

    if not query:
        # «выключи напоминания» → по смыслу тоже все
        return ToggleRequest(action=action, query=None, all=True)

    return ToggleRequest(action=action, query=query, all=False)


# ---------- напоминания (create) ----------

_TRIGGERS = r"(?:напомни|нагадай|remind(?:\s+me\s+to)?)"
_RE_IN = r"(?:через|за|in)"
_RE_AT = r"(?:в|о|at)"
_RE_TODAY = r"(?:сегодня|сьогодні|today)"
_RE_TOMORROW = r"(?:завтра|tomorrow)"
# чуть расширили: добавили «щоденно», «кожен», «кожного»
_RE_EVERY = r"(?:каждый|каждую|каждое|щодня|щоденно|щотижня|щосереди|щопонеділка|кожен|кожного|every|weekdays|daily)"

_DOW_MAP = {
    # RU
    "понедельник": 1,
    "вторник": 2,
    "среда": 3,
    "четверг": 4,
    "пятница": 5,
    "суббота": 6,
    "воскресенье": 0,
    "воскресение": 0,
    # UK
    "понеділок": 1,
    "вівторок": 2,
    "середа": 3,
    "четвер": 4,
    "пʼятниця": 5,
    "п'ятниця": 5,
    "субота": 6,
    "неділя": 0,
    # EN
    "monday": 1,
    "tuesday": 2,
    "wednesday": 3,
    "thursday": 4,
    "friday": 5,
    "saturday": 6,
    "sunday": 0,
}
_WEEKDAY_SET = set(_DOW_MAP.keys())

_RE_TIME = re.compile(
    r"\b(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ampm>am|pm)?\b",
    re.I,
)
_RE_DATE_DOT = re.compile(
    r"\b(?P<d>\d{1,2})\.(?P<m>\d{1,2})(?:\.(?P<y>\d{4}))?\b"
)
_RE_DATE_ISO = re.compile(
    r"\b(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})\b"
)

_RE_REL = re.compile(
    rf"\b{_RE_IN}\s+"
    r"(?:(?P<hours>\d+)\s*(?:час(?:а|ов)?|год(?:ини)?|h|hours?)\s*)?"
    r"(?:(?P<minutes>\d+)\s*(?:минут(?:ы)?|хв(?:илин)?|m|mins?|minutes?)\s*)?"
    r"(?:(?P<days>\d+)\s*(?:дн(?:я|ей|ів)?|days?)\s*)?"
    r"(?:(?P<weeks>\d+)\s*(?:недел(?:я|и|ь)|тижн(?:і|ів)|weeks?)\s*)?"
    r"\b",
    re.I,
)


def _normalize(text: str) -> str:
    t = text.strip().lower().replace("’", "'")
    t = re.sub(r"\s+", " ", t)
    return t


def _extract_what(
    text_norm: str,
    recurring: bool,
    allow_without_trigger: bool = True,
) -> Optional[str]:
    """
    Вырезаем «что напоминать» до маркеров времени.
    """
    markers = [
        r"\b" + _RE_IN + r"\b",
        r"\b" + _RE_AT + r"\b",
        r"\b" + _RE_TODAY + r"\b",
        r"\b" + _RE_TOMORROW + r"\b",
        r"\b" + _RE_EVERY + r"\b",
        r"\b" + "|".join(map(re.escape, _WEEKDAY_SET)) + r"\b",
        r"\bпо будням\b",
        r"\bпо буднях\b",
        r"\bweekdays\b",
        r"\bdaily\b",
        r"\bщодня\b",
        r"\bщоденно\b",
    ]

    # ищем триггер, типа «напомни / remind me to»
    m = re.search(rf"{_TRIGGERS}\s+(?:me\s+to\s+)?", text_norm)
    if m:
        start = m.end()
    elif allow_without_trigger:
        start = 0
    else:
        return None

    end = len(text_norm)
    for mk in markers:
        mm = re.search(mk, text_norm[start:])
        if mm:
            end = min(end, start + mm.start())

    what = text_norm[start:end].strip(" ,.;:—-")
    return what or None


def _parse_time_fragment(s: str) -> Optional[time]:
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


def _parse_once_datetime(
    text_norm: str,
    now: datetime,
    tz: ZoneInfo,
) -> Optional[datetime]:
    # относительные: через X минут/часов/дней/недель
    m = _RE_REL.search(text_norm)
    if m and any(m.group(g) for g in ("minutes", "hours", "days", "weeks")):
        dt = now
        if m.group("minutes"):
            dt += timedelta(minutes=int(m.group("minutes")))
        if m.group("hours"):
            dt += timedelta(hours=int(m.group("hours")))
        if m.group("days"):
            dt += timedelta(days=int(m.group("days")))
        if m.group("weeks"):
            dt += timedelta(weeks=int(m.group("weeks")))
        return dt

    # даты: 2025-12-31 или 31.12.(2025)
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

    # сегодня
    if re.search(rf"\b{_RE_TODAY}\b", text_norm):
        dt = _apply_time(now, tm or time(9, 0))
        # если время не указано и уже прошло — чуть сдвинем
        if tm is None and dt < now:
            dt = dt + timedelta(hours=1)
        return dt

    # завтра
    if re.search(rf"\b{_RE_TOMORROW}\b", text_norm):
        base = (now + timedelta(days=1)).replace(
            hour=9, minute=0, second=0, microsecond=0
        )
        if tm:
            base = _apply_time(base, tm)
        return base

    # только время → сегодня или завтра, если уже прошло
    if tm:
        dt = _apply_time(now, tm)
        if dt <= now:
            dt += timedelta(days=1)
        return dt

    return None


def _parse_recurring_cron(text_norm: str) -> Optional[str]:
    """
    Парсим повторяющиеся напоминания в cron-строку:
    - every day at 10:00 / каждый день в 10 / щодня о 10:00
    - по будням в 9 / weekdays at 9
    - every monday at 10, щосереди о 20 и т.п.
    """
    tm = _parse_time_fragment(text_norm)
    if not tm:
        return None

    minute = tm.minute
    hour = tm.hour

    # daily: каждый день / щодня / щоденно / every day
    if re.search(
        r"\b("
        r"daily"
        r"|щодня"
        r"|щоденно"
        r"|каждый день"
        r"|кожен день"
        r"|кожного дня"
        r"|every day"
        r"|everyday"
        r")\b",
        text_norm,
    ):
        return f"{minute} {hour} * * *"

    # weekdays: по будням / по буднях / weekdays
    if re.search(r"\b(weekdays|по будням|по буднях)\b", text_norm):
        return f"{minute} {hour} * * 1-5"

    # every monday / щосереди и т.п.
    if re.search(r"\b(кажд\w+|щос\w+|every)\b", text_norm):
        wd = _find_weekday(text_norm)
        if wd is not None:
            return f"{minute} {hour} * * {wd}"

    # просто «в понедельник в 10:00»
    wd = _find_weekday(text_norm)
    if wd is not None and re.search(rf"\b{_RE_AT}\b", text_norm):
        return f"{minute} {hour} * * {wd}"

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


__all__ = [
    "ParsedReminder",
    "ToggleRequest",
    "ParseResult",
    "parse_any",
    "parse_remind",
    "parse_toggle",
]