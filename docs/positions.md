# Positions

Local `positions` mirror the broker (the broker is authoritative). Upserted from execution events and reconciliation; unique `(account_id, broker_position_id)`.

Traceability chain stored on each row: `signal_id → order_id → position → trade`, plus `bot_id`, `strategy_id`, `strategy_version_id`. Positions not opened by the platform are marked `source=EXTERNAL`.

Telegram actions: list (all/by account), details, set SL, set TP (broker `AmendPositionSLTP`), close, partial close (validated against step/min volume), close all. Closing deals create `trades` (gross, swap, commission, net) and feed the Risk Engine (daily loss, consecutive losses).
