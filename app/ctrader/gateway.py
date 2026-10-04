"""CTraderGateway — the single facade the application uses to talk to cTrader.

Nothing outside app/ctrader touches protobuf. Telegram handlers never import this module directly;
they go through application services (accounts, orders, positions...).
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.config import Settings
from app.core.enums import Category
from app.core.exceptions import CTraderError, CTraderNotConnected, NotFoundError
from app.core.utils import utcnow
from app.ctrader import accounts as acc_proto
from app.ctrader import market_data as md_proto
from app.ctrader import orders as ord_proto
from app.ctrader import positions as pos_proto
from app.ctrader.authentication import TokenService
from app.ctrader.connection import ConnState, CTraderConnectionManager
from app.ctrader.pb import sdk
from app.ctrader.types import (AccountSession, BarData, BrokerDeal, BrokerOrder, BrokerPosition, BrokerTrader,
                               ExecutionEventData, SymbolInfo, TickEvent)
from app.logging import get_logger

log = get_logger(Category.CTRADER)

TickHandler = Callable[[str, TickEvent], None]
ExecHandler = Callable[[str, ExecutionEventData], Awaitable[None]]
StateHandler = Callable[[str, str, str], Awaitable[None]]   # (account_id, event, detail)


def _norm(name: str) -> str:
    return name.upper().replace("/", "").replace(" ", "")


def _is_auth_error(exc: CTraderError) -> bool:
    c = (exc.broker_code or "").upper()
    return any(k in c for k in ("TOKEN", "NOT_AUTHENTICATED", "ACCOUNT_NOT_AUTHORIZED", "CH_CLIENT_AUTH"))


class CTraderGateway:
    def __init__(self, settings: Settings, tokens: TokenService) -> None:
        self.s = settings
        self.tokens = tokens
        self.managers: dict[str, CTraderConnectionManager] = {}
        self.sessions: dict[str, AccountSession] = {}
        self._by_ctid: dict[tuple[str, int], str] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.tick_handlers: list[TickHandler] = []
        self.execution_handlers: list[ExecHandler] = []
        self.state_handlers: list[StateHandler] = []
        self.last_event_at: datetime | None = None
        self._bg: set[asyncio.Task] = set()

    # ---- infrastructure ------------------------------------------------
    def _spawn(self, coro: Awaitable[Any]) -> None:
        t = asyncio.ensure_future(coro)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)

    async def manager(self, env: str) -> CTraderConnectionManager:
        env = env.upper()
        mgr = self.managers.get(env)
        if mgr is None:
            if not self.s.ctrader_configured:
                raise CTraderError("cTrader application not configured (CTRADER_CLIENT_ID / SECRET / REDIRECT_URI)")
            mgr = CTraderConnectionManager(self.s, env)
            mgr.on_message = lambda m, e=env: self._dispatch(e, m)
            mgr.on_ready = lambda e=env: self._on_ready(e)
            mgr.on_down = lambda reason, e=env: self._on_down(e, reason)
            self.managers[env] = mgr
        await mgr.start()
        return mgr

    async def wait_ready(self, env: str, timeout: float = 30.0) -> CTraderConnectionManager:
        mgr = await self.manager(env)
        try:
            await asyncio.wait_for(mgr._ready.wait(), timeout)
        except asyncio.TimeoutError as exc:
            raise CTraderNotConnected(f"{env} connection not ready: {mgr.last_error or mgr.state.value}") from exc
        return mgr

    async def shutdown(self) -> None:
        for aid, sess in list(self.sessions.items()):
            sess.desired = False
            if sess.authed:
                try:
                    mgr = self.managers.get(sess.environment)
                    if mgr and mgr.is_ready:
                        r = sdk().msgs.ProtoOAAccountLogoutReq()
                        r.ctidTraderAccountId = sess.ctid
                        await mgr.request(r, timeout=3)
                except Exception:
                    pass
            sess.authed = False
        for mgr in self.managers.values():
            await mgr.stop()
        for t in list(self._bg):
            t.cancel()

    # ---- session helpers -------------------------------------------------
    def session(self, account_id: str) -> AccountSession:
        sess = self.sessions.get(account_id)
        if sess is None:
            raise NotFoundError("Account has no active cTrader session")
        return sess

    def is_connected(self, account_id: str) -> bool:
        s = self.sessions.get(account_id)
        if not s or not s.authed:
            return False
        m = self.managers.get(s.environment)
        return bool(m and m.is_ready)

    async def _emit_state(self, account_id: str, event: str, detail: str = "") -> None:
        for h in list(self.state_handlers):
            try:
                await h(account_id, event, detail)
            except Exception as exc:
                log.error("state handler error: %s", type(exc).__name__)

    # ---- connect / disconnect ---------------------------------------------
    async def connect_account(self, account_id: str, ctid: int, environment: str) -> AccountSession:
        env = environment.upper()
        sess = self.sessions.get(account_id)
        if sess is None:
            sess = AccountSession(account_id=account_id, ctid=ctid, environment=env)
            self.sessions[account_id] = sess
            self._by_ctid[(env, ctid)] = account_id
        sess.desired = True
        await self.wait_ready(env)
        await self._auth_session(sess, raise_errors=True)
        return sess

    async def disconnect_account(self, account_id: str) -> None:
        sess = self.sessions.get(account_id)
        if not sess:
            return
        sess.desired = False
        mgr = self.managers.get(sess.environment)
        if sess.authed and mgr and mgr.is_ready:
            try:
                if sess.subscribed:
                    await mgr.request(md_proto.build_unsubscribe_spots(sess.ctid, list(sess.subscribed)), timeout=5)
                r = sdk().msgs.ProtoOAAccountLogoutReq()
                r.ctidTraderAccountId = sess.ctid
                await mgr.request(r, timeout=5)
            except Exception as exc:
                log.warning("logout error account=%s: %s", account_id, type(exc).__name__)
        sess.authed = False
        sess.subscribed.clear()
        await self._emit_state(account_id, "DISCONNECTED", "manual")

    def forget_account(self, account_id: str) -> None:
        sess = self.sessions.pop(account_id, None)
        if sess:
            self._by_ctid.pop((sess.environment, sess.ctid), None)

    async def _on_ready(self, env: str) -> None:
        for sess in list(self.sessions.values()):
            if sess.environment == env and sess.desired:
                self._spawn(self._auth_session(sess, raise_errors=False))

    async def _on_down(self, env: str, reason: str) -> None:
        for sess in self.sessions.values():
            if sess.environment == env and sess.authed:
                sess.authed = False
                self._spawn(self._emit_state(sess.account_id, "DISCONNECTED", reason))

    async def _auth_session(self, sess: AccountSession, *, raise_errors: bool) -> None:
        lock = self._locks.setdefault(sess.account_id, asyncio.Lock())
        async with lock:
            if sess.authed:
                return
            mgr = self.managers.get(sess.environment)
            if mgr is None or not mgr.is_ready:
                if raise_errors:
                    raise CTraderNotConnected("transport not ready")
                return
            await self._emit_state(sess.account_id, "CONNECTING", "")
            try:
                for attempt in (0, 1):
                    token = await self.tokens.get_access_token(sess.account_id, force_refresh=bool(attempt))
                    try:
                        await mgr.request(acc_proto.build_account_auth(sess.ctid, token))
                        break
                    except CTraderError as exc:
                        if attempt == 0 and _is_auth_error(exc):
                            log.warning("account auth rejected (%s); refreshing token once", exc.broker_code)
                            continue
                        raise
                sess.authed = True
                sess.last_event_at = utcnow()
                if not sess.symbols_by_id:
                    await self._load_symbols(sess)
                await self.refresh_trader(sess.account_id)
                if sess.subscribed:
                    await mgr.request(md_proto.build_subscribe_spots(sess.ctid, list(sess.subscribed)))
                await self._emit_state(sess.account_id, "CONNECTED", "")
            except Exception as exc:
                sess.authed = False
                ev = "AUTH_FAILED" if isinstance(exc, CTraderError) and _is_auth_error(exc) else "ERROR"
                if isinstance(exc, CTraderError) and exc.code == "TOKEN_ERROR":
                    ev = "TOKEN_FAILED"
                log.error("account session auth failed account=%s: %s", sess.account_id, str(exc)[:200])
                await self._emit_state(sess.account_id, ev, str(exc)[:200])
                if raise_errors:
                    raise

    # ---- requests (reads retry once after re-auth) ---------------------------
    async def _mgr_for(self, account_id: str) -> tuple[AccountSession, CTraderConnectionManager]:
        sess = self.session(account_id)
        mgr = self.managers.get(sess.environment)
        if not sess.authed or mgr is None or not mgr.is_ready:
            raise CTraderNotConnected("Account is not connected")
        return sess, mgr

    async def _read(self, account_id: str, req: Any, timeout: float | None = None) -> Any:
        sess, mgr = await self._mgr_for(account_id)
        try:
            return await mgr.request(req, timeout=timeout)
        except CTraderError as exc:
            if _is_auth_error(exc):
                sess.authed = False
                await self._auth_session(sess, raise_errors=True)
                return await mgr.request(req, timeout=timeout)
            raise

    # ---- discovery -------------------------------------------------------------
    async def list_accounts_by_token(self, environment: str, access_token: str) -> list[dict[str, Any]]:
        mgr = await self.wait_ready(environment)
        res = await mgr.request(acc_proto.build_account_list_req(access_token))
        return acc_proto.parse_account_list(res)

    async def _load_symbols(self, sess: AccountSession) -> None:
        mgr = self.managers[sess.environment]
        res = await mgr.request(acc_proto.build_symbols_list_req(sess.ctid), timeout=30)
        infos = acc_proto.parse_light_symbols(res)
        sess.symbols_by_id = {i.symbol_id: i for i in infos}
        sess.symbols_by_name = {_norm(i.name): i for i in infos}
        ares = await mgr.request(acc_proto.build_assets_req(sess.ctid), timeout=20)
        sess.assets = acc_proto.parse_assets(ares)
        log.info("loaded %d symbols for account %s", len(infos), sess.account_id)

    async def get_symbol(self, account_id: str, name: str) -> SymbolInfo:
        sess = self.session(account_id)
        info = sess.symbols_by_name.get(_norm(name))
        if info is None:
            raise NotFoundError(f"Symbol {name} not available on this account")
        if not info.detailed:
            await self.ensure_details(account_id, [info.symbol_id])
        return info

    def symbol_by_id(self, account_id: str, symbol_id: int) -> SymbolInfo | None:
        s = self.sessions.get(account_id)
        return s.symbols_by_id.get(symbol_id) if s else None

    async def ensure_details(self, account_id: str, ids: list[int]) -> None:
        sess = self.session(account_id)
        todo = [i for i in ids if i in sess.symbols_by_id and not sess.symbols_by_id[i].detailed]
        for k in range(0, len(todo), 50):
            res = await self._read(account_id, acc_proto.build_symbol_by_id_req(sess.ctid, todo[k:k + 50]), timeout=20)
            acc_proto.apply_symbol_details(res, sess.symbols_by_id)

    # ---- account data ------------------------------------------------------------
    async def refresh_trader(self, account_id: str) -> BrokerTrader:
        sess, mgr = await self._mgr_for_loose(account_id)
        res = await mgr.request(acc_proto.build_trader_req(sess.ctid))
        sess.trader = acc_proto.parse_trader(res)
        return sess.trader

    async def _mgr_for_loose(self, account_id: str) -> tuple[AccountSession, CTraderConnectionManager]:
        sess = self.session(account_id)
        mgr = self.managers.get(sess.environment)
        if mgr is None or not mgr.is_ready:
            raise CTraderNotConnected("transport not ready")
        return sess, mgr

    async def reconcile_snapshot(self, account_id: str) -> tuple[list[BrokerPosition], list[BrokerOrder]]:
        sess = self.session(account_id)
        res = await self._read(account_id, pos_proto.build_reconcile_req(sess.ctid), timeout=30)
        return [pos_proto.parse_position(p) for p in res.position], [ord_proto.parse_order(o) for o in res.order]

    async def unrealized_pnl(self, account_id: str) -> dict[int, float]:
        sess = self.session(account_id)
        res = await self._read(account_id, acc_proto.build_unrealized_req(sess.ctid))
        return acc_proto.parse_unrealized(res)

    async def account_snapshot(self, account_id: str) -> dict[str, Any]:
        """Balance/equity/margin + live positions/orders straight from the broker."""
        trader = await self.refresh_trader(account_id)
        positions, orders = await self.reconcile_snapshot(account_id)
        try:
            pnl = await self.unrealized_pnl(account_id)
        except CTraderError:
            pnl = {}
        sess = self.session(account_id)
        unreal = sum(pnl.values())
        used = sum(p.used_margin for p in positions)
        equity = trader.balance + unreal
        return {"trader": trader, "positions": positions, "orders": orders, "pnl": pnl,
                "balance": trader.balance, "equity": equity, "unrealized": unreal, "used_margin": used,
                "free_margin": equity - used, "currency": sess.assets.get(trader.deposit_asset_id or -1)}

    async def order_history(self, account_id: str, from_ms: int, to_ms: int) -> list[BrokerOrder]:
        sess = self.session(account_id)
        res = await self._read(account_id, ord_proto.build_order_list_req(sess.ctid, from_ms, to_ms), timeout=30)
        return [ord_proto.parse_order(o) for o in res.order]

    async def deal_history(self, account_id: str, from_ms: int, to_ms: int) -> list[BrokerDeal]:
        sess = self.session(account_id)
        res = await self._read(account_id, pos_proto.build_deals_req(sess.ctid, from_ms, to_ms), timeout=30)
        return [pos_proto.parse_deal(d) for d in res.deal]

    # ---- trading (writes are NEVER auto-retried) ---------------------------------------
    async def new_order(self, account_id: str, sym: SymbolInfo, **kw: Any) -> ExecutionEventData:
        sess, mgr = await self._mgr_for(account_id)
        req = ord_proto.build_new_order(sess.ctid, sym, **kw)
        res = await mgr.request(req)
        if type(res).__name__ != "ProtoOAExecutionEvent":
            raise CTraderError(f"Unexpected response {type(res).__name__} to new order")
        return ord_proto.parse_execution_event(res)

    async def cancel_order(self, account_id: str, order_id: int) -> None:
        sess, mgr = await self._mgr_for(account_id)
        await mgr.request(ord_proto.build_cancel_order(sess.ctid, order_id))

    async def amend_sltp(self, account_id: str, position_id: int, sl: float | None, tp: float | None) -> None:
        sess, mgr = await self._mgr_for(account_id)
        await mgr.request(pos_proto.build_amend_sltp_req(sess.ctid, position_id, sl, tp))

    async def close_position(self, account_id: str, position_id: int, protocol_volume: int) -> ExecutionEventData | None:
        sess, mgr = await self._mgr_for(account_id)
        res = await mgr.request(pos_proto.build_close_req(sess.ctid, position_id, protocol_volume))
        if type(res).__name__ == "ProtoOAExecutionEvent":
            return ord_proto.parse_execution_event(res)
        return None

    # ---- market data -----------------------------------------------------------------------
    async def subscribe_spots(self, account_id: str, symbol_ids: list[int]) -> None:
        sess = self.session(account_id)
        new = [i for i in symbol_ids if i not in sess.subscribed]
        sess.subscribed.update(symbol_ids)
        if new and sess.authed:
            await self._read(account_id, md_proto.build_subscribe_spots(sess.ctid, new))

    async def unsubscribe_spots(self, account_id: str, symbol_ids: list[int]) -> None:
        sess = self.sessions.get(account_id)
        if not sess:
            return
        gone = [i for i in symbol_ids if i in sess.subscribed]
        sess.subscribed.difference_update(gone)
        if gone and sess.authed:
            try:
                await self._read(account_id, md_proto.build_unsubscribe_spots(sess.ctid, gone))
            except CTraderError:
                pass

    async def get_bars(self, account_id: str, symbol_id: int, tf: str, count: int = 200) -> list[BarData]:
        sess = self.session(account_id)
        now_ms = int(time.time() * 1000)
        span = md_proto.TF_SECONDS[tf] * 1000 * int(count * 1.7 + 50)
        from_ms = now_ms - min(span, 365 * 86_400_000)
        res = await self._read(account_id, md_proto.build_trendbars_req(sess.ctid, symbol_id, tf, from_ms, now_ms), timeout=30)
        bars = md_proto.parse_trendbars(res, tf)
        return bars[-count:]

    # ---- event dispatch ---------------------------------------------------------------------
    def _dispatch(self, env: str, msg: Any) -> None:
        name = type(msg).__name__
        self.last_event_at = utcnow()
        if name == "ProtoOASpotEvent":
            tick = md_proto.parse_spot(msg, int(time.time() * 1000))
            aid = self._by_ctid.get((env, tick.ctid))
            if aid:
                for h in self.tick_handlers:
                    try:
                        h(aid, tick)
                    except Exception as exc:
                        log.error("tick handler error: %s", type(exc).__name__)
            return
        ctid = int(getattr(msg, "ctidTraderAccountId", 0) or 0)
        aid = self._by_ctid.get((env, ctid))
        if aid and aid in self.sessions:
            self.sessions[aid].last_event_at = utcnow()
        if name == "ProtoOAExecutionEvent" and aid:
            evt = ord_proto.parse_execution_event(msg)
            evt.received_at = utcnow()
            for h in self.execution_handlers:
                self._spawn(self._safe_exec(h, aid, evt))
        elif name == "ProtoOAAccountDisconnectEvent" and aid:
            self.sessions[aid].authed = False
            self._spawn(self._emit_state(aid, "DISCONNECTED", "broker disconnected account"))
            self._spawn(self._reauth_later(aid))
        elif name == "ProtoOAAccountsTokenInvalidatedEvent":
            for c in getattr(msg, "ctidTraderAccountIds", []):
                a = self._by_ctid.get((env, int(c)))
                if a:
                    self.sessions[a].authed = False
                    self._spawn(self._emit_state(a, "TOKEN_INVALIDATED", getattr(msg, "reason", "") or ""))
        elif name == "ProtoOATraderUpdatedEvent" and aid:
            self._spawn(self._emit_state(aid, "TRADER_UPDATED", ""))
        elif name == "ProtoOAMarginCallTriggerEvent" and aid:
            self._spawn(self._emit_state(aid, "MARGIN_CALL", ""))
        elif name == "ProtoOAOrderErrorEvent":
            log.warning("unsolicited order error: %s %s", getattr(msg, "errorCode", ""), getattr(msg, "description", ""))
        elif name in ("ProtoOAClientDisconnectEvent",):
            log.warning("server requested client disconnect: %s", getattr(msg, "reason", ""))

    async def _reauth_later(self, account_id: str) -> None:
        await asyncio.sleep(5)
        sess = self.sessions.get(account_id)
        if sess and sess.desired and not sess.authed:
            try:
                await self._auth_session(sess, raise_errors=False)
            except Exception:
                pass

    async def _safe_exec(self, h: ExecHandler, aid: str, evt: ExecutionEventData) -> None:
        try:
            await h(aid, evt)
        except Exception as exc:
            log.error("execution handler error: %s", type(exc).__name__, exc_info=True)

    def info(self) -> dict[str, Any]:
        return {"managers": {e: m.info() for e, m in self.managers.items()},
                "sessions": {a: {"env": s.environment, "authed": s.authed, "subs": len(s.subscribed)} for a, s in self.sessions.items()},
                "last_event_at": self.last_event_at}
