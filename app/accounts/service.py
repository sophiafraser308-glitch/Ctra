"""Account management: OAuth onboarding, connect/disconnect, sync, edit/remove. Demo/Live are always explicit."""
from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Any, Awaitable, Callable

from sqlalchemy import func, select

from app.audit import AuditService
from app.config import Settings
from app.core.enums import AccountStatus, BotStatus, Category, Environment, Severity
from app.core.exceptions import ConflictError, NotFoundError, SafetyError, ValidationFailed
from app.core.utils import new_token, utcnow
from app.ctrader.authentication import OAuthClient, TokenService
from app.ctrader.gateway import CTraderGateway
from app.database import Database
from app.logging import get_logger
from app.models import (Bot, ConnectionEvent, CredentialMetadata, OAuthState, Position, RiskProfile, RiskState,
                        TradingAccount)
from app.notifications.service import Notifier
from app.security.crypto import CryptoService
from app.workers.heartbeat import HeartbeatService

log = get_logger(Category.CTRADER)
OAUTH_TTL_MINUTES = 15


class AccountService:
    def __init__(self, db: Database, settings: Settings, audit: AuditService, notifier: Notifier, gateway: CTraderGateway,
                 tokens: TokenService, oauth: OAuthClient, crypto: CryptoService, hb: HeartbeatService) -> None:
        self.db, self.s, self.audit, self.notifier = db, settings, audit, notifier
        self.gateway, self.tokens, self.oauth, self.crypto, self.hb = gateway, tokens, oauth, crypto, hb
        # late-bound collaborators (set by container)
        self.recon: Any = None
        self.positions: Any = None
        self.risk: Any = None
        self.bots: Any = None
        self.on_oauth_authorized: Callable[[OAuthState], Awaitable[None]] | None = None
        self._ever_connected: set[str] = set()
        self._sync_debounce: dict[str, float] = {}
        gateway.state_handlers.append(self._on_gateway_state)
        tokens.on_refresh_failed = self._on_token_refresh_failed

    # ---- queries -----------------------------------------------------------
    async def list(self) -> list[TradingAccount]:
        async with self.db.session() as s:
            q = select(TradingAccount).where(TradingAccount.deleted_at.is_(None)).order_by(TradingAccount.created_at)
            return list((await s.execute(q)).scalars())

    async def get(self, account_id: str) -> TradingAccount:
        async with self.db.session() as s:
            acc = await s.get(TradingAccount, account_id)
        if acc is None or acc.deleted_at is not None:
            raise NotFoundError("Account not found")
        return acc

    async def counts(self) -> dict[str, int]:
        accs = await self.list()
        return {"total": len(accs), "demo": sum(a.environment == "DEMO" for a in accs),
                "live": sum(a.environment == "LIVE" for a in accs),
                "connected": sum(a.status == "CONNECTED" for a in accs)}

    async def default_risk_profile(self) -> RiskProfile:
        async with self.db.session() as s:
            p = (await s.execute(select(RiskProfile).where(RiskProfile.is_default.is_(True)))).scalars().first()
            if p is None:
                p = RiskProfile(name="Default", is_default=True)
                s.add(p)
                await s.flush()
            return p

    # ---- OAuth onboarding ---------------------------------------------------------
    async def start_oauth(self, user_id: int, chat_id: int, name: str, environment: str) -> tuple[str, str]:
        env = environment.upper()
        if env not in ("DEMO", "LIVE"):
            raise ValidationFailed("Environment must be DEMO or LIVE")
        if not name or len(name) > 64:
            raise ValidationFailed("Account name must be 1-64 characters")
        state = new_token(18)
        url = self.oauth.authorization_url(state, self.s.ctrader_scope)
        async with self.db.session() as s:
            s.add(OAuthState(state=state, telegram_id=user_id, chat_id=chat_id, account_name=name.strip(), environment=env,
                             scope=self.s.ctrader_scope, expires_at=utcnow() + timedelta(minutes=OAUTH_TTL_MINUTES)))
        await self.audit.record(action="ACCOUNT_OAUTH_STARTED", user_id=user_id, target=name, new_state={"environment": env})
        return state, url

    async def handle_callback(self, state: str | None, code: str | None, error: str | None) -> tuple[bool, str]:
        """Called by the HTTP redirect endpoint. Returns (ok, human message for the browser page)."""
        async with self.db.session() as s:
            row = await self._resolve_state(s, state)
            if row is None:
                return False, "Unknown or expired authorization request. Start again from Telegram."
            if row.status != "PENDING":
                return False, "This authorization link was already used."
            if row.expires_at < utcnow():
                row.status, row.error = "EXPIRED", "expired"
                return False, "Authorization request expired. Start again from Telegram."
            st = row.state
            snapshot = (row.telegram_id, row.chat_id, row.environment, row.scope)
        if error or not code:
            await self._fail_state(st, f"Authorization denied: {error or 'no code'}")
            return False, "Authorization was not granted."
        try:
            tokens = await self.oauth.exchange_code(code)          # authorization code is single-use
            accounts = await self.gateway.list_accounts_by_token(snapshot[2], tokens["access_token"])
        except Exception as exc:
            await self._fail_state(st, str(exc)[:300])
            return False, "Could not complete authorization. Check Telegram for details."
        want_live = snapshot[2] == "LIVE"
        usable = [a for a in accounts if a["is_live"] == want_live]
        async with self.db.session() as s:
            row = await s.get(OAuthState, st)
            row.status = "AUTHORIZED"
            row.tokens_enc = self.crypto.encrypt(json.dumps(tokens))
            row.accounts_json = usable
            row.error = None
        await self.audit.record(action="ACCOUNT_OAUTH_AUTHORIZED", user_id=snapshot[0], target=st[:6],
                                new_state={"accounts_found": len(usable), "environment": snapshot[2]})
        if self.on_oauth_authorized:
            async with self.db.session() as s:
                row = await s.get(OAuthState, st)
            await self.on_oauth_authorized(row)
        return True, "Authorization successful. You can close this tab and return to Telegram."

    async def _resolve_state(self, s, state: str | None) -> OAuthState | None:
        if state:
            return await s.get(OAuthState, state)
        # cTrader may not echo `state`; accept only if exactly one pending request exists
        rows = (await s.execute(select(OAuthState).where(OAuthState.status == "PENDING", OAuthState.expires_at > utcnow()))).scalars().all()
        return rows[0] if len(rows) == 1 else None

    async def _fail_state(self, state: str, msg: str) -> None:
        async with self.db.session() as s:
            row = await s.get(OAuthState, state)
            if row:
                row.status, row.error = "FAILED", msg
                chat, uid = row.chat_id, row.telegram_id
        await self.audit.record(action="ACCOUNT_OAUTH_FAILED", user_id=uid, result="FAILED", error=msg)
        await self.notifier.notify("AUTH_FAILURE", f"cTrader authorization failed: {msg}", Severity.WARNING, dedup_key=f"oauth:{state}")

    async def oauth_state(self, state: str) -> OAuthState:
        async with self.db.session() as s:
            row = await s.get(OAuthState, state)
        if row is None:
            raise NotFoundError("Authorization request not found")
        return row

    async def pick_account(self, state: str, ctid: int, user_id: int) -> TradingAccount:
        """Finish onboarding: persist account + encrypted tokens, then connect."""
        row = await self.oauth_state(state)
        if row.telegram_id != user_id:
            raise SafetyError("This authorization belongs to another user")
        if row.status != "AUTHORIZED" or not row.tokens_enc:
            raise ConflictError("Authorization is not ready or was already used")
        match = next((a for a in (row.accounts_json or []) if int(a["ctid"]) == int(ctid)), None)
        if match is None:
            raise NotFoundError("Selected account is not part of this authorization")
        env = row.environment
        if bool(match["is_live"]) != (env == "LIVE"):
            raise SafetyError("Demo/Live mismatch between selected account and requested environment")
        tokens = json.loads(self.crypto.decrypt(row.tokens_enc))
        profile = await self.default_risk_profile()
        async with self.db.session() as s:
            dup = (await s.execute(select(TradingAccount).where(TradingAccount.ctid_trader_account_id == ctid,
                                                                TradingAccount.environment == env,
                                                                TradingAccount.deleted_at.is_(None)))).scalar_one_or_none()
            if dup:
                raise ConflictError(f"Account {ctid} is already added as '{dup.name}'")
            acc = TradingAccount(name=row.account_name, environment=env, ctid_trader_account_id=int(ctid),
                                 trader_login=match.get("login"), status="DISCONNECTED", risk_profile_id=profile.id,
                                 created_by=user_id, trading_enabled=(env == "DEMO"))
            s.add(acc)
            await s.flush()
            r = await s.get(OAuthState, state)
            r.status, r.tokens_enc = "DONE", None
            acc_id = acc.id
        await self.tokens.store(acc_id, tokens, row.scope)
        await self.audit.record(action="ACCOUNT_ADDED", user_id=user_id, target=acc_id,
                                new_state={"name": row.account_name, "environment": env, "ctid": ctid, "scope": row.scope})
        try:
            await self.connect(acc_id, user_id)
        except Exception as exc:
            log.warning("initial connect failed for %s: %s", acc_id, str(exc)[:160])
        return await self.get(acc_id)

    # ---- connect / disconnect / sync ------------------------------------------------------
    async def _set_status(self, account_id: str, status: AccountStatus, error: str | None = None) -> str | None:
        async with self.db.session() as s:
            acc = await s.get(TradingAccount, account_id)
            if acc is None:
                return None
            prev = acc.status
            acc.status = status.value
            if error is not None or status == AccountStatus.CONNECTED:
                acc.last_error = error
            s.add(ConnectionEvent(account_id=account_id, environment=acc.environment, event=status.value, detail=(error or "")[:500]))
            return prev

    async def connect(self, account_id: str, user_id: int | None = None) -> TradingAccount:
        acc = await self.get(account_id)
        await self._set_status(account_id, AccountStatus.CONNECTING)
        try:
            await self.gateway.connect_account(account_id, acc.ctid_trader_account_id, acc.environment)
        except Exception as exc:
            await self._set_status(account_id, AccountStatus.ERROR, str(exc)[:300])
            await self.audit.record(action="ACCOUNT_CONNECT_FAILED", user_id=user_id, target=account_id, result="FAILED", error=str(exc)[:300])
            raise
        await self.audit.record(action="ACCOUNT_CONNECTED", user_id=user_id, target=account_id, new_state={"environment": acc.environment})
        await self.refresh(account_id)
        return await self.get(account_id)

    async def disconnect(self, account_id: str, user_id: int | None = None) -> None:
        await self.get(account_id)
        await self.gateway.disconnect_account(account_id)
        await self._set_status(account_id, AccountStatus.DISCONNECTED)
        await self.audit.record(action="ACCOUNT_DISCONNECTED", user_id=user_id, target=account_id)

    async def reconnect(self, account_id: str, user_id: int | None = None) -> TradingAccount:
        try:
            await self.gateway.disconnect_account(account_id)
        except Exception:
            pass
        sess = self.gateway.sessions.get(account_id)
        if sess:
            sess.authed = False
        return await self.connect(account_id, user_id)

    async def connect_enabled_accounts(self) -> None:
        for acc in await self.list():
            if acc.enabled and acc.status != "AUTH_FAILED":
                try:
                    await self.connect(acc.id)
                except Exception as exc:
                    log.warning("startup connect failed account=%s: %s", acc.id, str(exc)[:160])

    async def refresh(self, account_id: str) -> dict[str, Any]:
        """Pull balance/equity/margin from the broker and persist; feed risk & position price state."""
        snap = await self.gateway.account_snapshot(account_id)
        now = utcnow()
        async with self.db.session() as s:
            acc = await s.get(TradingAccount, account_id)
            t = snap["trader"]
            acc.balance, acc.equity = snap["balance"], snap["equity"]
            acc.used_margin, acc.free_margin = snap["used_margin"], snap["free_margin"]
            acc.unrealized_pnl, acc.last_sync_at = snap["unrealized"], now
            acc.money_digits, acc.leverage = t.money_digits, t.leverage or acc.leverage
            acc.broker = t.broker_name or acc.broker
            acc.currency = snap.get("currency") or acc.currency
            acc.trader_login = t.trader_login or acc.trader_login
            acc.status = "CONNECTED"
        self.hb.update("ctrader", operation=f"sync {account_id}", ctrader_event=True)
        if self.risk:
            await self.risk.update_account_state(account_id, balance=snap["balance"], equity=snap["equity"])
        if self.positions:
            await self.positions.apply_pnl(account_id, snap["positions"], snap["pnl"])
        return snap

    async def debounced_refresh(self, account_id: str, min_interval: float = 3.0) -> None:
        import time
        now = time.monotonic()
        if now - self._sync_debounce.get(account_id, 0) < min_interval:
            return
        self._sync_debounce[account_id] = now
        try:
            await self.refresh(account_id)
        except Exception as exc:
            log.warning("refresh failed account=%s: %s", account_id, str(exc)[:120])

    async def periodic_sync(self) -> None:
        for acc in await self.list():
            if self.gateway.is_connected(acc.id):
                await self.debounced_refresh(acc.id, 20.0)

    async def refresh_tokens(self) -> None:
        await self.tokens.refresh_all_due([a.id for a in await self.list()])

    # ---- gateway callbacks ---------------------------------------------------------------------
    async def _on_gateway_state(self, account_id: str, event: str, detail: str) -> None:
        try:
            async with self.db.session() as s:
                acc = await s.get(TradingAccount, account_id)
            if acc is None or acc.deleted_at is not None:
                return
            tag = f"{acc.name} ({acc.environment})"
            if event == "CONNECTED":
                prev = await self._set_status(account_id, AccountStatus.CONNECTED)
                if account_id in self._ever_connected and prev != "CONNECTED":
                    await self.notifier.notify("CTRADER_RECONNECT", f"Reconnected: {tag}", Severity.NOTICE, dedup_key=f"reconn:{account_id}", throttle_seconds=60)
                    if self.recon:
                        asyncio.create_task(self.recon.reconcile(account_id, "reconnect"))
                self._ever_connected.add(account_id)
            elif event == "DISCONNECTED":
                if detail != "manual":
                    await self._set_status(account_id, AccountStatus.DISCONNECTED, detail)
                    await self.notifier.notify("CTRADER_DISCONNECT", f"cTrader disconnected: {tag}\n{detail}", Severity.WARNING, dedup_key=f"disc:{account_id}")
            elif event in ("AUTH_FAILED", "TOKEN_FAILED", "TOKEN_INVALIDATED"):
                await self._set_status(account_id, AccountStatus.AUTH_FAILED, detail)
                await self.notifier.notify("AUTH_FAILURE", f"Authentication problem for {tag}: {event}\n{detail}\nRe-authorize the account.", Severity.ERROR, dedup_key=f"auth:{account_id}")
            elif event == "ERROR":
                await self._set_status(account_id, AccountStatus.ERROR, detail)
            elif event == "TRADER_UPDATED":
                asyncio.create_task(self.debounced_refresh(account_id))
            elif event == "MARGIN_CALL":
                await self.notifier.notify("MARGIN_CALL", f"Margin call trigger on {tag}", Severity.CRITICAL, dedup_key=f"mc:{account_id}", throttle_seconds=600)
        except Exception as exc:
            log.error("gateway state handler failed: %s", type(exc).__name__, exc_info=True)

    async def _on_token_refresh_failed(self, account_id: str, message: str) -> None:
        await self.notifier.notify("TOKEN_REFRESH_FAILURE", f"Token refresh failed for account {account_id}: {message}\nRe-authorize the account.", Severity.ERROR, dedup_key=f"tok:{account_id}")

    # ---- edit / remove ----------------------------------------------------------------------------
    async def rename(self, account_id: str, name: str, user_id: int) -> None:
        name = name.strip()
        if not (1 <= len(name) <= 64):
            raise ValidationFailed("Name must be 1-64 characters")
        async with self.db.session() as s:
            acc = await s.get(TradingAccount, account_id)
            prev, acc.name = acc.name, name
        await self.audit.record(action="ACCOUNT_RENAMED", user_id=user_id, target=account_id, previous_state={"name": prev}, new_state={"name": name})

    async def set_trading_enabled(self, account_id: str, enabled: bool, user_id: int) -> None:
        async with self.db.session() as s:
            acc = await s.get(TradingAccount, account_id)
            prev, acc.trading_enabled = acc.trading_enabled, enabled
        await self.audit.record(action="ACCOUNT_TRADING_PERMISSION", user_id=user_id, target=account_id, previous_state={"trading_enabled": prev}, new_state={"trading_enabled": enabled})

    async def set_risk_profile(self, account_id: str, profile_id: str, user_id: int) -> None:
        async with self.db.session() as s:
            acc = await s.get(TradingAccount, account_id)
            if await s.get(RiskProfile, profile_id) is None:
                raise NotFoundError("Risk profile not found")
            prev, acc.risk_profile_id = acc.risk_profile_id, profile_id
        await self.audit.record(action="ACCOUNT_RISK_PROFILE_SET", user_id=user_id, target=account_id, previous_state={"profile": prev}, new_state={"profile": profile_id})

    async def remove(self, account_id: str, user_id: int) -> None:
        async with self.db.session() as s:
            running = (await s.execute(select(func.count()).select_from(Bot).where(
                Bot.account_id == account_id, Bot.deleted_at.is_(None),
                Bot.status.in_(["RUNNING", "STARTING", "PAUSED", "RESTARTING", "LOCKED"])))).scalar_one()
            openpos = (await s.execute(select(func.count()).select_from(Position).where(
                Position.account_id == account_id, Position.status == "OPEN"))).scalar_one()
        if running:
            raise ConflictError(f"{running} bot(s) still active on this account — stop them first")
        if openpos:
            raise ConflictError(f"{openpos} open position(s) exist — close them first")
        await self.gateway.disconnect_account(account_id)
        self.gateway.forget_account(account_id)
        async with self.db.session() as s:
            acc = await s.get(TradingAccount, account_id)
            acc.deleted_at, acc.status, acc.enabled = utcnow(), "DISCONNECTED", False
            cred = (await s.execute(select(CredentialMetadata).where(CredentialMetadata.account_id == account_id))).scalar_one_or_none()
            if cred:
                await s.delete(cred)        # destroy stored tokens
        await self.audit.record(action="ACCOUNT_REMOVED", user_id=user_id, target=account_id)
