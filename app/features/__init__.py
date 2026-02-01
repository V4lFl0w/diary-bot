from aiogram import Router

from .calories import router as calories_router
from .meditation import router as meditation_router
from .music import router as music_router

router = Router()
router.include_router(meditation_router)
router.include_router(music_router)
router.include_router(calories_router)
