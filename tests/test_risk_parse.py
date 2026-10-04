import pytest

from app.core.exceptions import ValidationFailed
from app.schemas.risk import parse_risk_value


def test_numeric_ranges():
    assert parse_risk_value("max_open_positions", "5") == 5
    assert parse_risk_value("risk_per_trade_pct", "0.5") == 0.5
    with pytest.raises(ValidationFailed):
        parse_risk_value("risk_per_trade_pct", "50")
    with pytest.raises(ValidationFailed):
        parse_risk_value("max_open_positions", "abc")


def test_lists():
    assert parse_risk_value("allowed_symbols", "eurusd, gbpusd") == ["EURUSD", "GBPUSD"]
    assert parse_risk_value("allowed_symbols", "all") == []
    assert parse_risk_value("allowed_sessions", "07:00-16:00") == ["07:00-16:00"]
    with pytest.raises(ValidationFailed):
        parse_risk_value("allowed_sessions", "7-16")
    assert parse_risk_value("lock_policy", "next_day") == "NEXT_DAY"
