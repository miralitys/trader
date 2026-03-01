from __future__ import annotations

from datetime import timedelta

import redis
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.security import create_access_token, get_password_hash, verify_password
from app.db.session import get_db
from app.models.entities import User
from app.schemas.auth import LoginRequest, SignupRequest, TokenResponse, UserOut
from app.schemas.common import Message
from app.services.settings_service import ensure_user_settings

router = APIRouter()


def _login_rate_limit(email: str) -> None:
    try:
        client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        key = f"trader:login_attempt:{email}"
        attempts = client.incr(key)
        if attempts == 1:
            client.expire(key, 60)
        if attempts > 20:
            raise HTTPException(status_code=429, detail="Too many login attempts")
    except redis.RedisError:
        # Login should still work if Redis is temporarily unavailable.
        return


@router.post("/signup", response_model=UserOut)
def signup(payload: SignupRequest, db: Session = Depends(get_db)) -> UserOut:
    existing = db.scalar(select(User).where(User.email == payload.email.lower()))
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    first_user = db.scalar(select(User).order_by(User.id.asc()).limit(1))

    user = User(
        email=payload.email.lower(),
        password_hash=get_password_hash(payload.password),
        role="admin" if first_user is None else "user",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    ensure_user_settings(db, user.id)
    return UserOut.model_validate(user)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    _login_rate_limit(payload.email.lower())

    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = create_access_token(
        subject=user.id,
        expires_delta=timedelta(minutes=settings.jwt_expire_minutes),
    )
    return TokenResponse(access_token=token)


@router.post("/logout", response_model=Message)
def logout() -> Message:
    return Message(message="Logged out")


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)
