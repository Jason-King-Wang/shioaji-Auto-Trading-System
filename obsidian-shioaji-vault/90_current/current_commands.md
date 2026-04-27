# 永豐自動交易 Current Commands

## 先讀
- [[我的箴言語錄]]
- [[使用者操作偏好]]

## 現在的選股來源
- 上游大候選池固定為 `<PRIVATE_SIGNAL_EXPORT>\YYYY-MM-DD.json`
- 固定從 `<WORKSPACE>\data\private_preselect_export\*.json` 找 上游預選來源
- 正式買進來源必須以 `target_trade_date` / `source_target_trade_date` 對準目標交易日
- 檔名等於交易日但 target 不是該交易日的 JSON，不可拿來當天買進籃子
- 只讀 JSON 的 `a_preselect`，也就是 上游預選
- 目前完全不看 `B 預選`
- 如果找不到 target 對準目標交易日的 上游預選，就直接 pass，不回退舊日期名單

## 上游預選確定時間
- 永豐自動交易的確定 上游預選工作固定在目標交易日 `10:00 Asia/Taipei` 之後才做。
- 操作提醒固定句：`10點後才會執行永豐自動交易的確定上游預選工作。`
- 10:00 前可以做 `workflow_status`、`render_report`、live 登入檢查等只讀檢查，但不可把 上游預選物化成正式買進籃子。
- 原因：週一早上 06:00 才開始執行預選名單的產出，9:00 可能還沒產完；我們又可能在開盤前做測試，若太早 finalize，會把週末產出的 上游預選誤當成週一最新正式清單。

## 日常同步
```powershell
python run.py workflow_status --trade-date 2026-04-27
python run.py render_report --trade-date 2026-04-27
python run.py refresh_dashboard --trade-date 2026-04-27
python run.py sync_obsidian --trade-date 2026-04-27
```

## 上游預選主線
10:00 後才執行：

```powershell
python run.py prepare_week --trade-date 2026-04-27
python run.py finalize --trade-date 2026-04-27
python run.py buy_loop --trade-date 2026-04-27
python run.py sell_loop_readiness --trade-date 2026-04-30 --json
python run.py sell_loop --trade-date 2026-04-30
```

## 整包 live 買進修復確認流程
- 這套流程適用所有未來整包 live 買進，不只限 2026-04-27。
- 若使用者明確授權的整包 live 買進失敗，修復 blocking issue 後不能直接續下；要先跑 `repair_confirmation --live`，查 broker / local 已送、已成交、未送的差異。
- mail 給 `ops@example.com`，內容必須列出「買了什麼 / 已送但未成交 / 什麼還沒買」。
- 使用者不需要固定制式回覆；要依 mail 回覆內容判斷，例如同意補下、不同意補下、只補某幾檔或先停住。若內容不清楚，停止並回報。
- 只有使用者回覆該 mail 且內容明確授權後，且仍是原本授權的交易日 / 第一交易日，才可用同一輪 live gate 補下尚未送出的剩餘單。
- 接著下之前仍要確認 broker / local artifacts 沒有不明委託、active duplicate、ambiguous fill、部分成交矛盾或狀態衝突。
- 防重買優先：若同一 `strategy_lot_id` 在 local orders / positions / week lot ledger / broker fills / broker order id / broker `custom_field` 已有 active 或 filled buy，就不得再送。
- 若已經不是週一 / 第一交易日，或狀態不明，停止並回報，不延伸成週二 / 週三追買。
- 若 `buy_loop --live` 因 live gate、broker 回報 failed / rejected / error、單列被 blocked、或有目標股數尚未完全送出，狀態檔會標記 `repair_confirmation_required`，後續修復入口必須走此 mail 確認流程。

## 上游私有訊號 / LLM 備註
- 上游私有選股訊號來源看 `private_signal_source`
- 上游 Codex 自動化先產出 `private_candidate_pool_export`，再由 上游私有選股訊號產出 `private_preselect_export` JSON
- 本 repo 下游直接吃這份 JSON 的 `上游預選`

## Dry-run / 只檢查不送單
```powershell
python run.py login-check --live --no-fetch-contract
python run.py chase-stock-order --stock-id 2330 --price-cap 2100 --quantity 1 --order-lot IntradayOdd --live
python run.py buy_loop --trade-date 2026-04-27
```

## Live Guardrail
- 唯一受 guard live automation：`2330 / Buy / IntradayOdd / 1股 / 09:10 / 價格上限 2100`
- guarded command: `python run.py run_allowed_live_order`
- read-only reconcile: `python run.py reconcile_broker_state --trade-date 2026-04-27 --live --stock-id 2330`
- post guarded check: `python run.py post_guarded_order_check --trade-date 2026-04-27 --json`
- post guarded reconcile/report: `python run.py post_guarded_order_check --trade-date 2026-04-27 --live --reconcile --sell-loop-readiness --render-report --workflow-status`
- install guarded schedule: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_allowed_2330_live_order_task.ps1 -RunDate 2026-04-27 -AtTime 09:10 -UntilTime 13:20 -RetryIntervalMinutes 5`
- remove: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/remove_allowed_2330_live_order_task.ps1`
- runner: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_allowed_2330_live_order_task.ps1`
- install 會先跑共同 preflight；如果 09:10 已過但 13:20 還沒過，會排最近一次補跑並每 5 分鐘重試；每次送單前都會先查券商委託避免重複下單。
- guard skip 不再算成功：只有實際送出或查到同日同條件既有委託，runner 才回傳 exit code 0。

## 其他需要明確確認的 live 指令
- `python run.py manual-stock-order --stock-id 2330 --price 2100 --quantity 1 --order-lot IntradayOdd --action Buy --live --submit --confirm-live`
- `python run.py chase-stock-order --stock-id 2330 --price-cap 2100 --quantity 1 --order-lot IntradayOdd --live --submit --confirm-live --start-time 09:10`
- `python run.py buy_loop --trade-date 2026-04-27 --live --confirm-live`
- `python run.py sell_loop --trade-date 2026-04-30 --live --confirm-live`
