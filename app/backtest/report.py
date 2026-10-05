"""Backtest reports: Telegram summary (HTML), Excel workbook (Arabic, RTL, like the reference file) and CSV."""
from __future__ import annotations

import csv
import html
import io
from datetime import datetime, timezone
from typing import Any

from app.backtest.engine import BtResult, Trade

TRADE_HEADERS = ["#", "الزوج", "وقت الدخول", "TF", "اتجاه", "اللوت", "سعر الدخول", "الهدف (TP)", "الوقف (SL)", "وقت الخروج", "سعر الخروج",
                 "سبب الخروج", "النتيجة", "النقاط", "الربح الإجمالي ($)", "العمولة ($)", "صافي الربح ($)", "رصيد تراكمي ($)",
                 "أقصى تراجع بالصفقة (نقاط)", "أقصى ربح عائم (نقاط)", "عدد الشموع", "تعليق الإشارة"]
REASON_AR = {"TP": "TP ✅ هدف", "SL": "SL ❌ وقف", "SIGNAL_CLOSE": "إغلاق بإشارة", "DAILY_LOSS_LIMIT": "🛑 حد خسارة يومي",
             "DAILY_PROFIT_TARGET": "✅ هدف ربح يومي", "END_OF_TEST": "نهاية الاختبار"}


def _fmt(ts: int, tz_ms: int) -> str:
    return datetime.fromtimestamp((ts + tz_ms) / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")


def _result(t: Trade) -> str:
    return "WIN ✅" if t.net > 0 else ("LOSS ❌" if t.net < 0 else "BE ➖")


def _num(v: float | None, d: int = 2, suffix: str = "") -> str:
    return "—" if v is None else f"{v:,.{d}f}{suffix}"


def trade_row(t: Trade, tf: str, tz_ms: int, digits: int = 5) -> list[Any]:
    return [t.n, t.symbol, _fmt(t.entry_ts, tz_ms), tf, "BUY 📈" if t.side == "BUY" else "SELL 📉", t.lots, t.entry_price, t.tp, t.sl,
            _fmt(t.exit_ts, tz_ms), t.exit_price, REASON_AR.get(t.reason, t.reason), _result(t), round(t.pips, 1), round(t.gross, 2),
            round(t.commission, 2), round(t.net, 2), round(t.balance_after, 2), round(t.mae_pips, 1), round(t.mfe_pips, 1), t.bars, t.comment]


def summary_rows(r: BtResult, meta: dict[str, Any]) -> list[tuple[str, Any]]:
    s = r.stats
    ccy = r.cfg.deposit_ccy
    return [
        ("الاستراتيجية", f"{meta['strategy']} v{meta['version']}"), ("الحساب (مصدر البيانات)", meta["account"]), ("الرموز", ", ".join(meta["symbols"])),
        ("فريم التنفيذ", meta["timeframe"]), ("الفترة", f"{meta['date_from']} → {meta['date_to']} ({meta['tz_label']})"),
        ("عدد الشموع المعالجة", meta["bars"]), ("مدة التشغيل (ثانية)", round(meta.get("seconds", 0), 1)), ("", ""),
        (f"الرصيد الابتدائي ({ccy})", round(s["initial_balance"], 2)), (f"الرصيد النهائي ({ccy})", round(s["final_balance"], 2)),
        (f"صافي الربح ({ccy})", round(s["net_profit"], 2)), ("العائد %", round(s["return_pct"], 2)),
        (f"إجمالي الربح ({ccy})", round(s["gross_profit"], 2)), (f"إجمالي الخسارة ({ccy})", round(s["gross_loss"], 2)),
        ("معامل الربح (Profit Factor)", None if s["profit_factor"] is None else round(s["profit_factor"], 2)),
        ("عدد الصفقات", s["total_trades"]), ("رابحة", s["wins"]), ("خاسرة", s["losses"]), ("متعادلة", s["breakeven"]), ("نسبة النجاح %", round(s["win_rate"], 1)),
        ("متوسط الربح", round(s["avg_win"], 2)), ("متوسط الخسارة", round(s["avg_loss"], 2)),
        ("نسبة الربح/الخسارة (Payoff)", None if s["payoff_ratio"] is None else round(s["payoff_ratio"], 2)),
        ("العائد المتوقع للصفقة", round(s["expected_payoff"], 2)), ("أكبر ربح", round(s["largest_win"], 2)), ("أكبر خسارة", round(s["largest_loss"], 2)),
        ("أطول سلسلة ربح (عدد)", s["max_consec_wins"]), ("مجموعها", round(s["max_consec_wins_amount"], 2)),
        ("أطول سلسلة خسارة (عدد)", s["max_consec_losses"]), ("مجموعها", round(s["max_consec_losses_amount"], 2)),
        ("إجمالي النقاط", round(s["total_pips"], 1)), ("متوسط النقاط/صفقة", round(s["avg_pips"], 1)),
        ("أقصى تراجع (قيمة)", round(s["max_drawdown"], 2)), ("أقصى تراجع %", round(s["max_drawdown_pct"], 2)),
        ("عامل التعافي", None if s["recovery_factor"] is None else round(s["recovery_factor"], 2)),
        ("نسبة شارب (يومية، سنوية)", None if s["sharpe"] is None else round(s["sharpe"], 2)),
        ("صفقات شراء / ربحها", f"{s['buys']} / {round(s['buy_net'], 2)}"), ("صفقات بيع / ربحها", f"{s['sells']} / {round(s['sell_net'], 2)}"),
        ("متوسط مدة الصفقة (ساعات)", round(s["avg_hold_hours"], 1)), ("العمولات", round(s["commission_total"], 2)),
        ("إشارات صدرت / متجاهلة", f"{r.signals_total} / {len(r.skipped)}"), ("أيام توقف (حد يومي)", len(r.stopped_days)),
    ]


def settings_rows(r: BtResult, meta: dict[str, Any]) -> list[tuple[str, Any]]:
    c = r.cfg
    rows: list[tuple[str, Any]] = [
        ("الرصيد الابتدائي", c.initial_balance), ("عملة الحساب", c.deposit_ccy), ("نسبة المخاطرة % (عند عدم تحديد لوت)", c.risk_pct),
        ("عمولة لكل لوت (دورة كاملة)", c.commission_per_lot), ("أقصى صفقات مفتوحة", c.max_open_positions), ("أقصى لوت", c.max_lots),
        ("حد الخسارة اليومي ($)", c.daily_loss_limit or "معطل"), ("هدف الربح اليومي ($)", c.daily_profit_target or "معطل"),
        ("فارق التوقيت (ساعة)", c.tz_offset_hours),
    ]
    for sym, sp in meta.get("spreads", {}).items():
        rows.append((f"السبريد {sym} (نقاط)", sp))
    rows.append(("", ""))
    rows.append(("إعدادات الاستراتيجية المستخدمة", ""))
    rows += [(k, v) for k, v in meta.get("params", {}).items()]
    rows.append(("", ""))
    rows.append(("افتراضات المحاكاة", ""))
    rows += [("تنفيذ الإشارة", "عند افتتاح الشمعة التالية بعد إغلاق شمعة الإشارة"),
             ("الأسعار", "Bid من الشارت؛ الشراء بسعر Ask = Open + السبريد، البيع بسعر Bid"),
             ("SL و TP في نفس الشمعة", "يُحتسب الوقف أولًا (تقدير متحفظ)"),
             ("الفجوات", "الوقف يُنفذ بسعر الافتتاح؛ الهدف بسعر الافتتاح إن كان أفضل"),
             ("غير محاكى", "السواب، الانزلاق، الهامش/Stop-out، الأوامر المعلقة، قيود محرك المخاطر الحي"),
             ("دقة البيانات", "شموع OHLC من وسيطك عبر cTrader؛ كلما صغر الفريم زادت الدقة")]
    for w in r.warnings:
        rows.append(("تحذير", w))
    return rows


def build_summary_text(r: BtResult, meta: dict[str, Any]) -> str:
    s = r.stats
    e = html.escape
    pf = "∞" if s["profit_factor"] is None and s["gross_profit"] > 0 else _num(s["profit_factor"])
    lines = [
        f"🧪 <b>Backtest — {e(meta['strategy'])} v{e(meta['version'])}</b>",
        f"{e(', '.join(meta['symbols']))} · <b>{e(meta['timeframe'])}</b>",
        f"{e(meta['date_from'])} → {e(meta['date_to'])} ({e(meta['tz_label'])}) · {meta['bars']:,} bars · {meta.get('seconds', 0):.0f}s",
        "",
        f"💰 الرصيد: {_num(s['initial_balance'])} → <b>{_num(s['final_balance'])}</b> {e(r.cfg.deposit_ccy)}",
        f"{'🟢' if s['net_profit'] >= 0 else '🔴'} صافي الربح: <b>{s['net_profit']:+,.2f}</b> ({s['return_pct']:+.2f}%)",
        f"📊 الصفقات: <b>{s['total_trades']}</b> (✅ {s['wins']} / ❌ {s['losses']}) · النجاح <b>{s['win_rate']:.1f}%</b>",
        f"⚖️ Profit Factor: <b>{pf}</b> · Payoff: {_num(s['payoff_ratio'])} · Expectancy: {s['expected_payoff']:+.2f}",
        f"📉 أقصى تراجع: <b>{_num(s['max_drawdown'])}</b> ({s['max_drawdown_pct']:.2f}%) · Recovery: {_num(s['recovery_factor'])}",
        f"🏆 أكبر ربح {s['largest_win']:+.2f} · أكبر خسارة {s['largest_loss']:+.2f}",
        f"🔁 أطول ربح متتالٍ {s['max_consec_wins']} · أطول خسارة متتالية {s['max_consec_losses']}",
        f"📐 النقاط: {s['total_pips']:+.1f} (متوسط {s['avg_pips']:+.1f}) · شارب {_num(s['sharpe'])}",
    ]
    if r.per_symbol:
        lines.append("")
        lines.append("<b>حسب الرمز</b>")
        for p in r.per_symbol[:12]:
            lines.append(f"• {e(p['symbol'])}: {p['trades']} صفقة · {p['win_rate']:.0f}% · {p['net']:+,.2f}")
    if r.stopped_days:
        lines.append(f"\n🛑 أيام توقف بسبب الحد اليومي: {len(r.stopped_days)}")
    for w in r.warnings[:4]:
        lines.append(f"⚠️ {e(w)}")
    lines.append("\n<i>محاكاة على شموع OHLC؛ لا تضمن نتائج مستقبلية.</i>")
    return "\n".join(lines)[:3900]


def build_csv(r: BtResult, meta: dict[str, Any]) -> bytes:
    tz_ms = int(r.cfg.tz_offset_hours * 3_600_000)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(TRADE_HEADERS)
    for t in r.trades:
        w.writerow(trade_row(t, meta["timeframe"], tz_ms))
    return ("\ufeff" + buf.getvalue()).encode("utf-8")        # BOM so Excel shows Arabic correctly


def _sample(points: list, limit: int = 5000) -> list:
    if len(points) <= limit:
        return points
    step = len(points) / limit
    return [points[int(i * step)] for i in range(limit)] + [points[-1]]


def build_xlsx(r: BtResult, meta: dict[str, Any]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    tz_ms = int(r.cfg.tz_offset_hours * 3_600_000)
    base = Font(name="Arial", size=10)
    bold = Font(name="Arial", size=10, bold=True)
    head_font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="1F4E78")
    green, red = Font(name="Arial", size=10, color="0B7A32"), Font(name="Arial", size=10, color="B00020")
    thin = Side(style="thin", color="D0D0D0")
    border = Border(top=thin, bottom=thin, left=thin, right=thin)
    center = Alignment(horizontal="center", vertical="center", wrap_text=False)

    wb = Workbook()
    wb.remove(wb.active)

    def sheet(title: str, headers: list[str], rows: list[list[Any]], widths: list[int] | None = None, fmts: dict[int, str] | None = None,
              color_col: int | None = None) -> Any:
        ws = wb.create_sheet(title)
        ws.sheet_view.rightToLeft = True
        ws.append(headers)
        for c in range(1, len(headers) + 1):
            cell = ws.cell(1, c)
            cell.font, cell.fill, cell.alignment, cell.border = head_font, head_fill, center, border
        for row in rows:
            ws.append(row)
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
            for cell in row:
                cell.font, cell.border = base, border
                cell.alignment = Alignment(horizontal="center", vertical="center")
            if color_col is not None and row[color_col - 1].value is not None:
                v = row[color_col - 1].value
                if isinstance(v, (int, float)):
                    row[color_col - 1].font = green if v > 0 else (red if v < 0 else base)
                else:
                    row[color_col - 1].font = green if "WIN" in str(v) else (red if "LOSS" in str(v) else base)
        for ci, f in (fmts or {}).items():
            for rr in range(2, ws.max_row + 1):
                ws.cell(rr, ci).number_format = f
        for i, h in enumerate(headers, start=1):
            ws.column_dimensions[get_column_letter(i)].width = (widths[i - 1] if widths else max(12, min(32, len(str(h)) + 4)))
        ws.freeze_panes = "A2"
        if rows:
            ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{ws.max_row}"
        return ws

    # 1. summary
    ws = wb.create_sheet("الملخص")
    ws.sheet_view.rightToLeft = True
    ws.append(["المؤشر", "القيمة"])
    for c in (1, 2):
        ws.cell(1, c).font, ws.cell(1, c).fill, ws.cell(1, c).alignment = head_font, head_fill, center
    for k, v in summary_rows(r, meta):
        ws.append([k, v])
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        row[0].font, row[1].font = bold if row[1].value in (None, "") else base, base
        row[1].alignment = Alignment(horizontal="center")
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 38, 46

    # 2. trades (same columns idea as the reference workbook, plus details)
    trade_rows = [trade_row(t, meta["timeframe"], tz_ms) for t in r.trades]
    ws_t = sheet("الصفقات", TRADE_HEADERS, trade_rows,
                 [6, 12, 17, 6, 11, 8, 12, 12, 12, 17, 12, 18, 11, 10, 14, 11, 14, 15, 14, 14, 9, 28],
                 {6: "0.00", 7: "0.00000", 8: "0.00000", 9: "0.00000", 11: "0.00000", 14: "0.0", 15: "#,##0.00", 16: "#,##0.00", 17: "#,##0.00", 18: "#,##0.00"},
                 color_col=13)
    for rr in range(2, ws_t.max_row + 1):
        v = ws_t.cell(rr, 17).value
        if isinstance(v, (int, float)):
            ws_t.cell(rr, 17).font = green if v > 0 else (red if v < 0 else base)

    # 3. by symbol / 4. daily / 5. monthly
    sheet("حسب الرمز", ["الرمز", "الصفقات", "رابحة", "خاسرة", "نسبة النجاح %", "صافي الربح ($)", "النقاط", "Profit Factor", "متوسط الصفقة ($)"],
          [[p["symbol"], p["trades"], p["wins"], p["losses"], round(p["win_rate"], 1), round(p["net"], 2), round(p["pips"], 1),
            None if p["profit_factor"] is None else round(p["profit_factor"], 2), round(p["avg_net"], 2)] for p in r.per_symbol], color_col=6)
    sheet("يومي", ["التاريخ", "الصفقات", "الرابحة", "صافي اليوم ($)", "الرصيد ($)"],
          [[d["date"], d["trades"], d["wins"], round(d["net"], 2), round(d["balance"], 2)] for d in r.daily], color_col=4)
    sheet("شهري", ["الشهر", "الصفقات", "الرابحة", "صافي الشهر ($)", "العائد %", "الرصيد ($)"],
          [[m["month"], m["trades"], m["wins"], round(m["net"], 2), round(m["return_pct"], 2), round(m["balance"], 2)] for m in r.monthly], color_col=4)
    # 6. stopped days (same idea as the reference file)
    ws_s = sheet("أيام الإيقاف", ["التاريخ", "السبب (النتيجة)"], [[d["date"], d["reason"]] for d in r.stopped_days], [14, 120])
    # 7. skipped signals
    sheet("إشارات متجاهلة", ["الوقت", "الرمز", "الاتجاه", "السبب"], [[_fmt(s["ts"], tz_ms), s["symbol"], s["side"], s["reason"]] for s in r.skipped[:20000]], [18, 12, 10, 60])
    # 8. equity curve (sampled for Excel; CSV/summary use full data)
    sheet("منحنى الرصيد", ["الوقت", "الرصيد ($)", "الأسهم/Equity ($)"], [[_fmt(ts, tz_ms), round(b, 2), round(eq, 2)] for ts, b, eq in _sample(r.equity)], [18, 16, 18])
    # 9. settings
    ws_c = wb.create_sheet("الإعدادات")
    ws_c.sheet_view.rightToLeft = True
    ws_c.append(["البند", "القيمة"])
    for c in (1, 2):
        ws_c.cell(1, c).font, ws_c.cell(1, c).fill = head_font, head_fill
    for k, v in settings_rows(r, meta):
        ws_c.append([k, v if not isinstance(v, (dict, list)) else str(v)])
    for row in ws_c.iter_rows(min_row=2, max_row=ws_c.max_row):
        for cell in row:
            cell.font = base
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    ws_c.column_dimensions["A"].width, ws_c.column_dimensions["B"].width = 42, 80

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
