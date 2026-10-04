# Telegram interface

Library: aiogram 3 (long polling). Only **private chats** from authorised numeric IDs are served; others receive "not authorized" and an audit event (rate-limited).

Main menu: Dashboard · Accounts · Trading Bots · Strategies · Positions · Orders · Risk · Connections · System Health · Logs & Audit · Notifications · Settings · Emergency.

* Inline keyboards everywhere; lists are paginated; `callback_data` uses `section:action:arg` (≤ 64 bytes, ids are 12 hex chars).
* **Confirmations**: every destructive action uses `confirm_gate` — one confirmation, and a *second* red confirmation when the target is a LIVE account.
* **Idempotency**: state-changing buttons run through `CommandService` keyed by the Telegram callback id; a redelivered update executes once.
* **FSM** (aiogram `MemoryStorage`): add account, rename, strategy upload/params, bot wizard & edits, risk edits, SL/TP/partial close, user management. FSM state is *not* durable; durable state is in the DB. `/cancel` aborts any flow.
* **RBAC** per handler via `need(role, Permission)`; see `security.md`.
* Errors are mapped to short user messages; unexpected errors show a reference id and are logged with traceback.
