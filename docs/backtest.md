# Backtest

Telegram → **🧪 Backtest** (main menu, also under Strategies) → **New backtest**.

## Wizard
1. **Strategy** (active version).
2. **Account** – a *connected* cTrader account used only to download history and symbol specs (a DEMO account is fine). Nothing is ever traded.
3. **Symbols (select several)** – built from the symbols your broker really offers, limited to **Gold, Oil and the 7 major forex pairs**. Quick buttons select a whole group.
4. **Timeframes (select several)** – M1 M2 M3 M4 M5 M10 M15 M30 H1 H4 H12 D1 W1 MN1. Each timeframe is backtested separately and the Excel file compares them side by side.
5. **Days** – quick ranges (7/30/90/180/365) or a **calendar**: tap the start day, then the end day (future days are disabled; the end day is included). *⌨️ Type dates* accepts both in one message (`2026-06-01 2026-06-30`, `1/6/2026 30/6/2026`, Arabic digits, `today`).

**Options** (all editable): initial balance · lot size (0 = the strategy's own) · **stop loss in points** · **take-profit target in points** (0 = the strategy's own) · spread (`auto` = gold 2.5 pts / oil 4 / forex 1.2, or one number) · timezone · output (`both`/`xlsx`/`csv`) · strategy parameter overrides for this run. Risk-management options (risk %, commission, daily limits) were removed. Points are defined in `instruments.md` (gold: 4150→4152 = 20 points).

Press **🚀 RUN BACKTEST**: progress is edited live per timeframe, **⛔ Cancel run** stops it. One backtest runs at a time.

## Result
A summary message (balance, net, return %, trades, win rate, profit factor, payoff, expectancy, max drawdown, recovery factor, streaks, pips, Sharpe, per-symbol lines) plus:
* **Excel (RTL, Arabic)** – sheets `الملخص` (general info + one column per timeframe),  `الصفقات` (every trade: symbol, entry/exit time, TF, direction, lots, entry, TP, SL, exit, reason, WIN/LOSS, pips, gross, commission, net, running balance, MAE/MFE pips, bars held, signal comment), `حسب الرمز`, `يومي`, `شهري`, `أيام الإيقاف`, `إشارات متجاهلة`, `منحنى الرصيد`, `الإعدادات` (all options, strategy parameters, assumptions, warnings).
* **CSV** – all trades (UTF-8 with BOM).
Runs are stored in `backtest_runs` (config + brief summary).

## How the simulation works (assumptions)
* The strategy runs in the **same sandbox as live bots** (`bt_chunk` batches; history is kept inside the sandbox). It sees closed bars only; `on_start` is called once; order/position callbacks are not fed.
* A signal on bar *i* is executed at the **open of bar i+1** of that symbol. Chart prices are Bid: BUY fills at Ask = open + spread and exits at Bid; SELL fills at Bid and exits at Ask.
* SL/TP are checked on the bar's high/low; if both are touched in one bar the **stop loss wins** (pessimistic). Gaps fill at the open.
* Profit = price difference × lots × contract units × quote→deposit rate (historical rate from the broker's conversion pair when the quote currency differs from the account currency; otherwise 1.0 with a warning).
* Sizing: the *Lot size* option if set, otherwise the strategy's `volume_lots`, otherwise 0.10 lots (rounded to the symbol step). The *SL/TP points* options replace the strategy's values when set.
* **Not simulated:** swaps, slippage, margin/stop-out, pending orders, the live Risk Engine's locks/limits, news/liquidity effects. Bar data limits accuracy – smaller timeframes are more precise but heavier.
* Limits: ≈150,000 bars per timeframe (shorten the period / use a larger timeframe), 20-minute wall-clock cap, history depth = what the broker serves.

> Not runtime-verified against a live cTrader account in the build environment; the engine, sandbox protocol and reports were verified offline with synthetic data (`tests/test_backtest_*.py`).
