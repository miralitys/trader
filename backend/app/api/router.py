from fastapi import APIRouter

from app.api.routes import (
    auth,
    backtests,
    candles,
    health,
    instruments,
    positions,
    realtime,
    settings,
    signals,
    system,
    trades,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(instruments.router, tags=["instruments"])
api_router.include_router(candles.router, tags=["candles"])
api_router.include_router(signals.router, tags=["signals"])
api_router.include_router(positions.router, tags=["positions"])
api_router.include_router(trades.router, tags=["trades"])
api_router.include_router(backtests.router, tags=["backtests"])
api_router.include_router(settings.router, tags=["settings"])
api_router.include_router(system.router, tags=["system"])
api_router.include_router(health.router, tags=["health"])
api_router.include_router(realtime.router, tags=["realtime"])
