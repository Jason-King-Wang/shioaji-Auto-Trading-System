# 2026-04-11 自動交易同步

> 同步時間：2026-04-22 01:11:48 Asia/Taipei

## 專案輸出
- project_daily_note: `<AUTO_TRADING_PROJECT>\notes\daily\2026-04-11_auto_trading_daily.md`
- project_weekly_note: `<AUTO_TRADING_PROJECT>\notes\weeks\2026-04-06_2026-04-10_auto_trading_weekly.md`
- report_html: `<AUTO_TRADING_PROJECT>\reports\auto_trading\daily\2026-04-11.html`
- provider: ``
- buy_cutoff_day: ``
- last_trade_day: ``

## 事件
- Skipped allowed live order task: non_trade_day.

## 狀態摘要
- 單檔 live 路徑已可用，適合做受控小額測試。
- 整包 `buy_loop` 這一輪已補上實盤保護與 fill / position / pnl 落地；`sell_loop` 也已改成讀 strategy positions 做評估。
- 上游私有選股模型必須視為 `LLM-assisted` 的選股流程，不是單純匯出規則引擎。

## 今日 Guardrail
- 唯一允許沿用的 SinoPac live order automation：`2330 / Buy / IntradayOdd / 1股 / 09:10 / price cap 2030`。
- 本次同步與文件更新不會建立或修改任何其他 SinoPac live order automation。
- 安全更新範圍以 Obsidian、報表、dashboard、workflow status、LLM selection 文件流為主。
