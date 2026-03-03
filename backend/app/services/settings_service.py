from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import DEFAULT_UNIVERSE_INPUT, settings
from app.core.secrets import mask_key_id, secret_manager
from app.models.entities import Setting
from app.schemas.settings import DEFAULT_FEES, DEFAULT_RISK, DEFAULT_STRATEGY, DEFAULT_UNIVERSE


def _default_universe_payload() -> dict:
    payload = DEFAULT_UNIVERSE.copy()
    payload["input_tickers"] = DEFAULT_UNIVERSE_INPUT
    return payload


def create_default_settings(user_id: int) -> Setting:
    return Setting(
        user_id=user_id,
        paper_enabled=settings.paper_enabled,
        live_enabled=False,
        live_confirmed=False,
        risk_params_json=DEFAULT_RISK.copy(),
        strategy_params_json=DEFAULT_STRATEGY.copy(),
        universe_json=_default_universe_payload(),
        fees_json=DEFAULT_FEES.copy(),
        kill_switch_paused=False,
        strict_mode=False,
    )


def _merge_defaults(row: Setting) -> bool:
    changed = False

    risk = row.risk_params_json or {}
    merged_risk = DEFAULT_RISK.copy()
    merged_risk.update(risk)
    if merged_risk != risk:
        row.risk_params_json = merged_risk
        changed = True

    strategy = row.strategy_params_json or {}
    merged_strategy = DEFAULT_STRATEGY.copy()
    merged_strategy.update(strategy)
    if merged_strategy != strategy:
        row.strategy_params_json = merged_strategy
        changed = True

    fees = row.fees_json or {}
    merged_fees = DEFAULT_FEES.copy()
    merged_fees.update(fees)
    if merged_fees != fees:
        row.fees_json = merged_fees
        changed = True

    universe = row.universe_json or {}
    merged_universe = _default_universe_payload()
    merged_universe.update(universe)
    if merged_universe != universe:
        row.universe_json = merged_universe
        changed = True

    return changed


def ensure_user_settings(db: Session, user_id: int) -> Setting:
    stmt = select(Setting).where(Setting.user_id == user_id)
    row = db.scalar(stmt)
    if row:
        if _merge_defaults(row):
            db.add(row)
            db.commit()
            db.refresh(row)
        return row
    row = create_default_settings(user_id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_system_settings(db: Session) -> Setting | None:
    stmt = select(Setting).order_by(Setting.id.asc()).limit(1)
    row = db.scalar(stmt)
    if not row:
        return None
    if _merge_defaults(row):
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def update_settings_row(row: Setting, payload: dict) -> Setting:
    if payload.get("paper_enabled") is not None:
        row.paper_enabled = bool(payload["paper_enabled"])
    if payload.get("live_enabled") is not None:
        row.live_enabled = bool(payload["live_enabled"])
        row.live_confirmed = bool(payload["live_enabled"])

    if payload.get("risk_params_json"):
        merged = row.risk_params_json.copy()
        merged.update(payload["risk_params_json"])
        row.risk_params_json = merged

    if payload.get("strategy_params_json"):
        merged = row.strategy_params_json.copy()
        merged.update(payload["strategy_params_json"])
        row.strategy_params_json = merged

    if payload.get("universe_json"):
        merged = row.universe_json.copy()
        merged.update(payload["universe_json"])
        row.universe_json = merged

    if payload.get("fees_json"):
        merged = row.fees_json.copy()
        merged.update(payload["fees_json"])
        row.fees_json = merged

    if payload.get("strict_mode") is not None:
        row.strict_mode = bool(payload["strict_mode"])

    api_key = payload.get("coinbase_api_key")
    api_secret = payload.get("coinbase_api_secret")
    if api_key and api_secret:
        if not secret_manager.can_encrypt():
            raise RuntimeError(
                "SECRET_ENCRYPTION_KEY not set; cannot store Coinbase keys in database"
            )
        row.coinbase_api_key_enc = secret_manager.encrypt(api_key)
        row.coinbase_api_secret_enc = secret_manager.encrypt(api_secret)
        row.coinbase_api_key_hint = mask_key_id(api_key)

    row.updated_at = datetime.now(timezone.utc)
    return row
