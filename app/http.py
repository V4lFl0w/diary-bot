from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz")
async def healthz():
    return {"ok": True}


@router.get("/_version")
async def version():
    return {"ok": True}


def _base() -> str:
    public = (
        (os.getenv("PUBLIC_URL") or os.getenv("PUBLIC_BASE_URL") or "")
        .strip()
        .rstrip("/")
    )
    if not public.startswith("http"):
        # чтобы сразу было понятно в логах
        raise HTTPException(
            status_code=500, detail="PUBLIC_URL/PUBLIC_BASE_URL not set"
        )
    return public


# --- MonoPay (restored from http.py.bak_fix_8000) ---
import os

import httpx
from fastapi import HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse


@router.get("/pay-mono")
async def pay_mono(tg_id: str = Query(...)):
    token = os.getenv("MONO_TOKEN", "")
    amount_uah = float(os.getenv("SUB_PRICE_UAH", "299"))
    amount = int(round(amount_uah * 100))
    success = _base() + "/payments/success"
    webhook = _base() + "/payments/mono-callback"
    if not token:
        return PlainTextResponse("Monobank merchant token missing", status_code=501)
    payload = {
        "amount": amount,
        "ccy": 980,
        "merchantPaymInfo": {
            "reference": f"tg_{tg_id}",
            "destination": "Diary Assistant Premium",
        },
        "redirectUrl": success,
        "webHookUrl": webhook,
    }
    async with httpx.AsyncClient(timeout=20) as cli:
        r = await cli.post(
            "https://api.monobank.ua/api/merchant/invoice/create",
            headers={"X-Token": token, "Content-Type": "application/json"},
            json=payload,
        )

    # DEBUG: всегда логируем, что пришло (коротко)
    body_text = (r.text or "")[:2000]

    try:
        data = r.json()
    except Exception:
        # Mono вернул не-JSON (HTML/пусто/текст)
        raise HTTPException(
            status_code=502,
            detail={
                "mono_status": r.status_code,
                "mono_body": body_text,
            },
        )

    if r.status_code != 200 or "pageUrl" not in data:
        raise HTTPException(
            status_code=502,
            detail={
                "mono_status": r.status_code,
                "mono_json": data,
                "mono_body": body_text,
            },
        )

    return RedirectResponse(data["pageUrl"], status_code=303)


@router.post("/payments/mono-callback")
async def mono_cb(body: dict):
    return {"ok": True}


@router.get("/payments/success", response_class=HTMLResponse)
async def ok():
    return "<h1>Оплачено ✅</h1>"


@router.get("/payments/cancel", response_class=HTMLResponse)
async def cancel():
    return "<h1>Платёж отменён</h1>"


# --- /MonoPay ---
