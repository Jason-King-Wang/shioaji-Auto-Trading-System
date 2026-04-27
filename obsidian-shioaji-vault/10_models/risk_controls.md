# risk_controls

## 基本安全閘門
- 沒有 `signed=True` 不做正式送單
- 沒有 CA 不做正式送單
- 沒有明確 stock_id / action / quantity / order_lot / price 不送單
- 自動化任務必須寫明日期條件與 skip 條件
- 目前唯一受 guard 的單股 live automation 內容是 `2330 / Buy / IntradayOdd / 1股 / 09:10 / price cap 2100`
- 除非使用者明確要求，不能新增其他 SinoPac live order automation
- Obsidian / 報表 / dashboard 更新不得改動 live 下單參數或排程
- 整包 live buy 另受 weekly execution、weekly budget、hard budget、`--live --confirm-live`、`AUTO_TRADE_LIVE=1`、A-only、10:00 後 sizing、duplicate guard 控制
- 整包 live buy 修復後不得直接續下；必須先產生 `repair_confirmation` 對帳報告、寄給使用者，再依使用者 mail 回覆內容判斷是否補下尚未送出的剩餘單

## 手動測試原則
- 優先用 1 股盤中零股做流程驗證
- 先驗證「送單成功」再驗證「成交策略」
- 優先避免市價追價
- 若只是整理文件或同步狀態，優先停留在 preview / status / report 類操作，不碰 `--submit`

## 後續可補
- 單日總額限制
- 單股最大風險限制
- 黑名單 / 禁買時段
- 把 mail approval 做成程式本體硬 gate
