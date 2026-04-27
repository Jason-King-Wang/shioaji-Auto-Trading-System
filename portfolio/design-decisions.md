# Design Decisions

This project is designed around one bias: in live trading, a false positive is worse than a missed trade.

The system should be able to pass, block, or ask for repair confirmation without pretending everything is fine.

## 1. Private Signal Boundary

The execution layer consumes standardized outputs from a private upstream signal process.

It does not include the private rule text, candidate-pool logic, or model internals. This keeps the repo interview-safe while still showing the real downstream architecture.

## 2. 10:00 Materialization

The system waits until `10:00 Asia/Taipei` on the target trade date before materializing the upstream preselect into the official buy basket.

Reason:

- Early morning source files may still be incomplete.
- Pre-market testing can accidentally see an older file.
- Once finalized, sizing and order intents become live-relevant artifacts.

So finalization is not just "file exists"; it must also pass target-date and timing checks.

## 3. Budget Snapshot Before Live Buy

Live buy depends on a finalized `sizing.csv`.

That sizing snapshot must match the approved week, weekly budget, hard budget, and materialization time. If the user changes the budget after sizing, the system blocks live buy until `finalize` is rerun.

This avoids a quiet mismatch between what the operator approved and what the order loop is about to submit.

## 4. Strategy Lot IDs

The system treats each planned position as a strategy lot.

Before placing or replacing a live buy, it checks whether the same strategy lot already appears in:

- local orders
- local positions
- week lot ledger
- broker fills
- broker order IDs
- broker custom fields

If a matching active or filled buy exists, the system treats that as existing exposure and refuses to submit a duplicate.

## 5. Repair Confirmation Instead Of Blind Rerun

Partial live failure is normal in real systems.

The dangerous response is to "fix and rerun" without knowing what already happened.

This repo uses `repair_confirmation` to produce a broker/local reconciliation report first. Continuation is allowed only for the not-yet-submitted remainder, and only when the state is clear.

## 6. Exit Is Execution Policy

This repository keeps sell-side execution as an order-management policy:

- strategy positions only
- excluded broker inventory stays out of scope
- conservative exit price for live order safety
- basket-level threshold
- live gate and quote freshness before submission

Other independent model projects stay out of scope.

## 7. Obsidian As Operating Memory

Obsidian notes are used as operating memory:

- daily sync notes
- weekly summaries
- current system status
- construction blueprint
- user operation preferences

Old notes are kept as history. If an old note conflicts with newer code or newer current-status notes, the current scripts and artifacts win.

## 8. Tests As Guardrail Documentation

The tests are not only regression checks. They also document what the system refuses to do.

Examples include:

- live order safety
- dry-run behavior
- calendar and cutoff logic
- report rendering cleanliness
- selection provider behavior
- sizing and budget behavior
- workflow status updates
- repair confirmation behavior

That makes the repo reviewable even without real broker credentials.
