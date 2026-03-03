from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import Candle, Instrument
from app.services.coinbase import coinbase_client

logger = logging.getLogger(__name__)

TIMEFRAME_MAP = {
    "5m": "FIVE_MINUTE",
    "15m": "FIFTEEN_MINUTE",
    "1h": "ONE_HOUR",
    "1d": "ONE_DAY",
}

TIMEFRAME_SECONDS = {
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "1d": 86400,
}

LOOKBACK_WINDOWS = {
    "5m": timedelta(days=5),
    "15m": timedelta(days=14),
    "1h": timedelta(days=60),
}

# Coinbase candles endpoints reject overly large ranges per request.
MAX_CANDLES_PER_REQUEST = 300

LAST_SYNC_KEY = "trader:last_data_sync"
LAST_PRICE_PREFIX = "trader:last_price:"


def _redis_client() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def _fetch_candles_chunked(
    product_id: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    max_chunks: int | None = None,
) -> list[dict]:
    granularity = TIMEFRAME_MAP[timeframe]
    chunk_seconds = TIMEFRAME_SECONDS[timeframe] * MAX_CANDLES_PER_REQUEST
    candles_map: dict[int, dict] = {}
    cursor = start
    chunks = 0

    while cursor < end:
        if max_chunks is not None and chunks >= max_chunks:
            break
        next_cursor = min(cursor + timedelta(seconds=chunk_seconds), end)
        batch = coinbase_client.get_candles(
            product_id,
            granularity,
            cursor,
            next_cursor,
        )
        for candle in batch:
            candles_map[int(candle["start"])] = candle
        cursor = next_cursor
        chunks += 1

    return [candles_map[k] for k in sorted(candles_map)]


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
    redis_client = _redis_client()

    instruments = db.scalars(select(Instrument).where(Instrument.symbol.in_(symbols))).all()
    for instrument in instruments:
        for tf in ("5m", "15m", "1h"):
            start = now - LOOKBACK_WINDOWS[tf]
            end = now
            candles = _fetch_candles_chunked(
                instrument.product_id,
                tf,
                start,
                end,
            )
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
                redis_client.set(f"{LAST_PRICE_PREFIX}{instrument.symbol}", str(price))

    db.commit()
    redis_client.set(LAST_SYNC_KEY, now.isoformat())
    return {"inserted": inserted, "synced_at": now.isoformat()}


def _target_days_by_timeframe() -> dict[str, int]:
    return {
        "5m": max(1, int(settings.backfill_5m_days)),
        "15m": max(1, int(settings.backfill_15m_days)),
        "1h": max(1, int(settings.backfill_1h_days)),
    }


def _backfill_timeframe(
    db: Session,
    instrument: Instrument,
    timeframe: str,
    now: datetime,
    max_chunks: int,
) -> dict:
    target_start = now - timedelta(days=_target_days_by_timeframe()[timeframe])
    earliest_existing = db.scalar(
        select(func.min(Candle.ts)).where(
            Candle.instrument_id == instrument.id,
            Candle.timeframe == timeframe,
        )
    )

    if earliest_existing and earliest_existing <= target_start:
        return {
            "timeframe": timeframe,
            "inserted": 0,
            "chunks": 0,
            "status": "already_covered",
        }

    cursor_end = earliest_existing or now
    chunk_seconds = TIMEFRAME_SECONDS[timeframe] * MAX_CANDLES_PER_REQUEST
    inserted = 0
    chunks = 0
    empty_chunks = 0

    while cursor_end > target_start and chunks < max_chunks:
        cursor_start = max(target_start, cursor_end - timedelta(seconds=chunk_seconds))
        candles = coinbase_client.get_candles(
            instrument.product_id,
            TIMEFRAME_MAP[timeframe],
            cursor_start,
            cursor_end,
        )
        if candles:
            for candle in candles:
                ts = datetime.fromtimestamp(candle["start"], tz=timezone.utc)
                _upsert_candle(
                    db,
                    instrument.id,
                    timeframe,
                    ts,
                    candle["open"],
                    candle["high"],
                    candle["low"],
                    candle["close"],
                    candle["volume"],
                )
            inserted += len(candles)
            empty_chunks = 0
        else:
            empty_chunks += 1
            if empty_chunks >= 2:
                break

        cursor_end = cursor_start
        chunks += 1

    return {
        "timeframe": timeframe,
        "inserted": inserted,
        "chunks": chunks,
        "status": "ok" if inserted > 0 else "no_new_data",
    }


def backfill_history(db: Session, symbols: list[str] | None = None) -> dict:
    now = datetime.now(timezone.utc)
    redis_client = _redis_client()
    max_symbols = max(1, int(settings.backfill_max_symbols_per_run))
    max_chunks = max(1, int(settings.backfill_max_chunks_per_tf))

    if symbols:
        base_query = (
            select(Instrument)
            .where(
                Instrument.symbol.in_(symbols),
                Instrument.quote == "USDC",
                Instrument.status == "online",
            )
            .order_by(Instrument.symbol.asc())
        )
    else:
        base_query = (
            select(Instrument)
            .where(Instrument.quote == "USDC", Instrument.status == "online")
            .order_by(Instrument.updated_at.desc())
        )

    instruments = db.scalars(base_query).all()[:max_symbols]

    total_inserted = 0
    by_symbol: list[dict] = []

    for instrument in instruments:
        symbol_report = {"symbol": instrument.symbol, "timeframes": []}
        for timeframe in ("5m", "15m", "1h"):
            tf_report = _backfill_timeframe(
                db=db,
                instrument=instrument,
                timeframe=timeframe,
                now=now,
                max_chunks=max_chunks,
            )
            symbol_report["timeframes"].append(tf_report)
            total_inserted += int(tf_report["inserted"])

        latest_5m = db.scalar(
            select(Candle)
            .where(Candle.instrument_id == instrument.id, Candle.timeframe == "5m")
            .order_by(Candle.ts.desc())
            .limit(1)
        )
        if latest_5m:
            redis_client.set(f"{LAST_PRICE_PREFIX}{instrument.symbol}", str(latest_5m.close))

        by_symbol.append(symbol_report)

    db.commit()
    redis_client.set(LAST_SYNC_KEY, now.isoformat())
    return {
        "status": "ok",
        "symbols_processed": len(instruments),
        "inserted": total_inserted,
        "synced_at": now.isoformat(),
        "details": by_symbol,
    }


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
