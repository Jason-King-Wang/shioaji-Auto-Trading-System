# 永豐自動交易 Current Status

> 更新時間：2026-04-27 10:09:42 Asia/Taipei

## 先讀筆記
- [[我的箴言語錄]]
- [[使用者操作偏好]]
- [[永豐自動交易_專案說明書]]
- [[自動交易核心施工藍圖]]

## 判讀原則
- 新增的使用者偏好筆記以 [[我的箴言語錄]] 與 [[使用者操作偏好]] 為主。
- 舊筆記保留歷史脈絡，但若與新偏好衝突，應以新偏好為準。
- 真實下單仍必須保留既有 guardrail，不等於可以跳過檢查。

## 系統現況
- trade_date: `2026-04-27`
- provider: `private_preselect_export_json`
- candidate_pool_source: `<PRIVATE_SIGNAL_EXPORT>\YYYY-MM-DD.json`
- selection_source: `<WORKSPACE>\data\private_preselect_export\*.json`
- selection_rule: `target_trade_date matches trade_date / A-only / no B / missing usable target => pass`
- a_preselect_confirmation_start: `目標交易日 10:00 Asia/Taipei`
- a_preselect_confirmation_reminder: `10點後才會執行永豐自動交易的確定上游預選工作。`
- a_preselect_confirmation_reason: `週一 06:00 才開始產出預選名單，09:00 可能尚未完成；開盤前測試若太早 finalize，可能把週末產出的檔案誤鎖成週一最新正式清單。`
- live_buy_repair_confirmation_policy: `所有未來整包 live 買進只要修復後想續下，都必須先產生 repair_confirmation 對帳報告並寄信給使用者；使用者不需要固定制式回覆，依 mail 內容判斷；只有回覆內容明確授權、仍是授權交易日、broker/local 狀態明確，才可補下未送出的剩餘單。`
- duplicate_buy_guard_policy: `防重買優先於續下；同一 strategy_lot_id 若在 local orders / positions / week lot ledger / broker fills / broker order id / broker custom_field 已有 active 或 filled buy，不得再送同一筆。`
- run_status: `workflow_status_rendered`
- buy_cutoff_day: `2026-04-28`
- last_trade_day: `2026-04-30`
- calendar_missing_warning: `False`
- latest_report: `<AUTO_TRADING_PROJECT>\reports\auto_trading\daily\2026-04-27.html`
- latest_daily_note: `<AUTO_TRADING_PROJECT>\notes\daily\2026-04-27_auto_trading_daily.md`
- latest_event: render_report generated

## Live Guardrail
- 目前唯一允許的受 guard live automation：`2330 / Buy / IntradayOdd / 1股 / 09:10 / 價格上限 2100`
- 單股受 guard live 任務可真實送單，但整包 buy_loop / sell_loop 仍有各自 live 條件。
- 受 guard 排程必須先通過共同 preflight；09:10 已過但 13:20 未過時，應補跑並每 5 分鐘重試。
- 每次真實送單前都要查券商既有委託；找到同日同條件委託就略過，避免重複下單。
- 若使用者新偏好與舊文件或系統預設衝突，先指出衝突，再由使用者決定。

## Live 狀態
- 本次同步未額外讀取券商 live 狀態。
- 若要附帶券商帳號與 CA 狀態，請改用 `sync_obsidian --include-live-status`。

## 路徑
- project_root: `<AUTO_TRADING_PROJECT>`
- report_current_html: `<AUTO_TRADING_PROJECT>\reports\auto_trading\current.html`
- obsidian_vault_root: `<OBSIDIAN_ROOT>\shioaji Auto Trading System`
