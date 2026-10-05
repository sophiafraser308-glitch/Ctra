import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from datetime import datetime, timezone  # noqa: E402

from app.core.timeframes import TF_ORDER, bucket_start_ms  # noqa: E402
from app.market_data.catalog import build_catalog, classify  # noqa: E402


def test_catalog_only_gold_oil_forex_majors():
    names = ["EURUSD", "EURUSD.r", "XAUUSD", "GOLD", "XTIUSD", "USOIL", "XBRUSD", "GBPJPY", "EURUSDT", "BTCUSD", "XAGUSD", "USDJPY"]
    cat = {c.name: c.category for c in build_catalog(names)}
    assert cat["XAUUSD"] == "GOLD" and cat["GOLD"] == "GOLD" and cat["XTIUSD"] == "OIL" and cat["EURUSD.r"] == "FOREX"
    assert "GBPJPY" not in cat and "EURUSDT" not in cat and "BTCUSD" not in cat and "XAGUSD" not in cat
    assert classify("USDJPY").base == "USDJPY"


def test_timeframes_complete_and_buckets():
    assert TF_ORDER[0] == "M1" and TF_ORDER[-1] == "MN1" and len(TF_ORDER) == 14
    ms = int(datetime(2026, 10, 7, 13, 41, tzinfo=timezone.utc).timestamp() * 1000)     # a Wednesday
    f = lambda tf: datetime.fromtimestamp(bucket_start_ms(tf, ms) / 1000, timezone.utc)
    assert f("M15").minute == 30 and f("H4").hour == 12 and f("D1").hour == 0
    assert f("W1").weekday() == 0 and f("W1").day == 5 and f("MN1").day == 1
