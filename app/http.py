from fastapi import APIRouter

router = APIRouter()

@router.get("/_version")
async def version():
    return {"ok": True}
