# exporter.py
import re
from typing import Optional, Tuple

# Глагол-триггер (RU/UK/EN) + мягкие «пожалуйста/будь ласка»
VERB = (
    r"(?:пожалуйста\s+)?(?:будь\s+ласка\s+)?"
    r"(?:"
    r"напомни(?:ть)?|поставь\s+напоминание|"
    r"нагадай|нагадати|"
    r"remind(?:\s+me)?(?:\s+to)?"
    r")"
)

# Любые распространённые кавычки
QUOTE_CHARS = "\"'“”«»"
Q = r"[\"'“”«»]"


def _strip_quotes_punct(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] in QUOTE_CHARS and s[-1] in QUOTE_CHARS:
        s = s[1:-1].strip()
    return s.rstrip(".!?,; ").lstrip()


# Паттерны: возвращают группы what/when
_RAW_PATTERNS = [
    # 1) RU/UK/EN: «напомни WHAT в|о|через|at|on|in WHEN»
    rf"{VERB}\s+(?P<what>{Q}?[^\"'“”«»]+?{Q}?)\s+(?:в|о|через|at|on|in)\s+(?P<when>.+)",
    # 2) RU/EN: «напомни через WHEN WHAT»
    rf"{VERB}\s+(?:через|in)\s+(?P<when>[^\"'“”«»]+?)\s+(?P<what>{Q}?[^\"'“”«»]+{Q}?)",
    # 3) RU/UK: «напомни WHAT (сегодня|завтра|послезавтра|сьогодні) [в …]»
    rf"{VERB}\s+(?P<what>{Q}?[^\"'“”«»]+?{Q}?)\s+"
    rf"(?P<when>(?:сегодня|завтра|послезавтра|сьогодні)(?:\s+(?:в|о)\s+.+)?)",
    # 4) EN: «remind (today|tomorrow|next Monday|on Monday [at …]|weekdays|daily) WHAT»
    rf"{VERB}\s+(?P<when>(?:today|tomorrow|next\s+\w+|on\s+\w+(?:day)?|weekdays|daily)(?:\s+at\s+.+)?)\s+"
    rf"(?P<what>{Q}?[^\"'“”«»]+{Q}?)",
    # 5) Every/каждый/щодня + время (любые языки): «напомни WHAT каждый … в 9:00»
    rf"{VERB}\s+(?P<what>{Q}?[^\"'“”«»]+?{Q}?)\s+"
    rf"(?P<when>(?:кажд\w+|щодня|щотижня|щос\w+|every|weekdays|daily)(?:\s+(?:в|о|at)\s+.+)?)",
    # 6) Будний/день недели + время: «напомни отчёт среду в 18:30» / «… по будням в 10»
    rf"{VERB}\s+(?P<what>{Q}?[^\"'“”«»]+?{Q}?)\s+"
    rf"(?P<when>(?:по\s+будням|будням|weekdays|"
    rf"(?:понедельник|вторник|среда|среду|четверг|пятница|суббота|воскресенье)|"
    rf"(?:понеділок|вівторок|середа|четвер|пʼятниця|п'ятниця|субота|неділя)|"
    rf"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))"
    rf"(?:\s+(?:в|о|at)\s+.+)?)",
]

PATTERNS = [re.compile(p, re.I | re.U) for p in _RAW_PATTERNS]


def parse_remind(text: str) -> Optional[Tuple[str, str]]:
    """
    Возвращает (what, when) или None.
    Примеры распознаются:
      - «напомни позвонить маме завтра в 9»
      - «напомни через 2 часа "принять таблетки"»
      - «remind me to drink water weekdays at 10»
      - «нагадай "звіт" щосереди о 18:30»
      - «напомни отчёт по будням в 10»
      - «remind tomorrow at 7 "call John"»
    """
    s = (text or "").strip()
    if not s:
        return None
    for rx in PATTERNS:
        m = rx.search(s)
        if not m:
            continue
        what = _strip_quotes_punct(m.group("what"))
        when = _strip_quotes_punct(m.group("when"))
        if what and when:
            return what, when
    return None
