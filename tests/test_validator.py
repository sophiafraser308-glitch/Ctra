import pytest

pytest.importorskip("pydantic")

from app.strategies.validator import validate_source

ALLOWED = {"math", "statistics", "collections"}
GOOD = '''
from strategy_sdk import BaseStrategy
import math
STRATEGY_INFO = {"name": "Good One", "version": "1.2.3", "parameters": {"n": 5}, "symbols": ["EURUSD"], "timeframes": ["M5"]}
class Good(BaseStrategy):
    def on_tick(self, symbol, bid, ask, ts_ms):
        return None
'''


def v(src, **kw):
    return validate_source(src, allowed_imports=ALLOWED, allowed_dependencies=set(), max_bytes=kw.get("max_bytes", 100000), max_dependencies=3)


def test_good_strategy_passes():
    r = v(GOOD)
    assert r.ok, r.errors
    assert r.metadata["name"] == "Good One" and r.class_name == "Good" and len(r.sha256) == 64


@pytest.mark.parametrize("bad", ["import os", "import subprocess", "from socket import socket", "import importlib", "import ctypes", "from os import path"])
def test_bad_imports(bad):
    assert not v(GOOD + "\n" + bad).ok


@pytest.mark.parametrize("bad", ["eval('1')", "exec('x=1')", "open('f')", "__import__('os')", "compile('1','a','exec')", "getattr(object, 'x')"])
def test_banned_calls(bad):
    assert not v(GOOD + "\n" + bad).ok


def test_dunder_escape_blocked():
    assert not v(GOOD + "\nx = ().__class__.__bases__[0].__subclasses__()").ok


def test_requires_info_and_single_class():
    assert not v("from strategy_sdk import BaseStrategy\nclass A(BaseStrategy): pass").ok
    two = GOOD + "\nclass Other(BaseStrategy):\n    pass\n"
    assert not v(two).ok


def test_size_and_syntax():
    assert not v(GOOD, max_bytes=10).ok
    assert not v("def (:").ok


def test_dependencies_must_be_allowlisted():
    src = GOOD.replace('"timeframes": ["M5"]}', '"timeframes": ["M5"], "dependencies": ["numpy"]}')
    r = v(src)
    assert not r.ok and any("dependencies" in e for e in r.errors)


def test_global_statement_blocked():
    assert not v(GOOD + "\ndef f():\n    global X\n    X = 1\n").ok
