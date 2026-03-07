from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Instrument, Setting
from app.services.coinbase import coinbase_client

logger = logging.getLogger(__name__)


def _normalize_input_tickers(value: object) -> list[str]:
    if not isinstance(value, list):
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        ticker = str(item).strip().upper()
        if ticker.endswith("-USDC"):
            ticker = ticker[:-5]
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        normalized.append(ticker)
    return normalized


def compute_30d_quote_volume(product_id: str) -> float:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=30)
    try:
        candles = coinbase_client.get_candles(product_id, "ONE_DAY", start, end)
        quote_volume = 0.0
        for c in candles:
            quote_volume += c["volume"] * c["close"]
        return quote_volume
    except Exception as exc:
        logger.error(
            "universe_volume_fetch_failed",
            extra={"context": {"product_id": product_id, "error": str(exc)}},
        )
        return 0.0


def recompute_universe(db: Session, setting: Setting) -> dict:
    input_tickers = _normalize_input_tickers(setting.universe_json.get("input_tickers", []))
    instruments = db.scalars(
        select(Instrument).where(
            Instrument.base.in_(input_tickers),
            Instrument.quote == "USDC",
            Instrument.status == "online",
        )
    ).all()

    ranking: list[dict] = []
    for instrument in instruments:
        vol = compute_30d_quote_volume(instrument.product_id)
        ranking.append(
            {
                "symbol": instrument.symbol,
                "product_id": instrument.product_id,
                "base": instrument.base,
                "quote_volume_30d": vol,
            }
        )

    ranking.sort(key=lambda x: x["quote_volume_30d"], reverse=True)
    top = ranking[:5]

    payload = setting.universe_json.copy()
    payload["top_symbols"] = [x["symbol"] for x in top]
    payload["ranked"] = top
    payload["last_recomputed_at"] = datetime.now(timezone.utc).isoformat()
    payload["selection_basis"] = "30d_quote_volume"

    setting.universe_json = payload
    db.add(setting)
    db.commit()
    db.refresh(setting)
    return payload
