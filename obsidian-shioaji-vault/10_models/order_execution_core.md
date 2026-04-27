# order_execution_core

> 我不是要你把筆記完善 我是要你根據筆記 把交易模型做出來

## 目標
- 把人類意圖轉成安全、可追蹤、可重跑的 Shioaji 委託

## 核心原則
- 預設先 preview，再 submit
- live 下單前一定要確認帳號 `signed=True`
- live 下單前一定要啟用 CA
- 所有委託都要留下可回查的執行紀錄

## 最小委託欄位
- stock_id
- action
- order_lot
- quantity
- price
- account
- custom_field

## 目前支援重點
- 盤中零股
- 限價單
- 單筆手動測試單
- 後續可擴充模型批次單
