# Reconciliation

Triggers: startup (before bots start), reconnect, UNKNOWN order, periodic (`RECONCILE_INTERVAL_SECONDS`), critical-error burst, watchdog (overdue), manual (System Health).

| Kind | Meaning | Action |
|---|---|---|
| ORPHAN_POSITION | broker has a position we do not know | adopt as `EXTERNAL`, warn |
| MISSING_POSITION | broker position matches a recent FILLED order but local row is missing | rebuild with full attribution |
| PHANTOM_POSITION | local OPEN, not at broker | look up closing deals (7 d); record trade(s) or mark CLOSED with unknown P/L |
| POSITION_MISMATCH | volume/SL/TP differ | overwrite local with broker values, record both |
| UNEXPECTED_ORDER | broker pending order unknown locally | record as external order |
| MISSING_ORDER | local active order not at broker | `resolve_unknown` via order history |

Every divergence writes a `reconciliation_events` row (local state, broker state, action) and warns admins (throttled).
