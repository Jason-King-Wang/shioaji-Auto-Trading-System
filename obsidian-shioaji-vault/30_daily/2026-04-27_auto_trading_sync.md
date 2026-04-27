# 2026-04-27 自動交易同步

> 更新時間：2026-04-27 10:09:42 Asia/Taipei

## 先讀筆記
- [[我的箴言語錄]]
- [[使用者操作偏好]]
- [[永豐自動交易_專案說明書]]
- [[自動交易核心施工藍圖]]

## 今日摘要
- run_status: `workflow_status_rendered`
- provider: `private_preselect_export_json`
- buy_cutoff_day: `2026-04-28`
- last_trade_day: `2026-04-30`
- project_daily_note: `<AUTO_TRADING_PROJECT>\notes\daily\2026-04-27_auto_trading_daily.md`
- project_weekly_note: `<AUTO_TRADING_PROJECT>\notes\weeks\2026-04-27_2026-04-30_auto_trading_weekly.md`
- report_html: `<AUTO_TRADING_PROJECT>\reports\auto_trading\daily\2026-04-27.html`
- latest_event: render_report generated

## 今日判讀原則
- 以 [[我的箴言語錄]] 與 [[使用者操作偏好]] 為主。
- 舊筆記保留記錄用途，但若有落差，以更新後的偏好與現況為準。
- 目前唯一受 guard 的 live automation：`2330 / Buy / IntradayOdd / 1股 / 09:10 / 價格上限 2100`

## Live 狀態
- 本次同步未額外讀取券商 live 狀態。
- 若要附帶券商帳號與 CA 狀態，請改用 `sync_obsidian --include-live-status`。
