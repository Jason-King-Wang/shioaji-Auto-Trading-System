# Safety For Live Trading

Live trading is blocked unless all confirmations pass.

Required live gates:

1. config enables live mode
2. config enables this week's execution with `weekly_execution_enabled: true`
3. config sets this week's approved budget with `weekly_budget > 0`
4. CLI explicitly requests live mode
5. explicit live confirmation is provided for direct/manual commands
6. environment variable `AUTO_TRADE_LIVE=1`
7. broker account validation succeeds

Additional protections:

- dry-run is the default
- invalid ticks must be normalized
- prices cannot cross limit-up / limit-down boundaries
- stale quotes pause the affected symbol
- last trade day never opens new buys

Live automation guardrail:

- the only allowed SinoPac live order automation at the moment is `2330 / Buy / IntradayOdd / 1股 / 09:10 / price cap 2100`
- unless the user explicitly asks, do not add any other SinoPac live order automation
- note/report/dashboard/Obsidian updates must not change that morning live order's parameters or schedule
- the guarded automation entrypoint is `python run.py run_allowed_live_order`
- every Windows task installer that registers an order schedule must call `scripts/assert_live_order_schedule_preflight.ps1` before `Register-ScheduledTask`
- order schedule installers must not expose skip flags for runner smoke test or live guard preflight
- order schedule installers must reject a task only when the catch-up window is closed; a missed start time inside the same authorized trading window must still be eligible to run
- order schedules must retry inside the authorized trading window instead of relying on a single one-shot trigger
- order schedule preflight auto-enables `config/auto_trading.yaml` `live_enabled: true` only after `SINOPAC_ALLOW_LIVE_SUBMIT=1` and `AUTO_TRADE_LIVE=1` are in force for the guarded live schedule
- order schedule preflight must not auto-enable `weekly_execution_enabled` or set `weekly_budget`; those require the user's weekly command
- the weekly command is `python run.py approve_week --trade-date YYYY-MM-DD --weekly-budget AMOUNT --execute`; use `--disable` to close the gate again
- after changing the weekly budget, re-run `finalize`; live `buy_loop` blocks if its sizing snapshot was created under a different weekly budget
- A preselect confirmation/materialization for SinoPac auto trading starts only after `10:00` Asia/Taipei on the target trade date; remind the operator: `10點後才會執行永豐自動交易的確定A預選工作。`
- live `buy_loop` blocks when the source `sizing.csv` was not finalized after the 10:00 A-preselect confirmation time
- if any explicitly authorized live basket buy fails before completion, repair the blocking issue, then run `repair_confirmation --live` before any continuation; email the report to the user and continue only after the user replies to that email approving the not-yet-submitted remainder
- the repair confirmation report/email must list bought/filled orders, sent-but-not-fully-filled orders, and not-yet-submitted orders; if broker/local state is ambiguous, an order may already exist, part of the basket filled without safe mapping, the date is no longer the first trading day, or any live gate/budget/list changed, stop instead of continuing
- the user's reply does not need to match a fixed phrase; interpret the email content, continue only when it clearly authorizes the requested not-yet-submitted remainder, and stop when the reply is unclear, denies continuation, narrows the scope, or conflicts with the report
- this repair confirmation gate is the default for future live basket buy repairs; `buy_loop --live` records `repair_confirmation_required` when a live gate blocks the run, broker submission reports failed/rejected/error, a row is blocked, or a requested row is not fully submitted
- duplicate-buy prevention has priority over retry/repair: before placing or replacing a live buy, the system must reconcile the strategy lot id against local `orders.csv`, local positions/lot ledger, broker fills, broker order id state, and broker `custom_field`; a matching active or filled broker buy must be treated as existing exposure and must not be submitted again
- basket buy retry after the first trading day is disabled while `auto_trading.allow_buy_after_first_trade_day: false`; Monday/first-day misses are not chased on Tuesday or Wednesday
- if basket buy retry is explicitly enabled later, retry is chase-only: `buy_loop` reads the first trading day's sizing and strategy lot ids, subtracts already bought quantities, and only submits the unfilled remainder, not the new AB/A output from the retry day
- order schedule runs must return exit code `0` only when an order is submitted or an existing matching order is found; guard skips must return non-zero so Windows Task Scheduler cannot show a false success
- every retry must query broker trades first and skip submission when a matching same-day order already exists
- direct/manual live submit commands such as `manual-stock-order` and `chase-stock-order` now also require `--confirm-live`
- the Windows task install/remove scripts are `scripts/install_allowed_2330_live_order_task.ps1` and `scripts/remove_allowed_2330_live_order_task.ps1`
- read-only broker reconciliation is available through `python run.py reconcile_broker_state --trade-date YYYY-MM-DD --live --stock-id 2330`; it queries broker fills/positions and writes local ledgers, but must not place or cancel orders
- post-run guarded order checks are available through `python run.py post_guarded_order_check --trade-date YYYY-MM-DD`; this is status-only by default
- `post_guarded_order_check --reconcile` must also include `--live`, and still must not place or cancel orders
- `post_guarded_order_check --sell-loop-readiness --render-report --workflow-status` writes handoff artifacts before refreshing reports/workflow
- post-run checks write `post_guarded_order_check.json`, giving reports/workflow status a durable handoff artifact before sell-loop decisions depend on fills/positions
- sell-loop readiness checks are available through `python run.py sell_loop_readiness --trade-date YYYY-MM-DD --json`; they only inspect local artifacts and write `sell_loop_readiness.json`
- sell preparation is available through `python run.py sell_loop --trade-date YYYY-MM-DD --prepare-only --live --confirm-live`; this may query broker quotes/fills/positions and write `positions.csv`, `excluded_positions.csv`, `broker_position_mismatches.csv`, and `sell_decisions.csv`, but it must not place sell orders
- actual sell submission still requires a separate `python run.py sell_loop --trade-date YYYY-MM-DD --live --confirm-live` run, all live gates, a fresh quote, the sell window, and the basket auto-exit guard

Sell policy summary:

- The live exit decision is basket-first: when the whole basket exits, every strategy position in that basket is prepared for sell submission.
- Basket conservative profit is computed after conservative exit-price discounting, sell fee, and sell tax.
- Basket conservative profit must be greater than `max(total_buy_cost * 0.008, 3000)`.
- If basket conservative profit is negative, zero, or below that threshold, the basket recommendation is `hold` and no position is submitted.
- Individual stock sell signals remain in `can_sell_flag` for review, but they no longer block an all-basket exit once the basket threshold passes.
- Loser-loss ratio is kept as an observation field; it is not a standalone sell blocker.
