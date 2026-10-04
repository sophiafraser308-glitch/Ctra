import pytest

pytest.importorskip("sqlalchemy")

from datetime import datetime, timezone

from app.risk.engine import compute_lots, session_allowed


def dt(h, m=0):
    return datetime(2026, 1, 5, h, m, tzinfo=timezone.utc)


def test_session_windows():
    assert session_allowed([], dt(3))
    assert session_allowed(["07:00-16:00"], dt(8))
    assert not session_allowed(["07:00-16:00"], dt(16))
    assert session_allowed(["22:00-06:00"], dt(23)) and session_allowed(["22:00-06:00"], dt(5))
    assert not session_allowed(["22:00-06:00"], dt(12))


def test_compute_lots_basic():
    # balance 10000, risk 1% = 100; SL 20 pips, pip value/lot 10 -> 0.5 lots
    assert compute_lots(balance=10000, risk_pct=1, sl_pips=20, pip_value_per_lot=10, min_lots=0.01, step_lots=0.01, max_lots=100) == pytest.approx(0.5)


def test_compute_lots_clamps_and_rounds_down():
    assert compute_lots(balance=10000, risk_pct=1, sl_pips=7, pip_value_per_lot=10, min_lots=0.01, step_lots=0.01, max_lots=100) == pytest.approx(1.42)
    assert compute_lots(balance=10000, risk_pct=1, sl_pips=1, pip_value_per_lot=10, min_lots=0.01, step_lots=0.01, max_lots=2.0) == 2.0


def test_compute_lots_below_min_and_invalid():
    assert compute_lots(balance=100, risk_pct=0.1, sl_pips=50, pip_value_per_lot=10, min_lots=0.01, step_lots=0.01, max_lots=10) == 0.0
    assert compute_lots(balance=0, risk_pct=1, sl_pips=10, pip_value_per_lot=10, min_lots=0.01, step_lots=0.01, max_lots=10) == 0.0
