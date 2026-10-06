"""Pure-Python bar-based backtest engine (no I/O, no third-party packages).

Execution model (documented in docs/backtest.md):
  * the strategy sees CLOSED bars; a signal produced on bar i is executed at the OPEN of the next bar of that symbol;
  * chart prices are BID. BUY fills at ask = open + spread, exits at bid; SELL fills at bid, exits at ask = price + spread;
  * SL/TP are checked against the bar's high/low; when both are touched in the same bar the STOP LOSS wins (pessimistic);
    gaps fill at the open (worse for stops, better for targets);
  * profit = price difference x lots x contract units x quote->deposit rate at exit time; commission per lot is charged at close;
  * swaps, slippage and margin/stop-out are NOT simulated.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

DAY_MS = 86_400_000


@dataclass
class ConvSeries:
    times: list[int]
    rates: list[float]

    def at(self, ts: int) -> float:
        if not self.rates:
            return 1.0
        i = bisect.bisect_right(self.times, ts) - 1
        return self.rates[i if i >= 0 else 0]


@dataclass
class SymbolSpec:
    name: str
    pip_size: float
    lot_units: float                      # contract units per 1.0 lot
    digits: int = 5
    min_lots: float = 0.01
    step_lots: float = 0.01
    max_lots: float = 100.0
    spread_pips: float = 1.0
    quote_ccy: str = ""
    conv: ConvSeries | None = None        # quote -> deposit currency; None = same currency (rate 1)
    conv_missing: bool = False
    display_decimals: int = 5

    def rate(self, ts: int) -> float:
        return self.conv.at(ts) if self.conv else 1.0


@dataclass
class BtConfig:
    initial_balance: float = 10_000.0
    commission_per_lot: float = 0.0       # internal (not exposed in the UI)
    lot_override: float = 0.0             # >0: every trade uses this lot size; 0 = the strategy's volume_lots
    default_lots: float = 0.10            # used when neither the override nor the strategy gives a size
    sl_override: float = 0.0              # points; >0 replaces the strategy's stop loss
    tp_override: float = 0.0              # points; >0 replaces the strategy's take profit
    max_open_positions: int = 100
    max_lots: float = 100.0
    daily_loss_limit: float = 0.0         # deposit currency, 0 = off (worst intrabar floating counts)
    daily_profit_target: float = 0.0      # deposit currency, 0 = off
    tz_offset_hours: float = 3.0          # reporting timezone and trading-day boundary
    tf_ms: int = 900_000
    deposit_ccy: str = "USD"


@dataclass
class OpenPos:
    n: int
    symbol: str
    side: str
    lots: float
    entry_ts: int
    entry_price: float
    sl: float | None
    tp: float | None
    sl_pips: float | None
    tp_pips: float | None
    comment: str = ""
    signal_bar_ts: int = 0
    mae_pips: float = 0.0
    mfe_pips: float = 0.0
    bars: int = 0


@dataclass
class Trade:
    n: int
    symbol: str
    side: str
    lots: float
    entry_ts: int
    entry_price: float
    sl: float | None
    tp: float | None
    sl_pips: float | None
    tp_pips: float | None
    exit_ts: int
    exit_price: float
    reason: str                           # TP | SL | SIGNAL_CLOSE | DAILY_LOSS_LIMIT | DAILY_PROFIT_TARGET | END_OF_TEST
    pips: float
    gross: float
    commission: float
    net: float
    balance_after: float
    mae_pips: float
    mfe_pips: float
    bars: int
    comment: str
    signal_bar_ts: int


@dataclass
class BtResult:
    cfg: BtConfig
    trades: list[Trade]
    equity: list[tuple[int, float, float]]            # (ts, balance, equity)
    stopped_days: list[dict[str, Any]]
    skipped: list[dict[str, Any]]
    stats: dict[str, Any]
    per_symbol: list[dict[str, Any]]
    daily: list[dict[str, Any]]
    monthly: list[dict[str, Any]]
    warnings: list[str]
    signals_total: int = 0


class BacktestEngine:
    def __init__(self, cfg: BtConfig, specs: dict[str, SymbolSpec]) -> None:
        self.cfg, self.specs = cfg, specs
        self.balance = cfg.initial_balance
        self.open: list[OpenPos] = []
        self.trades: list[Trade] = []
        self.pending: dict[str, list[dict[str, Any]]] = {}
        self.last: dict[str, tuple[int, float]] = {}      # symbol -> (ts, last close)
        self.equity_curve: list[tuple[int, float, float]] = []
        self.skipped: list[dict[str, Any]] = []
        self.stopped_days: list[dict[str, Any]] = []
        self._n = 0
        self._day = None
        self._day_realized = 0.0
        self._day_stopped = False
        self._tz_ms = int(cfg.tz_offset_hours * 3_600_000)
        self.signals_total = 0
        self._sym_ci = {k.upper(): k for k in specs}

    # ---- helpers -----------------------------------------------------------------------------------
    def local_day(self, ts: int) -> int:
        return (ts + self._tz_ms) // DAY_MS

    def local_dt(self, ts: int) -> datetime:
        return datetime.fromtimestamp((ts + self._tz_ms) / 1000, timezone.utc)

    def _roll_day(self, ts: int) -> None:
        d = self.local_day(ts)
        if d != self._day:
            self._day, self._day_realized, self._day_stopped = d, 0.0, False

    def _spread(self, spec: SymbolSpec) -> float:
        return spec.spread_pips * spec.pip_size

    def _float_pnl(self, pos: OpenPos, price_bid: float, ts: int) -> float:
        spec = self.specs[pos.symbol]
        diff = (price_bid - pos.entry_price) if pos.side == "BUY" else (pos.entry_price - (price_bid + self._spread(spec)))
        return diff * pos.lots * spec.lot_units * spec.rate(ts)

    def _floating(self, ts: int, override: dict[str, float] | None = None) -> float:
        total = 0.0
        for pos in self.open:
            px = (override or {}).get(pos.symbol)
            if px is None:
                px = self.last.get(pos.symbol, (ts, pos.entry_price))[1]
            total += self._float_pnl(pos, px, ts)
        return total

    # ---- sizing ------------------------------------------------------------------------------------
    def _size(self, spec: SymbolSpec, ts: int, req: float | None) -> tuple[float | None, str]:
        step = spec.step_lots or 0.01
        lots = self.cfg.lot_override or req or self.cfg.default_lots
        lots = min(lots, self.cfg.max_lots, spec.max_lots)
        lots = round(math.floor(lots / step + 1e-9) * step, 8)
        if lots < spec.min_lots - 1e-12:
            return None, "BELOW_MIN_LOTS"
        return lots, ""

    # ---- trade lifecycle -------------------------------------------------------------------------------
    def _skip(self, ts: int, symbol: str, side: str, reason: str) -> None:
        self.skipped.append({"ts": ts, "symbol": symbol, "side": side, "reason": reason})

    def _open(self, ts: int, spec: SymbolSpec, bar: dict, p: dict[str, Any]) -> None:
        if self._day_stopped:
            return self._skip(ts, spec.name, p["side"], "DAY_STOPPED (daily limit reached)")
        if len(self.open) >= self.cfg.max_open_positions:
            return self._skip(ts, spec.name, p["side"], "MAX_OPEN_POSITIONS")
        sl_pips = self.cfg.sl_override or p.get("sl_pips") or None
        tp_pips = self.cfg.tp_override or p.get("tp_pips") or None
        lots, why = self._size(spec, ts, p.get("lots"))
        if lots is None:
            return self._skip(ts, spec.name, p["side"], why)
        spread = self._spread(spec)
        buy = p["side"] == "BUY"
        entry = bar["o"] + spread if buy else bar["o"]
        sgn = 1 if buy else -1
        sl = round(entry - sgn * sl_pips * spec.pip_size, spec.digits) if sl_pips else None
        tp = round(entry + sgn * tp_pips * spec.pip_size, spec.digits) if tp_pips else None
        self._n += 1
        self.open.append(OpenPos(self._n, spec.name, p["side"], lots, ts, round(entry, spec.digits), sl, tp, sl_pips, tp_pips,
                                 p.get("comment") or "", p.get("signal_ts", 0)))

    def _close(self, pos: OpenPos, ts: int, price: float, reason: str) -> None:
        spec = self.specs[pos.symbol]
        diff = (price - pos.entry_price) if pos.side == "BUY" else (pos.entry_price - price)
        gross = diff * pos.lots * spec.lot_units * spec.rate(ts)
        comm = self.cfg.commission_per_lot * pos.lots
        net = gross - comm
        self.balance += net
        self._day_realized += net
        self.trades.append(Trade(pos.n, pos.symbol, pos.side, pos.lots, pos.entry_ts, pos.entry_price, pos.sl, pos.tp, pos.sl_pips, pos.tp_pips,
                                 ts, round(price, spec.digits), reason, diff / spec.pip_size, gross, comm, net, self.balance,
                                 pos.mae_pips, pos.mfe_pips, pos.bars, pos.comment, pos.signal_bar_ts))
        self.open.remove(pos)

    def _exec_pending(self, ts: int, spec: SymbolSpec, bar: dict, p: dict[str, Any]) -> None:
        if p["kind"] == "CLOSE":
            spread = self._spread(spec)
            for pos in [x for x in self.open if x.symbol == spec.name and (p["side"] is None or x.side == p["side"])]:
                self._close(pos, ts, bar["o"] if pos.side == "BUY" else bar["o"] + spread, "SIGNAL_CLOSE")
        else:
            self._open(ts, spec, bar, p)

    def _manage(self, ts: int, spec: SymbolSpec, bar: dict) -> None:
        o, h, l = bar["o"], bar["h"], bar["l"]
        spread = self._spread(spec)
        end_ts = ts + self.cfg.tf_ms
        for pos in [x for x in self.open if x.symbol == spec.name]:
            pos.bars += 1
            if pos.side == "BUY":
                pos.mae_pips = max(pos.mae_pips, (pos.entry_price - l) / spec.pip_size)
                pos.mfe_pips = max(pos.mfe_pips, (h - pos.entry_price) / spec.pip_size)
                if pos.sl is not None and l <= pos.sl:
                    self._close(pos, ts if o <= pos.sl else end_ts, o if o <= pos.sl else pos.sl, "SL")
                elif pos.tp is not None and h >= pos.tp:
                    self._close(pos, ts if o >= pos.tp else end_ts, max(pos.tp, o), "TP")
            else:
                pos.mae_pips = max(pos.mae_pips, (h + spread - pos.entry_price) / spec.pip_size)
                pos.mfe_pips = max(pos.mfe_pips, (pos.entry_price - (l + spread)) / spec.pip_size)
                if pos.sl is not None and h + spread >= pos.sl:
                    gap = o + spread >= pos.sl
                    self._close(pos, ts if gap else end_ts, o + spread if gap else pos.sl, "SL")
                elif pos.tp is not None and l + spread <= pos.tp:
                    gap = o + spread <= pos.tp
                    self._close(pos, ts if gap else end_ts, o + spread if gap else pos.tp, "TP")

    def _check_daily(self, ts: int, spec: SymbolSpec, bar: dict) -> None:
        cfg = self.cfg
        if self._day_stopped or not self.open or not (cfg.daily_loss_limit > 0 or cfg.daily_profit_target > 0):
            return
        worst_px = {}
        for pos in self.open:
            if pos.symbol == spec.name:
                worst_px[pos.symbol] = bar["l"] if pos.side == "BUY" else bar["h"]
        # worst moment inside the candle for the current symbol; other symbols at their last close
        floating_worst = 0.0
        for pos in self.open:
            px = worst_px.get(pos.symbol, self.last.get(pos.symbol, (ts, pos.entry_price))[1])
            floating_worst += self._float_pnl(pos, px, ts)
        realized = self._day_realized
        date = self.local_dt(ts).strftime("%Y-%m-%d")
        if cfg.daily_loss_limit > 0 and realized + floating_worst <= -cfg.daily_loss_limit:
            reason = (f"🛑 تراجع عائم (الحد -{cfg.daily_loss_limit:.1f}$ | المحقق: {realized:.1f}$ + العائم (أسوأ لحظة داخل الشمعة): "
                      f"{floating_worst:.1f}$ = {realized + floating_worst:.1f}$)")
            for pos in list(self.open):
                px = worst_px.get(pos.symbol)
                spread = self._spread(self.specs[pos.symbol])
                if px is None:
                    px = self.last.get(pos.symbol, (ts, pos.entry_price))[1]
                self._close(pos, ts + cfg.tf_ms, px if pos.side == "BUY" else px + spread, "DAILY_LOSS_LIMIT")
            self._day_stopped = True
            self.stopped_days.append({"date": date, "kind": "LOSS", "reason": reason})
            return
        floating_close = self._floating(ts)
        if cfg.daily_profit_target > 0 and realized + floating_close >= cfg.daily_profit_target:
            reason = (f"✅ هدف عائم (الحد {cfg.daily_profit_target:.1f}$ | المحقق: {realized:.1f}$ + العائم: {floating_close:.2f}$ = "
                      f"{realized + floating_close:.2f}$)")
            for pos in list(self.open):
                px = self.last.get(pos.symbol, (ts, pos.entry_price))[1]
                spread = self._spread(self.specs[pos.symbol])
                self._close(pos, ts + cfg.tf_ms, px if pos.side == "BUY" else px + spread, "DAILY_PROFIT_TARGET")
            self._day_stopped = True
            self.stopped_days.append({"date": date, "kind": "TARGET", "reason": reason})

    # ---- main entry point --------------------------------------------------------------------------------------
    def feed(self, ts: int, symbol: str, bar: dict, signals: list[dict[str, Any]]) -> None:
        spec = self.specs[symbol]
        self._roll_day(ts)
        self.last[symbol] = (ts, bar["c"])
        for p in self.pending.pop(symbol, []):
            self._exec_pending(ts, spec, bar, p)
        self._manage(ts, spec, bar)
        self._check_daily(ts, spec, bar)
        self.equity_curve.append((ts, self.balance, self.balance + self._floating(ts)))
        for s in signals:
            self.signals_total += 1
            side = s.get("side")
            target = self._sym_ci.get(str(s.get("symbol", "")).upper(), symbol)     # strategies may signal another configured symbol
            if side == "CLOSE":
                self.pending.setdefault(target, []).append({"kind": "CLOSE", "side": s.get("close_side")})
            elif side in ("BUY", "SELL"):
                if s.get("order_type", "MARKET") != "MARKET":
                    self._skip(ts, target, side, "PENDING_ORDERS_NOT_SUPPORTED_IN_BACKTEST")
                    continue
                self.pending.setdefault(target, []).append({"kind": "OPEN", "side": side, "sl_pips": s.get("stop_loss_pips"),
                                                            "tp_pips": s.get("take_profit_pips"), "lots": s.get("volume_lots"),
                                                            "comment": s.get("comment"), "signal_ts": ts + self.cfg.tf_ms})

    def finish(self) -> BtResult:
        warnings: list[str] = []
        for pos in list(self.open):
            spec = self.specs[pos.symbol]
            ts, px = self.last.get(pos.symbol, (pos.entry_ts, pos.entry_price))
            self._close(pos, ts + self.cfg.tf_ms, px if pos.side == "BUY" else px + self._spread(spec), "END_OF_TEST")
        if self.equity_curve:
            ts = self.equity_curve[-1][0]
            self.equity_curve.append((ts, self.balance, self.balance))
        for sp in self.specs.values():
            if sp.conv_missing:
                warnings.append(f"{sp.name}: no {sp.quote_ccy}->{self.cfg.deposit_ccy} conversion data; profit computed with rate 1.0")
        if self.skipped:
            warnings.append(f"{len(self.skipped)} signal(s) skipped (see sheet 'إشارات متجاهلة')")
        stats = compute_stats(self.trades, self.equity_curve, self.cfg, self._tz_ms)
        return BtResult(self.cfg, self.trades, self.equity_curve, self.stopped_days, self.skipped, stats,
                        per_symbol_stats(self.trades), daily_rows(self.trades, self.cfg, self._tz_ms),
                        monthly_rows(self.trades, self.cfg, self._tz_ms), warnings, self.signals_total)


# ---- statistics ---------------------------------------------------------------------------------------------
def _streak(trades: list[Trade], win: bool) -> tuple[int, float]:
    best_n = cur_n = 0
    best_sum = cur_sum = 0.0
    for t in trades:
        ok = t.net > 0 if win else t.net < 0
        if ok:
            cur_n += 1
            cur_sum += t.net
            if cur_n > best_n:
                best_n, best_sum = cur_n, cur_sum
        else:
            cur_n, cur_sum = 0, 0.0
    return best_n, best_sum


def compute_stats(trades: list[Trade], equity: list[tuple[int, float, float]], cfg: BtConfig, tz_ms: int) -> dict[str, Any]:
    n = len(trades)
    wins = [t for t in trades if t.net > 0]
    losses = [t for t in trades if t.net < 0]
    gp = sum(t.net for t in wins)
    gl = sum(t.net for t in losses)
    net = sum(t.net for t in trades)
    peak = cfg.initial_balance
    max_dd = max_dd_pct = 0.0
    for _, _, eq in equity:
        peak = max(peak, eq)
        dd = peak - eq
        if dd > max_dd:
            max_dd, max_dd_pct = dd, (dd / peak * 100 if peak else 0.0)
    # daily returns for Sharpe
    last_eq: dict[int, float] = {}
    for ts, _, eq in equity:
        last_eq[(ts + tz_ms) // DAY_MS] = eq
    days = sorted(last_eq)
    rets = []
    prev = cfg.initial_balance
    for d in days:
        rets.append((last_eq[d] - prev) / prev if prev else 0.0)
        prev = last_eq[d]
    sharpe = None
    if len(rets) >= 5:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        sharpe = (mean / math.sqrt(var) * math.sqrt(252)) if var > 0 else None
    cw, cw_sum = _streak(trades, True)
    cl, cl_sum = _streak(trades, False)
    held = [(t.exit_ts - t.entry_ts) / 1000 for t in trades]
    buys = [t for t in trades if t.side == "BUY"]
    sells = [t for t in trades if t.side == "SELL"]
    return {
        "initial_balance": cfg.initial_balance, "final_balance": cfg.initial_balance + net, "net_profit": net,
        "return_pct": (net / cfg.initial_balance * 100) if cfg.initial_balance else 0.0,
        "gross_profit": gp, "gross_loss": gl, "profit_factor": (gp / abs(gl)) if gl < 0 else None,
        "total_trades": n, "wins": len(wins), "losses": len(losses), "breakeven": n - len(wins) - len(losses),
        "win_rate": (len(wins) / n * 100) if n else 0.0,
        "avg_win": (gp / len(wins)) if wins else 0.0, "avg_loss": (gl / len(losses)) if losses else 0.0,
        "payoff_ratio": ((gp / len(wins)) / abs(gl / len(losses))) if wins and losses else None,
        "expected_payoff": (net / n) if n else 0.0,
        "largest_win": max((t.net for t in trades), default=0.0), "largest_loss": min((t.net for t in trades), default=0.0),
        "max_consec_wins": cw, "max_consec_wins_amount": cw_sum, "max_consec_losses": cl, "max_consec_losses_amount": cl_sum,
        "total_pips": sum(t.pips for t in trades), "avg_pips": (sum(t.pips for t in trades) / n) if n else 0.0,
        "avg_mae_pips": (sum(t.mae_pips for t in trades) / n) if n else 0.0, "avg_mfe_pips": (sum(t.mfe_pips for t in trades) / n) if n else 0.0,
        "max_drawdown": max_dd, "max_drawdown_pct": max_dd_pct, "recovery_factor": (net / max_dd) if max_dd > 0 else None,
        "sharpe": sharpe, "commission_total": sum(t.commission for t in trades),
        "buys": len(buys), "sells": len(sells), "buy_net": sum(t.net for t in buys), "sell_net": sum(t.net for t in sells),
        "avg_hold_hours": (sum(held) / n / 3600) if n else 0.0, "trading_days": len(days),
        "best_day": None, "worst_day": None,
    }


def per_symbol_stats(trades: list[Trade]) -> list[dict[str, Any]]:
    out = []
    for sym in sorted({t.symbol for t in trades}):
        ts = [t for t in trades if t.symbol == sym]
        w = [t for t in ts if t.net > 0]
        gl = sum(t.net for t in ts if t.net < 0)
        out.append({"symbol": sym, "trades": len(ts), "wins": len(w), "losses": len([t for t in ts if t.net < 0]),
                    "win_rate": len(w) / len(ts) * 100, "net": sum(t.net for t in ts), "pips": sum(t.pips for t in ts),
                    "profit_factor": (sum(t.net for t in w) / abs(gl)) if gl < 0 else None, "avg_net": sum(t.net for t in ts) / len(ts)})
    return out


def daily_rows(trades: list[Trade], cfg: BtConfig, tz_ms: int) -> list[dict[str, Any]]:
    by: dict[int, list[Trade]] = {}
    for t in trades:
        by.setdefault((t.exit_ts + tz_ms) // DAY_MS, []).append(t)
    rows = []
    for d in sorted(by):
        ts = by[d]
        rows.append({"date": datetime.fromtimestamp(d * DAY_MS / 1000, timezone.utc).strftime("%Y-%m-%d"), "trades": len(ts),
                     "wins": sum(1 for t in ts if t.net > 0), "net": sum(t.net for t in ts), "balance": ts[-1].balance_after})
    return rows


def monthly_rows(trades: list[Trade], cfg: BtConfig, tz_ms: int) -> list[dict[str, Any]]:
    by: dict[str, list[Trade]] = {}
    for t in trades:
        by.setdefault(datetime.fromtimestamp((t.exit_ts + tz_ms) / 1000, timezone.utc).strftime("%Y-%m"), []).append(t)
    rows, start = [], cfg.initial_balance
    for m in sorted(by):
        ts = by[m]
        net = sum(t.net for t in ts)
        rows.append({"month": m, "trades": len(ts), "wins": sum(1 for t in ts if t.net > 0), "net": net,
                     "return_pct": (net / start * 100) if start else 0.0, "balance": ts[-1].balance_after})
        start = ts[-1].balance_after
    return rows
