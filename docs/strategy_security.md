# Strategy security model

Uploaded code is untrusted. Defence in depth:

1. **Upload checks** – `.py` only, size limit, UTF-8, line limit, role `STRATEGY_MANAGE` (ADMIN).
2. **AST validation** (`app/strategies/validator.py`) – import allow-list (`STRATEGY_ALLOWED_IMPORTS`), banned calls (`eval exec compile open __import__ getattr setattr globals locals vars type input breakpoint …`), banned dunder/frame attributes (`__class__ __subclasses__ __globals__ f_back …`), no `global`, no star/relative imports, no class decorators/metaclasses, mandatory literal `STRATEGY_INFO`, exactly one `BaseStrategy` subclass, dependency allow-list and count limit, integrity hash.
3. **Isolated process** – each bot strategy runs in its own child process (`app/strategies/runner.py`), started with an **empty environment** (no tokens/DB URL), an empty temp working directory and `python -I`. Source is passed over stdin (no file access needed). It never imports the `app` package, so it has no handle on the DB, Telegram or cTrader.
4. **In-process restrictions** – restricted builtins (no `open`, `eval`, `exec`, `compile`, `getattr`…), guarded `__import__` that only returns pre-loaded allow-listed modules, `print` redirected to stderr, **audit hook** that denies file opens, sockets, subprocess/exec/fork, ctypes, signals, pickle/marshal, etc.
5. **Resource limits** – `RLIMIT_AS` (memory), `RLIMIT_CPU`, `RLIMIT_FSIZE=0`, per-call timeout (kill on expiry), crash detection, bounded stderr capture, crash-loop lock by the watchdog.
6. **Optional Docker mode** (`STRATEGY_SANDBOX_MODE=docker`) – `--network none --read-only --cap-drop ALL --pids-limit --memory --cpus --user nobody`.
7. **Output validation** – every signal is re-validated by a pydantic model in the parent and still has to pass the Risk Engine; a strategy cannot place orders, set volume, or reach the broker.

## Honest limits
Python language-level sandboxing is **not** a perfect security boundary; a determined attacker with upload rights may find escapes. Treat the process mode as protection against mistakes and casual abuse, restrict upload to trusted admins (the default), and use Docker mode (kernel-level isolation) when strategies come from less-trusted sources. `rlimit`/audit hooks are Linux/Termux oriented; Docker mode requires the docker CLI in the host (not available on Railway).
