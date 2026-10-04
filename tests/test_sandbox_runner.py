"""Runs the real sandbox child process (stdlib only) and checks that dangerous operations are blocked."""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(__file__))
RUNNER = os.path.join(ROOT, "app", "strategies", "runner.py")
SDK = open(os.path.join(ROOT, "app", "strategies", "sdk.py"), encoding="utf-8").read()

SRC = '''
from strategy_sdk import BaseStrategy
import math
STRATEGY_INFO = {"name": "T"}
class S(BaseStrategy):
    def on_tick(self, symbol, bid, ask, ts_ms):
        return [self.buy(symbol, 10, 20)]
    def on_bar(self, symbol, timeframe, bar, history):
        return open("/etc/passwd").read()
    def on_order_update(self, update):
        import os
    def on_position_update(self, update):
        import socket
'''


def _session():
    p = subprocess.Popen([sys.executable, "-I", RUNNER], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd="/tmp")

    def rq(i, method, params):
        p.stdin.write(json.dumps({"id": i, "method": method, "params": params}) + "\n")
        p.stdin.flush()
        return json.loads(p.stdout.readline())
    return p, rq


def test_sandbox_blocks_and_allows():
    p, rq = _session()
    try:
        init = {"source": SRC, "sdk_source": SDK, "class_name": "S", "allowed_imports": ["math", "datetime"], "params": {}, "context": {}, "memory_mb": 512, "cpu_seconds": 30}
        assert rq(1, "init", init)["ok"] is True
        r = rq(2, "on_tick", {"symbol": "EURUSD", "bid": 1.1, "ask": 1.1001, "ts_ms": 1})
        assert r["ok"] and r["result"][0]["side"] == "BUY"
        assert rq(3, "on_bar", {"symbol": "EURUSD", "timeframe": "M5", "bar": {}, "history": []})["ok"] is False
        assert "not allowed" in rq(4, "on_order_update", {"update": {}})["error"]
        assert "not allowed" in rq(5, "on_position_update", {"update": {}})["error"]
    finally:
        p.kill()
