# Architecture

## Positioning

`SinoPac Auto Trading` is a downstream execution framework.

It accepts standardized selections from any provider and turns them into:

- finalized watchlists
- sized order intents
- dry-run or live broker actions
- daily markdown / HTML reports
- weekly settlement notes

## Layers

1. `SelectionProvider`
   Reads preselect / final list data from manual CSV, generic CSV, mock data, or optional private exports.
2. `Finalizer`
   Produces the 10:30 final list without embedding private AB rules.
3. `Sizing`
   Applies source weights, quantity overrides, budget and hard-budget checks.
4. `Order Engine`
   Manages buy/sell states, reprice thresholds, partial fills and cutoff logic.
5. `BrokerAdapter`
   Places or simulates orders.
6. `State Store`
   Writes SQLite tables and JSON / CSV artifacts.
7. `Report Writer`
   Renders daily and weekly outputs.

## Safety

- default mode is dry-run
- live mode requires explicit config + CLI + environment confirmation
- last trade day never buys
- positions are tracked per strategy lot, not by total broker account inventory
