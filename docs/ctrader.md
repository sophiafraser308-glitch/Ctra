# cTrader integration

Official SDK `ctrader-open-api` (Twisted) running on the asyncio loop through `twisted.internet.asyncioreactor`, installed first in `app/__main__.py`.

* `CTraderConnectionManager` – one per environment host (`demo.ctraderapi.com`, `live.ctraderapi.com`, port 5035, TLS). Application auth, 10 s heartbeat, request/response correlation via SDK `clientMsgId`, per-request timeouts, supervisor that forces reconnect with exponential backoff when stuck, state callbacks.
* `CTraderGateway` – facade: account auth (token refreshed once on auth error), symbol/asset cache, trader snapshot, `ProtoOAReconcileReq`, unrealized P/L, order/deal history, new/cancel/amend/close, spot subscriptions, trendbars, event dispatch (execution, disconnect, token invalidation, margin call).
* Market orders use **relative** SL/TP (cTrader rejects absolute SL/TP on market orders); pending orders use absolute prices. Every order carries a deterministic `clientOrderId` for later matching.
* Volumes: `protocol_volume = lots × symbol.lotSize`; rounded down to `stepVolume`, checked against min/max.
* Prices are `raw / 100000`; pip size = `10^-pipPosition`.

> Not runtime-verified here (no network/SDK in the build sandbox): protobuf field usage follows the published Open API model; verify against a DEMO account first. See `troubleshooting.md`.
