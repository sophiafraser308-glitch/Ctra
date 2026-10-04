from app.ctrader.types import SymbolInfo


def make():
    return SymbolInfo(symbol_id=1, name="EURUSD", digits=5, pip_position=4, lot_size=10_000_000, min_volume=100_000, step_volume=100_000, max_volume=10_000_000_000)


def test_pip_size_and_lots_conversion():
    s = make()
    assert abs(s.pip_size - 0.0001) < 1e-12
    assert s.lots_to_protocol(1.0) == 10_000_000
    assert s.lots_to_protocol(0.37) == 3_700_000
    assert s.protocol_to_lots(5_000_000) == 0.5
    assert s.min_lots == 0.01 and s.step_lots == 0.01


def test_step_rounding_down():
    s = make()
    assert s.lots_to_protocol(0.019) == 100_000


def test_jpy_pip():
    s = SymbolInfo(symbol_id=2, name="USDJPY", digits=3, pip_position=2)
    assert abs(s.pip_size - 0.01) < 1e-12
