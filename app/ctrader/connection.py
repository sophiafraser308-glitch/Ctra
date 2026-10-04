"""CTraderConnectionManager — one managed TCP/SSL protobuf connection per environment (DEMO / LIVE host).

Built on the official Spotware SDK (Twisted `Client`, run on the asyncio loop via asyncioreactor).
Responsibilities: connect, application auth, reconnect with backoff, heartbeat, request/response
correlation (done by the SDK through clientMsgId), timeouts, error mapping, graceful shutdown.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

from app.config import Settings
from app.core.enums import Category, StrEnum
from app.core.exceptions import CTraderError, CTraderNotConnected, CTraderTimeout
from app.ctrader.pb import sdk
from app.logging import get_logger

log = get_logger(Category.CONNECTION)


class ConnState(StrEnum):
    STOPPED = "STOPPED"
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"       # transport up, app auth pending
    READY = "READY"               # application authenticated
    STOPPING = "STOPPING"


ERROR_TYPES = {"ProtoOAErrorRes", "ProtoErrorRes", "ProtoOAOrderErrorEvent"}


def raise_if_error(msg: Any) -> None:
    name = type(msg).__name__
    if name in ERROR_TYPES:
        code = getattr(msg, "errorCode", "") or ""
        desc = getattr(msg, "description", "") or ""
        raise CTraderError(f"{code}: {desc}".strip(": ")[:300], broker_code=str(code))


class CTraderConnectionManager:
    HEARTBEAT_SECONDS = 10.0

    def __init__(self, settings: Settings, environment: str) -> None:
        self.s = settings
        self.environment = environment.upper()
        self.state = ConnState.STOPPED
        self.host = settings.ctrader_live_host if self.environment == "LIVE" else settings.ctrader_demo_host
        self.port = settings.ctrader_port
        self._client: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._handshake_task: asyncio.Task | None = None
        self._state_since = time.monotonic()
        self._attempt = 0
        self.last_error: str | None = None
        self.last_message_at: float | None = None
        self.reconnects = 0
        # callbacks wired by the gateway
        self.on_message: Callable[[Any], None] | None = None
        self.on_ready: Callable[[], Awaitable[None]] | None = None
        self.on_down: Callable[[str], Awaitable[None]] | None = None

    # ---- lifecycle -----------------------------------------------------
    async def start(self) -> None:
        if self.state != ConnState.STOPPED:      # DISCONNECTED = SDK is already auto-reconnecting
            return
        s = sdk()
        self._loop = asyncio.get_running_loop()
        self._ready.clear()
        self._set_state(ConnState.CONNECTING)
        self._client = s.Client(self.host, self.port, s.TcpProtocol)  # TCP + SSL (SDK default for port 5035)
        self._client.setConnectedCallback(self._cb_connected)
        self._client.setDisconnectedCallback(self._cb_disconnected)
        self._client.setMessageReceivedCallback(self._cb_message)
        self._client.startService()
        self._tasks = [asyncio.create_task(self._heartbeat_loop(), name=f"ct-hb-{self.environment}"),
                       asyncio.create_task(self._supervise(), name=f"ct-sup-{self.environment}")]
        log.info("cTrader %s connecting to %s:%s", self.environment, self.host, self.port)

    async def stop(self) -> None:
        if self.state == ConnState.STOPPED:
            return
        self._set_state(ConnState.STOPPING)
        for t in self._tasks + ([self._handshake_task] if self._handshake_task else []):
            t.cancel()
        self._tasks.clear()
        try:
            if self._client is not None:
                self._client.stopService()
        except Exception as exc:
            log.warning("stopService error: %s", type(exc).__name__)
        self._client = None
        self._ready.clear()
        self._set_state(ConnState.STOPPED)

    async def restart(self) -> None:
        await self.stop()
        await asyncio.sleep(0.5)
        await self.start()

    @property
    def is_ready(self) -> bool:
        return self.state == ConnState.READY

    def _set_state(self, st: ConnState) -> None:
        if st != getattr(self, "state", None):
            log.info("cTrader %s state %s -> %s", self.environment, getattr(self, "state", None), st)
        self.state = st
        self._state_since = time.monotonic()

    # ---- SDK callbacks (called by Twisted on the asyncio loop thread) ---
    def _cb_connected(self, client: Any) -> None:
        self._set_state(ConnState.CONNECTED)
        self._ready.clear()
        if self._handshake_task and not self._handshake_task.done():
            self._handshake_task.cancel()
        assert self._loop is not None
        self._handshake_task = self._loop.create_task(self._handshake())

    def _cb_disconnected(self, client: Any, reason: Any) -> None:
        if self.state in (ConnState.STOPPING, ConnState.STOPPED):
            return
        was_ready = self.state == ConnState.READY
        self._ready.clear()
        self._set_state(ConnState.DISCONNECTED)
        self.last_error = f"disconnected: {str(reason)[:120]}"
        log.warning("cTrader %s transport lost (%s)", self.environment, str(reason)[:120])
        if self.on_down and self._loop:
            self._loop.create_task(self.on_down(self.last_error))
        if was_ready:
            self.reconnects += 1
        # The SDK's ClientService reconnects automatically; supervisor forces a restart if the handshake stalls.

    def _cb_message(self, client: Any, message: Any) -> None:
        self.last_message_at = time.monotonic()
        try:
            extracted = sdk().Protobuf.extract(message)
        except Exception as exc:
            log.warning("protobuf extract failed: %s", type(exc).__name__)
            return
        if type(extracted).__name__ == "ProtoHeartbeatEvent":
            return
        if self.on_message:
            try:
                self.on_message(extracted)
            except Exception as exc:
                log.error("message handler error: %s", type(exc).__name__, exc_info=True)

    # ---- handshake -----------------------------------------------------
    async def _handshake(self) -> None:
        s = sdk()
        try:
            req = s.msgs.ProtoOAApplicationAuthReq()
            req.clientId = self.s.ctrader_client_id
            req.clientSecret = self.s.ctrader_client_secret.get_secret_value()
            res = await self.request(req, timeout=self.s.ctrader_request_timeout, require_ready=False)
            raise_if_error(res)
            self._attempt = 0
            self._set_state(ConnState.READY)
            self._ready.set()
            log.info("cTrader %s application authenticated", self.environment)
            if self.on_ready:
                await self.on_ready()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = f"app auth failed: {str(exc)[:160]}"
            log.error("cTrader %s application auth failed: %s", self.environment, str(exc)[:160])
            if self.on_down:
                await self.on_down(self.last_error)

    # ---- requests ------------------------------------------------------
    async def request(self, req: Any, *, timeout: float | None = None, require_ready: bool = True) -> Any:
        if self._client is None or self.state in (ConnState.STOPPED, ConnState.STOPPING, ConnState.DISCONNECTED, ConnState.CONNECTING):
            raise CTraderNotConnected(f"{self.environment} connection is {self.state.value}")
        if require_ready and self.state != ConnState.READY:
            raise CTraderNotConnected(f"{self.environment} connection not authenticated yet")
        timeout = timeout or self.s.ctrader_request_timeout
        assert self._loop is not None
        try:
            d = self._client.send(req, responseTimeoutInSeconds=timeout)
            fut = d.asFuture(self._loop)
            msg = await asyncio.wait_for(fut, timeout + 2)
        except asyncio.TimeoutError as exc:
            raise CTraderTimeout(f"{type(req).__name__} timed out") from exc
        except CTraderError:
            raise
        except Exception as exc:  # twisted TimeoutError / ConnectionLost etc.
            name = type(exc).__name__
            if "Timeout" in name:
                raise CTraderTimeout(f"{type(req).__name__} timed out") from exc
            raise CTraderError(f"{type(req).__name__} failed: {name}: {str(exc)[:150]}") from exc
        extracted = sdk().Protobuf.extract(msg)
        raise_if_error(extracted)
        return extracted

    # ---- background loops ---------------------------------------------
    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self.HEARTBEAT_SECONDS)
            if self.state == ConnState.READY and self._client is not None:
                try:
                    d = self._client.send(sdk().common.ProtoHeartbeatEvent(), responseTimeoutInSeconds=60)
                    d.addErrback(lambda f: None)  # heartbeat has no response; swallow timeout errback
                except Exception as exc:
                    log.warning("heartbeat send failed: %s", type(exc).__name__)

    async def _supervise(self) -> None:
        """Force a clean reconnect (exponential backoff) if we are not READY for too long."""
        while True:
            await asyncio.sleep(5)
            if self.state in (ConnState.STOPPING, ConnState.STOPPED, ConnState.READY):
                continue
            stalled = time.monotonic() - self._state_since
            limit = min(30 * (2 ** self._attempt), 300)
            if stalled > limit:
                self._attempt = min(self._attempt + 1, 6)
                log.warning("cTrader %s not ready for %ds; forcing reconnect (attempt %d)", self.environment, int(stalled), self._attempt)
                try:
                    if self._client is not None:
                        self._client.stopService()
                        await asyncio.sleep(1)
                        self._set_state(ConnState.CONNECTING)
                        self._client.startService()
                except Exception as exc:
                    log.error("forced reconnect failed: %s", type(exc).__name__)

    def info(self) -> dict[str, Any]:
        return {"environment": self.environment, "state": self.state.value, "host": self.host,
                "reconnects": self.reconnects, "last_error": self.last_error,
                "since_s": int(time.monotonic() - self._state_since)}
