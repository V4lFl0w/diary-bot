from __future__ import annotations

# app/services/assistant.py
import logging

log = logging.getLogger("media")

def _d(event: str, **kw) -> None:
    """Structured debug logger for media/vision pipeline."""
    safe = {}
    for k, v in kw.items():
        try:
            import json as _json

            _json.dumps(v, ensure_ascii=False, default=str)
            safe[k] = v
        except Exception:
            safe[k] = str(v)
    try:
        log.info("[media] %s | %s", event, safe)
    except Exception:
        pass
