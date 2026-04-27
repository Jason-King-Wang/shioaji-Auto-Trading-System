# sell_execution_layers

## 用途
- 這份檔案用來整理 sell model 的 execution layers
- execution layer 的任務，是把 sell model core 的高點預測，轉成可執行的賣出流程
- execution layer 不是 core；它是落地層、操作層與版本化優化層

## execution layer 的總原則
- execution 不能改寫 core 的目標
- core 回答的是：哪一個目標交易日、哪個 bucket、預估高點在哪裡
- execution 回答的是：實際上怎麼掛、怎麼調、怎麼分批、什麼時候人工修正
- execution 可以依情境切成不同版本，但要和 core 清楚分層

## manual execution

### 用途
- 用在人工判讀與手動掛單的情境
- 適合目前日常操作與對話式執行流程

### 適用情境
- 使用者直接問「怎麼賣」
- 需要人工根據當天盤勢、承接、主線同步性做動態判斷
- 需要保留人工 override 的彈性

### 與 core 的關係
- core 提供的是高點預測
- manual execution 再把這個預測轉成實際掛價與賣出動作
- 手動執行不應反過來重新定義 core 目標

### 暫不定的細節
- 第一階段 / 第二階段具體價差模板
- 不同波動分類是否採完全不同掛價規則
- 人工 override 的固定觸發條件

## weekly exit execution

### 用途
- 用在每週最後一個交易日的出清與收尾流程
- 是 current sell model 目前最接近的 execution 版本

### 適用情境
- 週五或該週最後交易日收盤前賣出
- 需要把 sell model 與週結算、尾盤修正、批次決策一起考慮時

### 與 core 的關係
- core 仍只負責高點預測
- weekly exit execution 則負責：
  - 如何映射成當前賣價流程
  - 若未成交後怎麼處理
  - 收盤前怎麼收尾
- 這一層可以使用收盤價 benchmark、尾盤修正分類、批次處理，但這些都不是 core

### 暫不定的細節
- 第一 / 第二階段的正式長期模板
- 一次都賣 / 一次都留的固定啟動規則
- 13:00 後主動修正要不要再拆更多子類別

## auto execution

### 用途
- 保留給未來的半自動 / 自動執行版本
- 目前先定義邊界，不定義完整實作

### 適用情境
- 之後若接 API、自動掛單、或預測到 execution 的自動映射

### 與 core 的關係
- auto execution 只能拿 core output 做映射
- 不能自行取代 core 的高點預測邏輯
- 若未來有風控 / fallback / human override，也應屬 auto execution 層

### 暫不定的細節
- API 接口
- 自動掛單規則
- human override 流程
- 風控 guardrail
- 失敗回退機制

## 備註
- 目前 repo 中最接近 weekly exit execution 的生效版本，是 `90_current/current_sell_model.md`
- 若未來 execution 再細分版本，建議保留在長版 execution 層或週來源檔，不直接混進 core
