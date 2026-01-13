# app/urls.py
import os

def pay_url(tg_id: int) -> str | None:
    public = (os.getenv("PUBLIC_URL") or "").strip().rstrip("/")
    if not public.startswith("http"):
        return None
    return f"{public}/pay?tg_id={tg_id}"

# Backward-compatible alias (used by premium.py)
public_pay_url = pay_url
