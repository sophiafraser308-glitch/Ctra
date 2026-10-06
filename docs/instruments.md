# Points, decimals and point value

One definition table (`app/market_data/instruments.py`) is used by live orders (SL/TP distance), the Risk Engine (pip value), spreads and backtests, so "20 points" means the same thing everywhere.

| Instrument | 1 point = | Decimals shown | Value of 1 point per 1.0 lot |
|---|---|---|---|
| **Gold** (XAUUSD / GOLD) | **0.1** | **0** | $10 (100 oz × 0.1) — 4150 → 4152 = **20 points** = $200 per lot |
| Oil (XTIUSD, USOIL, XBRUSD, UKOIL) | 0.01 | 2 | $10 (1000 bbl × 0.01) |
| EURUSD, GBPUSD, AUDUSD, NZDUSD | 0.0001 (1 pip) | 5 | $10 |
| USDCHF | 0.0001 | 5 | 10 CHF ≈ 10 ÷ USDCHF USD |
| USDCAD | 0.0001 | 5 | 10 CAD ≈ 10 ÷ USDCAD USD |
| USDJPY | 0.01 | 3 | 1000 JPY ≈ 1000 ÷ USDJPY USD |

* Decimals shown affect display only (Telegram, Excel number format). Orders still use the broker's price precision, and Excel cells keep the exact value.
* **Stop loss, take profit, spread** (Trade settings `/set sl|tp`, strategies' `sl_pips`/`tp_pips`, backtest options, Risk Profile *max spread*) are all in these points. Example: gold a stop of `30` points is **$3.00**, not $0.30.
* Telegram: `/points` or Settings → 📐 Points & decimals (also inside Backtest) shows the exact values from your connected account (broker pip is shown for comparison); *Reference table* works without an account.
* Instruments outside Gold / Oil / forex majors keep the broker's pip definition.
