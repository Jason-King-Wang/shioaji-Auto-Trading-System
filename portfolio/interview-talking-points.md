# Interview Talking Points

這份可以當成面試時的講稿骨架。

## 30-Second Version

這是一套 Shioaji / SinoPac 自動交易執行框架。

我把私有選股模型和公開執行層分開：repo 裡不放選股規則，只展示下游如何把標準化訊號轉成 sizing、order intent、broker submission、fills、positions、PnL、reports 和 Obsidian operating notes。

重點不是只會送單，而是能在 live trading 前做多層 guard、在失敗後對帳修復、在週期內保留可追溯的狀態。

## What I Built

- A provider boundary that consumes standardized upstream signal exports.
- A finalization step that prevents stale files from becoming live baskets.
- A sizing layer with weekly budget and hard-budget checks.
- A broker adapter pattern that separates dry-run from live Shioaji behavior.
- Buy and sell execution loops with time windows, quote checks, state checks, and live confirmation.
- Local artifacts and ledgers for orders, fills, positions, excluded positions, and PnL.
- Daily and weekly reports plus Obsidian sync for operator visibility.
- Repair confirmation flow to avoid unsafe reruns after partial live failures.

## Engineering Problems It Solves

| Problem | System answer |
| --- | --- |
| The source file exists but may be stale | Use target-date and 10:00 materialization gates. |
| Budget changed after sizing | Block live buy until sizing is regenerated. |
| A live run partially failed | Generate repair confirmation before any continuation. |
| Broker and local state may disagree | Reconcile broker/local orders, fills, positions, and strategy lots. |
| A rerun might duplicate a buy | Check strategy lot IDs across local and broker state. |
| Dashboard may be stale | Treat scripts and artifacts as source of truth. |

## Code Review Path

If the reviewer has limited time, point them here:

- `auto-trading/src/sinopac_auto_trading/selection_provider.py`
- `auto-trading/src/sinopac_auto_trading/finalizer.py`
- `auto-trading/src/sinopac_auto_trading/sizing.py`
- `auto-trading/src/sinopac_auto_trading/broker_adapter.py`
- `auto-trading/src/sinopac_auto_trading/order_engine.py`
- `auto-trading/src/sinopac_auto_trading/repair_confirmation.py`
- `auto-trading/src/sinopac_auto_trading/report_writer.py`
- `auto-trading/tests`

## What I Would Improve Next

- Move email approval after repair confirmation into a first-class application gate.
- Add a stricter replay harness for broker/local mismatch scenarios.
- Add more compact dashboard views for operator handoff.
- Add simulated market sessions for quote freshness and partial-fill edge cases.
- Formalize artifact versioning so weekly reports can show exactly which config and provider snapshot produced each run.
