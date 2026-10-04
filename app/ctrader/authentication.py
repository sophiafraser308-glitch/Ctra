"""cTrader Open API OAuth2: authorization URL, code exchange, refresh, and secure token storage."""
from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import select

from app.config import Settings
from app.core.enums import Category
from app.core.exceptions import TokenError
from app.core.utils import ensure_aware, utcnow
from app.database import Database
from app.logging import get_logger
from app.models import CredentialMetadata
from app.security.crypto import CryptoService

log = get_logger(Category.AUTH)


class OAuthClient:
    def __init__(self, settings: Settings) -> None:
        self.s = settings

    def authorization_url(self, state: str, scope: str) -> str:
        if not self.s.ctrader_configured:
            raise TokenError("cTrader application is not configured (CTRADER_CLIENT_ID/SECRET/REDIRECT_URI)")
        params = {"client_id": self.s.ctrader_client_id, "redirect_uri": self.s.ctrader_redirect_uri,
                  "scope": scope, "product": "web", "state": state}
        return f"{self.s.ctrader_auth_url}?{urlencode(params)}"

    async def _token_request(self, params: dict[str, str]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(self.s.ctrader_token_url, params=params)
        except httpx.HTTPError as exc:
            raise TokenError(f"Token endpoint unreachable: {type(exc).__name__}") from exc
        try:
            data = resp.json()
        except ValueError as exc:
            raise TokenError(f"Token endpoint returned non-JSON (HTTP {resp.status_code})") from exc
        err = data.get("errorCode") or data.get("error")
        if resp.status_code >= 400 or err:
            raise TokenError(f"Token request failed: {err or resp.status_code}: {data.get('description') or data.get('error_description') or ''}"[:300])
        access = data.get("accessToken") or data.get("access_token")
        if not access:
            raise TokenError("Token response did not contain an access token")
        return {
            "access_token": access,
            "refresh_token": data.get("refreshToken") or data.get("refresh_token"),
            "token_type": data.get("tokenType") or data.get("token_type") or "bearer",
            "expires_in": int(data.get("expiresIn") or data.get("expires_in") or 2_628_000),
        }

    async def exchange_code(self, code: str) -> dict[str, Any]:
        """Authorization code is single-use; never persisted."""
        return await self._token_request({
            "grant_type": "authorization_code", "code": code, "redirect_uri": self.s.ctrader_redirect_uri,
            "client_id": self.s.ctrader_client_id, "client_secret": self.s.ctrader_client_secret.get_secret_value()})

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        return await self._token_request({
            "grant_type": "refresh_token", "refresh_token": refresh_token,
            "client_id": self.s.ctrader_client_id, "client_secret": self.s.ctrader_client_secret.get_secret_value()})


class TokenService:
    """Stores tokens encrypted; refreshes before expiry; serialises refreshes per account."""

    def __init__(self, db: Database, crypto: CryptoService, oauth: OAuthClient, settings: Settings) -> None:
        self.db, self.crypto, self.oauth, self.s = db, crypto, oauth, settings
        self._locks: dict[str, asyncio.Lock] = {}
        self.on_refresh_failed: Any = None  # async callable(account_id, message)

    async def store(self, account_id: str, tokens: dict[str, Any], scope: str | None) -> None:
        now = utcnow()
        async with self.db.session() as s:
            row = (await s.execute(select(CredentialMetadata).where(CredentialMetadata.account_id == account_id))).scalar_one_or_none()
            vals = dict(access_token_enc=self.crypto.encrypt(tokens["access_token"]),
                        refresh_token_enc=self.crypto.encrypt(tokens["refresh_token"]) if tokens.get("refresh_token") else (row.refresh_token_enc if row else None),
                        token_type=tokens.get("token_type"), scope=scope or (row.scope if row else None),
                        expires_at=now + timedelta(seconds=int(tokens.get("expires_in", 0))), refreshed_at=now,
                        status="VALID", last_error=None)
            if row:
                for k, v in vals.items():
                    setattr(row, k, v)
            else:
                s.add(CredentialMetadata(account_id=account_id, **vals))

    async def _load(self, account_id: str) -> CredentialMetadata:
        async with self.db.session() as s:
            row = (await s.execute(select(CredentialMetadata).where(CredentialMetadata.account_id == account_id))).scalar_one_or_none()
        if row is None:
            raise TokenError("No credentials stored for this account")
        return row

    async def get_access_token(self, account_id: str, *, force_refresh: bool = False) -> str:
        """Return a valid access token, refreshing if (nearly) expired."""
        row = await self._load(account_id)
        exp = ensure_aware(row.expires_at)
        near = exp is None or (exp - utcnow()).total_seconds() < self.s.token_refresh_margin_seconds
        if force_refresh or near:
            async with self._locks.setdefault(account_id, asyncio.Lock()):
                row = await self._load(account_id)  # another task may have refreshed meanwhile
                exp = ensure_aware(row.expires_at)
                still_near = exp is None or (exp - utcnow()).total_seconds() < self.s.token_refresh_margin_seconds
                if force_refresh or still_near:
                    await self._refresh(account_id, row)
                    row = await self._load(account_id)
        return self.crypto.decrypt(row.access_token_enc)

    async def _refresh(self, account_id: str, row: CredentialMetadata) -> None:
        if not row.refresh_token_enc:
            await self._mark(account_id, "REFRESH_FAILED", "No refresh token stored")
            raise TokenError("No refresh token stored; re-authorize the account")
        try:
            tokens = await self.oauth.refresh(self.crypto.decrypt(row.refresh_token_enc))
        except TokenError as exc:
            await self._mark(account_id, "REFRESH_FAILED", str(exc))
            log.error("token refresh failed account=%s: %s", account_id, exc)
            if self.on_refresh_failed:
                try:
                    await self.on_refresh_failed(account_id, str(exc))
                except Exception:
                    pass
            raise
        await self.store(account_id, tokens, row.scope)
        log.info("token refreshed account=%s", account_id)

    async def _mark(self, account_id: str, status: str, err: str) -> None:
        async with self.db.session() as s:
            row = (await s.execute(select(CredentialMetadata).where(CredentialMetadata.account_id == account_id))).scalar_one_or_none()
            if row:
                row.status, row.last_error = status, err[:500]

    async def refresh_all_due(self, account_ids: list[str]) -> None:
        for aid in account_ids:
            try:
                await self.get_access_token(aid)
            except Exception as exc:
                log.warning("periodic token check failed account=%s: %s", aid, type(exc).__name__)

    async def scope_of(self, account_id: str) -> str | None:
        return (await self._load(account_id)).scope
