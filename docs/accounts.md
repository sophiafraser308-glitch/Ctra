# Accounts

* Multiple accounts, each explicitly `DEMO` or `LIVE`. A demo/live mismatch between the selected broker account and the chosen environment is refused.
* Statuses: `DISCONNECTED`, `CONNECTING`, `CONNECTED`, `AUTH_FAILED`, `ERROR`; every transition is stored in `connection_events`.
* Actions (Telegram): connect, disconnect, reconnect, refresh (balance/equity/margin/floating P/L straight from the broker), balance, statistics, positions, orders, risk profile selection/edit, rename, per-account trading permission, remove.
* Remove is refused while bots are active or positions are open; it deletes tokens and soft-deletes the account.
* Equity = balance + net unrealised P/L (`ProtoOAGetPositionUnrealizedPnLReq`); used margin = Σ position `usedMargin`; free margin = equity − used margin.
* Multi-currency accounts are reported per currency on the dashboard (no naive cross-currency sums).
