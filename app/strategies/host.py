"""Parent-side handle for one sandboxed strategy process (process mode or Docker mode)."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from collections import deque
from pathlib import Path
from typing import Any

from app.config import Settings
from app.core.enums import Category
from app.core.exceptions import PlatformError, StrategyCrashed, StrategyTimeout
from app.logging import get_logger

log = get_logger(Category.STRATEGY)
RUNNER = Path(__file__).with_name("runner.py")
SDK_FILE = Path(__file__).with_name("sdk.py")


class StrategyHost:
    def __init__(self, settings: Settings, label: str, source: str, class_name: str, params: dict[str, Any], context: dict[str, Any]) -> None:
        self.s, self.label = settings, label
        self.source, self.class_name, self.params, self.context = source, class_name, params, context
        self.proc: asyncio.subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._seq = 0
        self._reader: asyncio.Task | None = None
        self._err_reader: asyncio.Task | None = None
        self._stderr_tail: deque[str] = deque(maxlen=30)
        self._tmp: str | None = None
        self._write_lock = asyncio.Lock()
        self.dead_reason: str | None = None
        self.calls = 0
        self.last_error: str | None = None

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None and self.dead_reason is None

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc else None

    def stderr_tail(self) -> str:
        return "\n".join(self._stderr_tail)

    # ---- process creation -----------------------------------------------------
    def _command(self) -> tuple[list[str], dict[str, str], str]:
        self._tmp = tempfile.mkdtemp(prefix="strat_")
        os.chmod(self._tmp, 0o700)
        if self.s.strategy_sandbox_mode == "docker":
            if not shutil.which("docker"):
                raise PlatformError("STRATEGY_SANDBOX_MODE=docker but the docker CLI is not available")
            cmd = ["docker", "run", "--rm", "-i", "--network", "none", "--read-only", "--cap-drop", "ALL",
                   "--security-opt", "no-new-privileges", "--pids-limit", "32", "--memory", f"{self.s.strategy_memory_mb + 64}m",
                   "--cpus", "1", "--user", "65534:65534", "--tmpfs", "/tmp:size=1m,noexec", self.s.strategy_docker_image,
                   "python", "-I", "/app/app/strategies/runner.py"]
            return cmd, {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}, self._tmp
        env = {"PATH": "/usr/bin:/bin", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1", "LANG": "C.UTF-8"}
        return [sys.executable, "-I", "-s", str(RUNNER)], env, self._tmp

    async def start(self) -> None:
        cmd, env, cwd = self._command()
        self.proc = await asyncio.create_subprocess_exec(
            *cmd, env=env, cwd=cwd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True, limit=8 * 1024 * 1024)
        self._reader = asyncio.create_task(self._read_stdout(), name=f"strat-out-{self.label}")
        self._err_reader = asyncio.create_task(self._read_stderr(), name=f"strat-err-{self.label}")
        init = {"source": self.source, "sdk_source": SDK_FILE.read_text(encoding="utf-8"), "class_name": self.class_name,
                "allowed_imports": sorted(self.s.strategy_allowed_imports), "params": self.params, "context": self.context,
                "memory_mb": self.s.strategy_memory_mb, "cpu_seconds": self.s.strategy_cpu_seconds}
        await self._request("init", init, timeout=20)
        log.info("strategy process started %s pid=%s mode=%s", self.label, self.pid, self.s.strategy_sandbox_mode)

    # ---- IO ----------------------------------------------------------------------
    async def _read_stdout(self) -> None:
        assert self.proc and self.proc.stdout
        try:
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                fut = self._pending.pop(msg.get("id"), None)
                if fut and not fut.done():
                    fut.set_result(msg)
        except (asyncio.CancelledError, Exception):
            pass
        finally:
            rc = self.proc.returncode if self.proc else None
            if rc is None and self.proc:
                try:
                    rc = await asyncio.wait_for(self.proc.wait(), 2)
                except Exception:
                    rc = None
            self._mark_dead(f"process exited (code {rc})")

    async def _read_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        try:
            while True:
                line = await self.proc.stderr.readline()
                if not line:
                    break
                self._stderr_tail.append(line.decode("utf-8", "replace").rstrip()[:300])
        except (asyncio.CancelledError, Exception):
            pass

    def _mark_dead(self, reason: str) -> None:
        if self.dead_reason is None:
            self.dead_reason = reason
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(StrategyCrashed(self.dead_reason or reason))
        self._pending.clear()

    async def _request(self, method: str, params: dict[str, Any], timeout: float) -> Any:
        if not self.alive and method != "init":
            raise StrategyCrashed(self.dead_reason or "strategy process not running")
        assert self.proc and self.proc.stdin
        self._seq += 1
        rid = self._seq
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        data = (json.dumps({"id": rid, "method": method, "params": params}, separators=(",", ":"), default=str) + "\n").encode()
        try:
            async with self._write_lock:
                self.proc.stdin.write(data)
                await self.proc.stdin.drain()
            msg = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            self.last_error = f"{method} timed out after {timeout}s"
            await self.kill(f"timeout in {method}")
            raise StrategyTimeout(self.last_error)
        except (BrokenPipeError, ConnectionResetError) as exc:
            self._mark_dead("broken pipe")
            raise StrategyCrashed("strategy process pipe broken") from exc
        if not msg.get("ok"):
            self.last_error = str(msg.get("error"))
            raise PlatformError(f"strategy error: {msg.get('error')}")
        return msg.get("result")

    async def call(self, method: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> list[dict]:
        self.calls += 1
        res = await self._request(method, params or {}, timeout or self.s.strategy_call_timeout)
        return res or []

    # ---- shutdown --------------------------------------------------------------------
    async def kill(self, why: str = "killed") -> None:
        self._mark_dead(why)
        if self.proc and self.proc.returncode is None:
            try:
                os.killpg(self.proc.pid, 9)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        await self._cleanup()

    async def stop(self) -> None:
        if self.alive:
            try:
                await self._request("on_stop", {}, timeout=3)
            except Exception:
                pass
        await self.kill("stopped")

    async def _cleanup(self) -> None:
        for t in (self._reader, self._err_reader):
            if t and not t.done():
                t.cancel()
        if self.proc:
            try:
                await asyncio.wait_for(self.proc.wait(), 3)
            except Exception:
                pass
        if self._tmp:
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None
