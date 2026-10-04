# Orders

Only `OrderManager` talks to the broker for order placement.

* **Idempotency** – `orders.request_id` is UNIQUE (`sig-<signal_id>` for bot orders). A repeated submit returns the stored order and never re-sends. `client_order_id` (`TP` + sha256[:30]) is sent to cTrader for later matching.
* **Flow** – `PENDING` (row created) → gates (live/new-trading/permission/connected) → symbol & volume validation → `PROCESSING` **committed before the network call** → single send → `SUBMITTED` → execution events → `ACCEPTED / PARTIALLY_FILLED / FILLED / REJECTED / CANCELLED`.
* **Outcomes** – not-connected before send ⇒ `FAILED` (provably unsent, retry allowed); explicit broker error ⇒ `REJECTED`; timeout or unexpected error after send ⇒ **`UNKNOWN`**.
* **UNKNOWN handling** – never auto-resent. Reconciliation queries order history + open orders by `clientOrderId`: found ⇒ adopt broker state (and rebuild the position); not found ⇒ `FAILED / NOT_FOUND_ON_BROKER` with `retry_allowed`. A retry is a *new* request that passes the Risk Engine again and is audited (`ORDER_RETRY`).
* **Crash safety** – orders stuck in `PROCESSING/PENDING` (process died) are marked `UNKNOWN` by the watchdog and reconciled; DB updates after a send are retried so the outcome is not lost.
* Execution handling is idempotent (monotonic status ranks; trades unique by broker deal id), so the same event arriving twice is harmless.
* Telegram: filters (active, pending, filled, rejected/failed, cancelled, UNKNOWN, recent), details with full trace (signal → risk decision → order → position), reconcile, cancel pending, retry.
