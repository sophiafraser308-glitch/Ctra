"""Run the Twisted reactor (used by the official SDK) on top of the asyncio event loop.

MUST be called inside the running loop *before* anything imports `twisted.internet.reactor`
(i.e. before `ctrader_open_api` is imported). `app.__main__` does this first thing.
"""
from __future__ import annotations

import asyncio


def install_asyncio_reactor(loop: asyncio.AbstractEventLoop | None = None) -> None:
    loop = loop or asyncio.get_running_loop()
    try:
        from twisted.internet import asyncioreactor
    except ImportError:  # twisted missing: cTrader features will raise a clear error later
        return
    try:
        asyncioreactor.install(eventloop=loop)
    except Exception as exc:  # ReactorAlreadyInstalledError
        if type(exc).__name__ != "ReactorAlreadyInstalledError":
            raise
