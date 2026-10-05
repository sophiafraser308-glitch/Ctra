"""Sandbox child process. Self-contained: it must NOT import the `app` package (no secrets, no DB, no network).

Protocol (JSON lines):  parent -> child  {"id": n, "method": "...", "params": {...}}
                        child  -> parent {"id": n, "ok": true, "result": ...} | {"id": n, "ok": false, "error": "..."}
The first message is method "init" carrying the strategy source (no file access needed at all).

Layers of defence (see docs/strategy_security.md):
  1. AST validation (parent, before storage)       2. restricted builtins + guarded __import__
  3. audit-hook denies files/sockets/subprocess/ctypes/etc.   4. rlimits (memory, CPU, file size)
  5. empty environment, empty cwd, parent-side timeouts & kill   6. optional Docker (--network none, read-only)
"""
import builtins
import importlib
import json
import os
import sys
import types

PROTO_OUT = sys.stdout
PROTO_IN = sys.stdin
sys.stdout = sys.stderr           # strategy print() must never corrupt the protocol channel

DENY_PREFIXES = ("socket.", "subprocess.", "os.exec", "os.fork", "os.posix_spawn", "os.spawn", "os.system", "os.remove",
                 "os.rename", "os.mkdir", "os.rmdir", "os.truncate", "os.chmod", "os.chown", "os.symlink", "os.link",
                 "os.unlink", "os.putenv", "os.kill", "os.chdir", "os.listdir", "os.scandir", "shutil.", "ctypes.",
                 "pty.", "urllib.", "http.", "ftplib.", "smtplib.", "sqlite3.", "mmap.", "webbrowser.", "signal.",
                 "sys.settrace", "sys.setprofile", "sys.addaudithook", "pickle.find_class", "marshal.", "resource.",
                 "tempfile.", "glob.", "fcntl.", "msvcrt.", "winreg.", "multiprocessing.", "threading.Thread",
                 "_thread.start_new_thread", "open", "os.open", "os.walk", "builtins.")

REMOVED_BUILTINS = ("open", "eval", "exec", "compile", "input", "breakpoint", "exit", "quit", "help", "globals", "locals",
                    "vars", "memoryview", "getattr", "setattr", "delattr", "__loader__", "__spec__", "copyright",
                    "credits", "license", "dir", "type")


def _send(obj):
    PROTO_OUT.write(json.dumps(obj, separators=(",", ":"), default=str) + "\n")
    PROTO_OUT.flush()


def _apply_limits(mem_mb, cpu_s):
    try:
        import resource
        if mem_mb:
            resource.setrlimit(resource.RLIMIT_AS, (mem_mb * 1024 * 1024, mem_mb * 1024 * 1024))
        if cpu_s:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s))
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except Exception:
        pass


def _install_audit():
    def hook(event, args):
        for p in DENY_PREFIXES:
            if event == p or event.startswith(p):
                raise PermissionError("sandbox: operation '%s' is not permitted" % event)
    sys.addaudithook(hook)


def _make_import(allowed):
    real_import = builtins.__import__

    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        if level:
            raise ImportError("relative imports are not allowed")
        root = name.split(".")[0]
        if root not in allowed:
            raise ImportError("import of '%s' is not allowed in the strategy sandbox" % name)
        if name not in sys.modules:
            raise ImportError("module '%s' is not available in the sandbox" % name)
        return real_import(name, globals, locals, fromlist, level)
    return guarded


def _safe_print(*args, **kwargs):
    kwargs.pop("file", None)
    print_real(*args, file=sys.stderr, **kwargs)


print_real = builtins.print


def _normalize(result):
    if result is None:
        return []
    if not isinstance(result, (list, tuple)):
        raise TypeError("strategy hooks must return a list of signal dicts or None")
    out = []
    for s in list(result)[:20]:
        if not isinstance(s, dict):
            raise TypeError("each signal must be a dict")
        out.append({k: s[k] for k in ("symbol", "side", "order_type", "price", "stop_loss_pips", "take_profit_pips", "confidence", "comment", "volume_lots", "close_side") if k in s})
    return out


def main():
    first = PROTO_IN.readline()
    if not first:
        return
    msg = json.loads(first)
    if msg.get("method") != "init":
        _send({"id": msg.get("id"), "ok": False, "error": "first message must be init"})
        return
    p = msg["params"]
    allowed = set(p["allowed_imports"]) | {"strategy_sdk", "__future__"}
    _apply_limits(p.get("memory_mb"), p.get("cpu_seconds"))
    # preload allowed modules BEFORE locking down file access
    for name in sorted(allowed):
        if name in ("strategy_sdk",):
            continue
        try:
            importlib.import_module(name)
        except Exception:
            pass
    for extra in ("collections.abc", "typing", "dataclasses", "enum", "_strptime", "encodings.utf_8", "encodings.ascii"):
        try:
            importlib.import_module(extra)
        except Exception:
            pass
    sdk = types.ModuleType("strategy_sdk")
    sdk.__dict__["__builtins__"] = builtins.__dict__
    exec(compile(p["sdk_source"], "strategy_sdk", "exec"), sdk.__dict__)
    sys.modules["strategy_sdk"] = sdk
    try:
        code = compile(p["source"], "<strategy>", "exec")
    except Exception as exc:
        _send({"id": msg["id"], "ok": False, "error": "compile error: %s" % exc})
        return
    safe = {k: v for k, v in builtins.__dict__.items() if k not in REMOVED_BUILTINS}
    safe["__import__"] = _make_import(allowed)
    safe["print"] = _safe_print
    ns = {"__builtins__": safe, "__name__": "strategy"}
    _install_audit()
    try:
        exec(code, ns)
        cls = ns[p["class_name"]]
        inst = cls(p.get("params", {}), p.get("context", {}))
    except BaseException as exc:
        _send({"id": msg["id"], "ok": False, "error": "init failed: %s: %s" % (type(exc).__name__, exc)})
        return
    _send({"id": msg["id"], "ok": True, "result": "ready"})

    allowed_methods = {"on_start", "on_tick", "on_bar", "on_order_update", "on_position_update", "on_stop"}
    hist = {}                                  # backtest only: (symbol, timeframe) -> closed bars kept inside the sandbox
    for line in PROTO_IN:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            method, params = req["method"], req.get("params", {})
            if method == "ping":
                _send({"id": req["id"], "ok": True, "result": "pong"})
                continue
            if method == "bt_chunk":
                # backtest: items are [symbol, timeframe, bar, warmup_flag] in chronological order; history stays in this process
                out, base = [], params.get("base", 0)
                for k, it in enumerate(params["items"]):
                    sym, tf, bar, warm = it
                    h = hist.setdefault((sym, tf), [])
                    if not warm:
                        sg = _normalize(inst.on_bar(sym, tf, bar, h[-190:]))
                        if sg:
                            out.append({"i": base + k, "s": sg})
                    h.append(bar)
                    if len(h) > 600:
                        del h[:200]
                _send({"id": req["id"], "ok": True, "result": out})
                continue
            if method not in allowed_methods:
                raise ValueError("unknown method")
            res = getattr(inst, method)(**params)
            _send({"id": req["id"], "ok": True, "result": _normalize(res) if method in ("on_start", "on_tick", "on_bar") else None})
            if method == "on_stop":
                return
        except BaseException as exc:
            try:
                _send({"id": req.get("id") if isinstance(req, dict) else None, "ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:300])})
            except Exception:
                return


if __name__ == "__main__":
    main()
