# Backtest

Telegram → **🧪 Backtest** (main menu, also under Strategies) → **New backtest**.

## Wizard
1. **Strategy** (active version).
2. **Account** – a *connected* cTrader account used only to download history and symbol specs (a DEMO account is fine). Nothing is ever traded.
3. **Symbols (multi-select)** – the list is built from the symbols your broker really offers and limited to **Gold, Oil and the 7 major forex pairs** (names differ per broker: `XAUUSD`/`GOLD`, `XTIUSD`/`USOIL`, suffixes…). Quick buttons select a whole group. If the account is offline a generic, *unverified* list is shown.
4. **Execution timeframe** – one of M1 M2 M3 M4 M5 M10 M15 M30 H1 H4 H12 D1 W1 MN1.
5. **Period** – quick ranges (7/30/90/180/365 days) or custom dates `YYYY-MM-DD` → `YYYY-MM-DD|today` (inclusive, in the chosen report timezone, default UTC+3).

Then the **options** screen (all editable): initial balance, spread (`auto` = gold 25 / oil 4 / forex 1.2 pips, or one number), commission per lot, risk % (used when the strategy does not send lots), max open positions, **daily loss limit $** and **daily profit target $** (like the reference workbook's "أيام الإيقاف": worst intrabar floating loss counts), timezone offset, output (`both`/`xlsx`/`csv`) and **Strategy parameters** (overrides for this run only).

Press **🚀 RUN BACKTEST**: progress is edited live, **⛔ Cancel run** stops it. One backtest runs at a time.

## Result
A summary message (balance, net, return %, trades, win rate, profit factor, payoff, expectancy, max drawdown, recovery factor, streaks, pips, Sharpe, per-symbol lines) plus:
* **Excel (RTL, Arabic)** – sheets `الملخص`, `الصفقات` (every trade: symbol, entry/exit time, TF, direction, lots, entry, TP, SL, exit, reason, WIN/LOSS, pips, gross, commission, net, running balance, MAE/MFE pips, bars held, signal comment), `حسب الرمز`, `يومي`, `شهري`, `أيام الإيقاف`, `إشارات متجاهلة`, `منحنى الرصيد`, `الإعدادات` (all options, strategy parameters, assumptions, warnings).
* **CSV** – all trades (UTF-8 with BOM).
Runs are stored in `backtest_runs` (config + brief summary).

## How the simulation works (assumptions)
* The strategy runs in the **same sandbox as live bots** (`bt_chunk` batches; history is kept inside the sandbox). It sees closed bars only; `on_start` is called once; order/position callbacks are not fed.
* A signal on bar *i* is executed at the **open of bar i+1** of that symbol. Chart prices are Bid: BUY fills at Ask = open + spread and exits at Bid; SELL fills at Bid and exits at Ask.
* SL/TP are checked on the bar's high/low; if both are touched in one bar the **stop loss wins** (pessimistic). Gaps fill at the open.
* Profit = price difference × lots × contract units × quote→deposit rate (historical rate from the broker's conversion pair when the quote currency differs from the account currency; otherwise 1.0 with a warning).
* Sizing: `volume_lots` from the strategy if present (rounded to the symbol step, capped), otherwise risk % with the SL distance; signals that cannot be sized are listed in `إشارات متجاهلة`.
* **Not simulated:** swaps, slippage, margin/stop-out, pending orders, the live Risk Engine's locks/limits, news/liquidity effects. Bar data limits accuracy – smaller timeframes are more precise but heavier.
* Limits: ≈150,000 bars per run (shorten the period / use a larger timeframe), 20-minute wall-clock cap, history depth = what the broker serves.

> Not runtime-verified against a live cTrader account in the build environment; the engine, sandbox protocol and reports were verified offline with synthetic data (`tests/test_backtest_*.py`).
