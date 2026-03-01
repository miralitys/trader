from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.secrets import load_coinbase_credentials
from app.db.session import get_db
from app.models.entities import User
from app.schemas.settings import SettingsOut, SettingsUpdate
from app.services.settings_service import ensure_user_settings, update_settings_row

router = APIRouter(prefix="/settings")


@router.get("", response_model=SettingsOut)
def get_settings(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SettingsOut:
    row = ensure_user_settings(db, user.id)
    return SettingsOut.model_validate(row)


@router.put("", response_model=SettingsOut)
def update_settings(
    payload: SettingsUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SettingsOut:
    row = ensure_user_settings(db, user.id)

    if payload.live_enabled:
        key, secret = load_coinbase_credentials(row.coinbase_api_key_enc, row.coinbase_api_secret_enc)
        key = payload.coinbase_api_key or key
        secret = payload.coinbase_api_secret or secret
        if not key or not secret:
            raise HTTPException(
                status_code=400,
                detail="Live mode requires Coinbase API credentials (env or encrypted settings)",
            )

    try:
        update_settings_row(row, payload.model_dump(exclude_unset=True))
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db.add(row)
    db.commit()
    db.refresh(row)
    return SettingsOut.model_validate(row)
