"""Secret encryption at rest (Fernet / AES-128-CBC + HMAC)."""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.exceptions import ConfigError


class CryptoService:
    def __init__(self, *keys: str) -> None:
        if not keys or not all(keys):
            raise ConfigError("ENCRYPTION_KEY is required")
        try:
            self._f = MultiFernet([Fernet(k.encode()) for k in keys])
        except Exception as exc:
            raise ConfigError("ENCRYPTION_KEY is not a valid Fernet key") from exc

    def encrypt(self, plaintext: str) -> str:
        return self._f.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        try:
            return self._f.decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ConfigError("Cannot decrypt stored secret (wrong ENCRYPTION_KEY?)") from exc

    @staticmethod
    def generate_key() -> str:
        return Fernet.generate_key().decode("ascii")
