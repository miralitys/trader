from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import Candle, Instrument
from app.services.audit_service import audit_log
from app.services.coinbase import coinbase_client

logger = logging.getLogger(__name__)

TIMEFRAME_MAP = {
    "5m": "FIVE_MINUTE",
    "15m": "FIFTEEN_MINUTE",
    "1h": "ONE_HOUR",
    "1d": "ONE_DAY",
}

LOOKBACK_WINDOWS = {
    "5m": timedelta(days=5),
    "15m": timedelta(days=14),
    "1h": timedelta(days=60),
}

LAST_SYNC_KEY = "trader:last_data_sync"
LAST_PRICE_PREFIX = "trader:last_price:"


def sync_instruments(db: Session) -> list[Instrument]:
    products = coinbase_client.get_products()
    upserted: list[Instrument] = []

    for product in products:
        product_id = product.get("product_id")
        if not product_id or not product_id.endswith("-USDC"):
            continue
        base = product_id.split("-")[0]
        symbol = product_id

        instrument = db.scalar(select(Instrument).where(Instrument.product_id == product_id))
        if not instrument:
            instrument = Instrument(
                symbol=symbol,
                base=base,
                quote="USDC",
                product_id=product_id,
                status=(product.get("trading_disabled") and "disabled") or "online",
                min_size=float(product.get("base_min_size") or 0),
                size_increment=float(product.get("base_increment") or 0.00000001),
                price_increment=float(product.get("quote_increment") or 0.00000001),
            )
            db.add(instrument)
        else:
            instrument.status = (product.get("trading_disabled") and "disabled") or "online"
            instrument.min_size = float(product.get("base_min_size") or instrument.min_size)
            instrument.size_increment = float(product.get("base_increment") or instrument.size_increment)
            instrument.price_increment = float(product.get("quote_increment") or instrument.price_increment)

        upserted.append(instrument)

    db.commit()
    return upserted


def _upsert_candle(
    db: Session,
    instrument_id: int,
    timeframe: str,
    ts: datetime,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float,
) -> None:
    existing = db.scalar(
        select(Candle).where(
            Candle.instrument_id == instrument_id,
            Candle.timeframe == timeframe,
            Candle.ts == ts,
        )
    )
    if existing:
        existing.open = open_
        existing.high = high
        existing.low = low
        existing.close = close
        existing.volume = volume
    else:
        db.add(
            Candle(
                instrument_id=instrument_id,
                timeframe=timeframe,
                ts=ts,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
                source="coinbase",
            )
        )


def ingest_candles(db: Session, symbols: list[str]) -> dict:
    now = datetime.now(timezone.utc)
    inserted = 0

    instruments = db.scalars(select(Instrument).where(Instrument.symbol.in_(symbols))).all()
    for instrument in instruments:
        for tf in ("5m", "15m", "1h"):
            start = now - LOOKBACK_WINDOWS[tf]
            end = now
            granularity = TIMEFRAME_MAP[tf]
            candles = coinbase_client.get_candles(instrument.product_id, granularity, start, end)
            for candle in candles:
                ts = datetime.fromtimestamp(candle["start"], tz=timezone.utc)
                _upsert_candle(
                    db,
                    instrument.id,
                    tf,
                    ts,
                    candle["open"],
                    candle["high"],
                    candle["low"],
                    candle["close"],
                    candle["volume"],
                )
                inserted += 1

            if candles:
                latest = candles[-1]
                price = latest["close"]
                r = redis.Redis.from_url(settings.redis_url, decode_responses=True)
                r.set(f"{LAST_PRICE_PREFIX}{instrument.symbol}", str(price))

    db.commit()
    redis.Redis.from_url(settings.redis_url, decode_responses=True).set(
        LAST_SYNC_KEY, now.isoformat()
    )
    return {"inserted": inserted, "synced_at": now.isoformat()}


def get_last_sync_info() -> tuple[datetime | None, int | None]:
    try:
        client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        value = client.get(LAST_SYNC_KEY)
        if not value:
            return None, None
        ts = datetime.fromisoformat(value)
        delay = int((datetime.now(timezone.utc) - ts).total_seconds())
        return ts, max(0, delay)
    except Exception:
        return None, None


def get_last_price(symbol: str) -> float | None:
    try:
        client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        value = client.get(f"{LAST_PRICE_PREFIX}{symbol}")
        return float(value) if value else None
    except Exception:
        return None
