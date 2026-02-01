import base64

import httpx


async def trace_moe_identify(image_bytes: bytes) -> dict | None:
    """
    Identify anime frame using trace.moe
    Returns None if uncertain or failed
    """
    b64 = base64.b64encode(image_bytes).decode()

    payload = {"image": f"data:image/jpeg;base64,{b64}"}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post("https://api.trace.moe/search", json=payload)

        if r.status_code != 200:
            return None

        data = r.json()
        if not data.get("result"):
            return None

        top = data["result"][0]

        return {
            "title": top.get("filename"),
            "episode": top.get("episode"),
            "similarity": float(top.get("similarity", 0)),
            "preview": top.get("image"),
        }

    except Exception:
        return None
