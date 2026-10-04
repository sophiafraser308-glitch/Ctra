"""aiohttp server: /healthz and the cTrader OAuth redirect endpoint."""
from __future__ import annotations

import html
from typing import Any

from aiohttp import web

from app import __version__
from app.core.enums import Category
from app.logging import get_logger
from app.security.rate_limit import RateLimiter

log = get_logger(Category.AUTH)
_limiter = RateLimiter(20, 60)

PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>cTrader authorization</title><style>body{{font-family:sans-serif;max-width:480px;margin:15vh auto;padding:0 16px;text-align:center}}
.ok{{color:#0a7d33}}.err{{color:#b00020}}</style></head><body><h2 class="{cls}">{title}</h2><p>{msg}</p></body></html>"""


def build_app(ctx: Any) -> web.Application:
    app = web.Application(client_max_size=64 * 1024)

    async def health(request: web.Request) -> web.Response:
        db_ok = await ctx.db.ping()
        return web.json_response({"status": "ok" if db_ok else "degraded", "db": db_ok, "version": __version__}, status=200 if db_ok else 503)

    async def callback(request: web.Request) -> web.Response:
        ip = request.headers.get("X-Forwarded-For", request.remote or "?").split(",")[0].strip()
        if not _limiter.allow(ip):
            return web.Response(status=429, text="Too many requests")
        q = request.query
        ok, msg = await ctx.accounts.handle_callback(q.get("state"), q.get("code"), q.get("error") or q.get("error_description"))
        log.info("oauth callback handled ok=%s", ok)
        body = PAGE.format(cls="ok" if ok else "err", title="Authorization successful" if ok else "Authorization failed", msg=html.escape(msg))
        return web.Response(text=body, content_type="text/html", status=200 if ok else 400,
                            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"})

    app.router.add_get("/healthz", health)
    app.router.add_get("/", health)
    app.router.add_get(ctx.settings.redirect_path, callback)
    return app


class WebServer:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.runner: web.AppRunner | None = None

    async def start(self) -> None:
        self.runner = web.AppRunner(build_app(self.ctx), access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, self.ctx.settings.web_host, self.ctx.settings.web_port)
        await site.start()
        log.info("web server listening on %s:%s (callback path %s)", self.ctx.settings.web_host, self.ctx.settings.web_port, self.ctx.settings.redirect_path)

    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()
