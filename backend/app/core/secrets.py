from __future__ import annotations

import base64
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


class SecretManager:
    def __init__(self, encryption_key: Optional[str] = None) -> None:
        key = encryption_key or settings.secret_encryption_key
        self._fernet: Fernet | None = None
        if key:
            normalized = self._normalize_key(key)
            self._fernet = Fernet(normalized)

    def _normalize_key(self, key: str) -> bytes:
        if len(key) == 44:
            return key.encode("utf-8")
        padded = (key + "0" * 32)[:32].encode("utf-8")
        return base64.urlsafe_b64encode(padded)

    def can_encrypt(self) -> bool:
        return self._fernet is not None

    def encrypt(self, raw: str) -> str:
        if not self._fernet:
            raise RuntimeError("Secret encryption key is not configured")
        return self._fernet.encrypt(raw.encode("utf-8")).decode("utf-8")

    def decrypt(self, token: str) -> str:
        if not self._fernet:
            raise RuntimeError("Secret encryption key is not configured")
        try:
            return self._fernet.decrypt(token.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise RuntimeError("Failed to decrypt secret") from exc


secret_manager = SecretManager()


def mask_key_id(key_id: str) -> str:
    if not key_id:
        return ""
    return "***" + key_id[-4:]


def load_coinbase_credentials(
    stored_key_enc: str | None,
    stored_secret_enc: str | None,
) -> tuple[str, str]:
    api_key = settings.coinbase_api_key
    api_secret = settings.coinbase_api_secret

    if stored_key_enc and stored_secret_enc and secret_manager.can_encrypt():
        api_key = secret_manager.decrypt(stored_key_enc)
        api_secret = secret_manager.decrypt(stored_secret_enc)

    return api_key, api_secret
