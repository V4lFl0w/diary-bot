import json
import logging
import os
from contextvars import ContextVar
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler

# -------- structured context (per update) --------
_tg_id: ContextVar[int | None] = ContextVar("tg_id", default=None)
_chat_id: ContextVar[int | None] = ContextVar("chat_id", default=None)
_update_id: ContextVar[int | None] = ContextVar("update_id", default=None)


def set_log_context(tg_id: int | None = None, chat_id: int | None = None, update_id: int | None = None) -> None:
    _tg_id.set(tg_id)
    _chat_id.set(chat_id)
    _update_id.set(update_id)


def clear_log_context() -> None:
    _tg_id.set(None)
    _chat_id.set(None)
    _update_id.set(None)



class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Базовые поля
        obj = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # Если есть исключение — добавим
        if record.exc_info:
            obj["exc_info"] = self.formatException(record.exc_info)

        # Если кто-то передал extra={"tg_id":..., "update_id":...} — подтянем
        # (не ломаемся, просто добавляем)
        for k in ("tg_id", "user_id", "chat_id", "update_id", "handler", "event"):
            if hasattr(record, k):
                obj[k] = getattr(record, k)

        return json.dumps(obj, ensure_ascii=False)


def setup_logging():
    # inject contextvars into EVERY log record (incl. aiogram.*)
    old_factory = logging.getLogRecordFactory()

    def record_factory(*args, **kwargs):
        record = old_factory(*args, **kwargs)
        try:
            record.tg_id = _tg_id.get()
            record.chat_id = _chat_id.get()
            record.update_id = _update_id.get()
        except Exception:
            pass
        return record

    logging.setLogRecordFactory(record_factory)


    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    log_format = (os.getenv("LOG_FORMAT", "text") or "text").strip().lower()
    use_json = log_format in {"json", "structured", "jsonl"}

    text_fmt = "%(asctime)s %(levelname)s %(name)s %(message)s"
    text_formatter = logging.Formatter(text_fmt)
    json_formatter = JsonFormatter()

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    # stdout
    ch = logging.StreamHandler()
    ch.setLevel(level)
    ch.setFormatter(json_formatter if use_json else text_formatter)
    root.addHandler(ch)

    # файл (rotation)
    log_path = os.getenv("LOG_FILE", "logs/bot.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    fh = TimedRotatingFileHandler(
        log_path,
        when="D",
        interval=1,
        backupCount=14,
        encoding="utf-8",
    )
    fh.setLevel(level)
    fh.setFormatter(json_formatter if use_json else text_formatter)
    root.addHandler(fh)
