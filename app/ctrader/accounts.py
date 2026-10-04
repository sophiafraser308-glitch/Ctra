"""Protocol helpers for accounts: auth, account list, trader snapshot, assets, symbols."""
from __future__ import annotations

from typing import Any

from app.ctrader.pb import has, opt, sdk
from app.ctrader.types import BrokerTrader, SymbolInfo


def build_account_auth(ctid: int, access_token: str):
    r = sdk().msgs.ProtoOAAccountAuthReq()
    r.ctidTraderAccountId = ctid
    r.accessToken = access_token
    return r


def build_account_list_req(access_token: str):
    r = sdk().msgs.ProtoOAGetAccountListByAccessTokenReq()
    r.accessToken = access_token
    return r


def parse_account_list(res: Any) -> list[dict[str, Any]]:
    out = []
    for a in res.ctidTraderAccount:
        out.append({"ctid": int(a.ctidTraderAccountId), "is_live": bool(opt(a, "isLive", False)),
                    "login": int(opt(a, "traderLogin", 0)) or None,
                    "last_closing_deal_ts": opt(a, "lastClosingDealTimestamp"),
                    "last_balance_update_ts": opt(a, "lastBalanceUpdateTimestamp")})
    return out


def build_trader_req(ctid: int):
    r = sdk().msgs.ProtoOATraderReq()
    r.ctidTraderAccountId = ctid
    return r


def parse_trader(res: Any) -> BrokerTrader:
    t = res.trader
    digits = int(opt(t, "moneyDigits", 2))
    lev_cents = opt(t, "leverageInCents")
    return BrokerTrader(
        ctid=int(t.ctidTraderAccountId), balance=int(t.balance) / (10 ** digits), money_digits=digits,
        deposit_asset_id=int(opt(t, "depositAssetId", 0)) or None,
        leverage=(lev_cents / 100.0) if lev_cents else None, broker_name=opt(t, "brokerName"),
        trader_login=int(opt(t, "traderLogin", 0)) or None,
        access_rights=sdk().model.ProtoOAAccessRights.Name(t.accessRights) if has(t, "accessRights") else None)


def build_assets_req(ctid: int):
    r = sdk().msgs.ProtoOAAssetListReq()
    r.ctidTraderAccountId = ctid
    return r


def parse_assets(res: Any) -> dict[int, str]:
    return {int(a.assetId): a.name for a in res.asset}


def build_symbols_list_req(ctid: int):
    r = sdk().msgs.ProtoOASymbolsListReq()
    r.ctidTraderAccountId = ctid
    return r


def parse_light_symbols(res: Any) -> list[SymbolInfo]:
    return [SymbolInfo(symbol_id=int(s.symbolId), name=s.symbolName,
                       base_asset_id=int(opt(s, "baseAssetId", 0)) or None,
                       quote_asset_id=int(opt(s, "quoteAssetId", 0)) or None)
            for s in res.symbol if opt(s, "enabled", True)]


def build_symbol_by_id_req(ctid: int, ids: list[int]):
    r = sdk().msgs.ProtoOASymbolByIdReq()
    r.ctidTraderAccountId = ctid
    r.symbolId.extend(ids)
    return r


def apply_symbol_details(res: Any, by_id: dict[int, SymbolInfo]) -> None:
    for s in res.symbol:
        info = by_id.get(int(s.symbolId))
        if not info:
            continue
        info.digits = int(opt(s, "digits", info.digits))
        info.pip_position = int(opt(s, "pipPosition", info.pip_position))
        info.lot_size = int(opt(s, "lotSize", info.lot_size)) or info.lot_size
        info.min_volume = int(opt(s, "minVolume", info.min_volume))
        info.step_volume = int(opt(s, "stepVolume", info.step_volume)) or info.step_volume
        info.max_volume = int(opt(s, "maxVolume", info.max_volume))
        info.detailed = True


def build_unrealized_req(ctid: int):
    r = sdk().msgs.ProtoOAGetPositionUnrealizedPnLReq()
    r.ctidTraderAccountId = ctid
    return r


def parse_unrealized(res: Any) -> dict[int, float]:
    digits = int(opt(res, "moneyDigits", 2))
    return {int(p.positionId): int(p.netUnrealizedPnL) / (10 ** digits) for p in res.positionUnrealizedPnL}
