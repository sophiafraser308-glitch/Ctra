import csv
import io
import os
import sys

import pytest

pytest.importorskip("openpyxl")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from app.backtest.engine import BacktestEngine, BtConfig, SymbolSpec  # noqa: E402
from app.backtest.report import TRADE_HEADERS, build_csv, build_summary_text, build_xlsx  # noqa: E402

TF = 900_000


def make_result():
    cfg = BtConfig(tf_ms=TF, tz_offset_hours=3, daily_loss_limit=0)
    e = BacktestEngine(cfg, {"EURUSD": SymbolSpec("EURUSD", 0.0001, 100_000, 5, spread_pips=1.0)})
    sig = {"side": "BUY", "order_type": "MARKET", "stop_loss_pips": 10, "take_profit_pips": 20, "volume_lots": 1.0}
    for k in range(10):
        e.feed(2 * k * TF, "EURUSD", {"t": 2 * k * TF, "o": 1.1, "h": 1.1, "l": 1.1, "c": 1.1, "v": 1}, [sig])
        win = k % 2 == 0
        e.feed((2 * k + 1) * TF, "EURUSD", {"t": (2 * k + 1) * TF, "o": 1.1, "h": 1.1030 if win else 1.1, "l": 1.0999 if win else 1.098, "c": 1.1, "v": 1}, [])
    return e.finish()


META = {"strategy": "Stochastic Cross", "version": "1.0.0", "account": "Demo", "symbols": ["EURUSD"], "timeframe": "M15", "date_from": "2026-01-01",
        "date_to": "2026-01-31", "tz_label": "UTC+3", "bars": 20, "seconds": 1.2, "params": {"k_period": 49}, "spreads": {"EURUSD": 1.0}}


def test_xlsx_sheets_and_trade_rows():
    from openpyxl import load_workbook
    r = make_result()
    wb = load_workbook(io.BytesIO(build_xlsx(r, META)))
    assert wb.sheetnames == ["الملخص", "الصفقات", "حسب الرمز", "يومي", "شهري", "أيام الإيقاف", "إشارات متجاهلة", "منحنى الرصيد", "الإعدادات"]
    ws = wb["الصفقات"]
    assert [c.value for c in ws[1]] == TRADE_HEADERS
    assert ws.max_row == 1 + len(r.trades) and ws["M2"].value in ("WIN ✅", "LOSS ❌")
    assert abs(ws.cell(ws.max_row, 18).value - r.stats["final_balance"]) < 0.01


def test_csv_and_summary():
    r = make_result()
    rows = list(csv.reader(io.StringIO(build_csv(r, META).decode("utf-8-sig"))))
    assert rows[0] == TRADE_HEADERS and len(rows) == 1 + len(r.trades)
    text = build_summary_text(r, META)
    assert "Backtest" in text and "Stochastic Cross" in text and len(text) < 4000
