"""Symbol catalog: the *correct* broker symbol names for the instruments this platform trades.

Scope (as configured by the operator): GOLD, OIL and the 7 major forex pairs. Broker names differ
(XAUUSD vs GOLD, XTIUSD vs USOIL, suffixes like 'm' / '.r'), so the list is built from the symbols the
connected cTrader account really offers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

FOREX_MAJORS = ("EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD")
GOLD_KEYS = ("XAUUSD", "GOLD")
OIL_KEYS = ("XTIUSD", "USOIL", "WTIUSD", "WTI", "XBRUSD", "UKOIL", "BRENT", "USCRUDE", "CRUDEOIL", "CL")
CATEGORY_ORDER = ("GOLD", "OIL", "FOREX")
CATEGORY_LABEL = {"GOLD": "🥇 Gold", "OIL": "🛢 Oil", "FOREX": "💱 Forex majors"}
# fallback names when the account is offline (unverified!)
FALLBACK = ["XAUUSD", "XTIUSD", "XBRUSD", *FOREX_MAJORS]
AUTO_SPREAD_PIPS = {"GOLD": 25.0, "OIL": 4.0, "FOREX": 1.2}     # backtest defaults (pips as cTrader defines them)


@dataclass(frozen=True)
class CatalogSymbol:
    name: str          # exact broker name
    category: str      # GOLD | OIL | FOREX
    base: str          # canonical key, e.g. EURUSD


def _norm(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def classify(name: str) -> CatalogSymbol | None:
    n = _norm(name)
    for cat, keys in (("FOREX", FOREX_MAJORS), ("GOLD", GOLD_KEYS), ("OIL", OIL_KEYS)):
        for k in keys:
            if n.startswith(k):
                rest = n[len(k):]
                # allow short broker suffixes (M, PRO, RAW ...), refuse look-alikes such as EURUSDT / XAUUSDK futures codes
                if rest == "" or (len(rest) <= 3 and rest.isalpha() and rest not in ("T",)):
                    if k == "CL" and rest != "":
                        continue
                    return CatalogSymbol(name=name, category=cat, base=k)
    return None


def build_catalog(broker_symbol_names: list[str]) -> list[CatalogSymbol]:
    found: dict[str, CatalogSymbol] = {}
    for nm in broker_symbol_names:
        c = classify(nm)
        if c and c.name not in found:
            found[c.name] = c
    order = {c: i for i, c in enumerate(CATEGORY_ORDER)}
    return sorted(found.values(), key=lambda s: (order[s.category], s.base, s.name))


class SymbolCatalog:
    def __init__(self, gateway) -> None:
        self.gateway = gateway

    def for_account(self, account_id: str) -> tuple[list[CatalogSymbol], bool]:
        """(symbols, verified). verified=False means the account is offline and a generic fallback list is returned."""
        sess = self.gateway.sessions.get(account_id)
        if sess and sess.symbols_by_id:
            cat = build_catalog([i.name for i in sess.symbols_by_id.values()])
            if cat:
                return cat, True
        return [CatalogSymbol(n, (classify(n).category if classify(n) else "FOREX"), n) for n in FALLBACK], False
