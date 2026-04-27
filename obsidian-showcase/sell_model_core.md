# sell_model_core

## 用途
- 這份檔案用來定義 sell model 的正式核心
- 核心主流程以「每日選股 → 每日預測 → 每日校正」為中心
- 核心負責回答：要預測哪一個目標交易日、該交易日內哪個時段最可能出高點、以及高點大致落在哪裡
- 這份檔案不處理具體掛價、分階段執行、尾盤修正等 execution 細節
- 若與 execution 層有差異，core 仍是母規則；實際執行版本另由 current execution 檔案承接

## 核心目標
- sell model 的核心目標，是預測目標交易日的最高點
- 更精確地說，核心目標是預測：
  - 哪一個交易日是本次應評估的目標交易日
  - 該交易日內哪個高點落點時段最可能出現高點
  - 高點價格大致落在哪裡
  - 從目前位置到高點，理論上還剩多少可期待上行空間
- 最終目的不是寫出掛單流程，而是讓實際賣價盡量貼近該交易日的真實高點
- 收盤價 benchmark 不是核心目標；它只是在最後交易日出清時，可額外拿來做對照的附加 benchmark

## 每日主流程
- 每日主流程固定分成三段：
  1. 每日選股輸入池
  2. 每日高點預測輸出
  3. 每日收盤校正回寫
- sell model core 的正式每日輸入池，不是任意輸入池，也不是持股 + 額外觀察股的混合池
- 正式定義是：
  - 用 AB 選股模型邏輯，每天挑 30 支股
  - sell model core 只對這 30 支股做預測與校正
- 這樣 sell model 才是貼著原本 AB 模型運作，而不是只對一批隨機名單變準
- 一旦每日 30 檔確定，core 就必須對每檔輸出固定 prediction 欄位，並在收盤後進入固定校正

## 目標交易日定義
- sell model core 先回答的第一個問題，不是怎麼賣，而是「要預測哪一個目標交易日」
- 目標交易日（target trading session）的定義：
  - 若目前仍在開盤中的交易日，目標是當前交易日
  - 若目前不在開盤中的交易日，目標是下一個交易日
- 這個定義與高點落點時段是兩層概念，不可混用
- core 必須先決定 target trading session，後續才評估該交易日內的高點結構

## 高點落點時段定義
- 在 core 中，高點落點時段（peak time bucket）只回答「目標交易日內，高點大致落在哪個 bucket」
- 目前先固定為四個 bucket：
  - 開盤前段
  - 上午
  - 午後
  - 尾盤
- 這個 bucket 是高點預測輸出的一部分，不等於目標交易日本身
- 後續若要再細分 bucket，應先從校正結果證明有必要，再升級

## 核心輸入
- sell model core 的輸入應拆成三層，不應只綁死某一種選股流程

### 市場層
- 大盤 / 指數結構
- 市場強弱與風險環境
- 國際市場、headline、事件風險
- 主線是否同步
- 是否屬一般環境、偏空環境、或特殊事件壓力環境

### 個股層
- 個股近期價格結構
- 相對強弱
- 量能與趨勢完整度
- 主題 / 族群位置
- 催化是否存在、是否擴散、是否已鈍化
- 歷史高點落點特徵與波動結構

### 上下文層
- 是否在 AB 邏輯選出的每日 30 檔內
- 是否屬 A / B / AB overlap
- 角色層級（核心 / 次核心 / 觀察）
- 是否屬最後交易日或特定出清情境
- 是否屬某種已知策略上下文
- 這些只能作為 context feature，不能變成 core 必須綁定 A / B 才能運作

## 核心輸出
- 為了和 repo 既有 schema / daily / 資料層一致，core 輸出優先採 `pred_*` 命名
- 核心輸出至少固定為：
  - `pred_peak_price`
  - `pred_peak_time_bucket`
  - `pred_remaining_upside_from_now`
  - `pred_peak_status`
  - `pred_confidence`
- 可選補充輸出：
  - `pred_peak_range`
- 這些輸出回答的是「高點本身」與「從現在到高點還有多少空間」，不是 execution 指令
- 若要落地成每日固定流程，至少要做到：
  - 每一檔每日都有一致格式的 prediction row
  - 每日收盤後可直接對照 actual outcome 做校正
  - prediction row 可被 daily note、model run note、資料層共同引用
  - 每日 prediction 的預測對象固定來自 AB 邏輯選出的正式 30 檔

## 核心 KPI
- 核心 KPI 應圍繞 prediction quality，而不是掛單流程是否方便
- 至少包括：
  - 預測高點與真實高點的差距
  - 預測高點時段與真實高點時段的一致性
  - 實際賣價距離真實高點的差距
  - 命中高點區的比例
  - 不同分組下的預測誤差與命中率
- 這裡的高點區命中率，先保留為底層評估概念，不把固定 `±1% / ±2%` 寫成 core baseline

## 評估與校正原則
- 每日收盤後都必須固定進入校正，不應只在週結算時才回頭看
- 每日校正至少要能回答：
  - 哪些股票的高點預測偏差大
  - 哪些股票的高點時段判斷偏差大
  - 哪些股票雖然方向對，但高點位置預測不夠準
  - 哪些 context feature 與預測成功 / 失敗相關
- 初期固定採「每日 50 點校正方案」
- 原因不是因為 50 點本身神奇，而是初期模型一定還不夠準，需要更密、更細、可回看的校正註記
- 現階段先不急著把校正縮成少量指標，而是先保留足夠多的註記，讓後續能回修 core
- core 評估時，必須把 prediction 與 execution 分開看
- 要區分：
  - 模型其實有預測到合理高點
  - execution 沒有在合理區域賣到
- 也要區分：
  - 當天其實存在可賣高點
  - 當天根本沒有形成足夠可賣高點
- 不能把「execution 沒咬到」與「模型根本沒預測到」混成同一種失敗
- `±1% / ±2%`、穩定股 / 高波動股分流、尾盤主動修正等，屬於 calibration / benchmark 或 execution 評估層，不寫進 core baseline

## 每日校正固定欄位與口徑
- 每日校正至少應固定保留：
  - `pred_peak_price`
  - `pred_peak_time_bucket`
  - `pred_remaining_upside_from_now`
  - `pred_peak_status`
  - `pred_confidence`
  - `actual_high`
  - `actual_high_time`（若可得）
  - `actual_close`
  - `pred_peak_error_abs`
  - `pred_peak_time_bucket_hit`
  - `review_tag`
  - `review_note`
- 每日校正的核心口徑是：
  - 預測高點與真實高點差多少
  - 預測高點 bucket 是否大致正確
  - 該 prediction 是否足夠支持後續 execution
- 若 execution 尚未介入，core 校正仍然成立；也就是 daily calibration 不應綁死 execution version
- 現階段真正重要的，不只是最後算出幾個命中率，而是每天留下的校正註記
- 這些註記之後可作為 core 升級依據
- 這和 AB 模型過去的優化方式相同：不是只看數字，而是看哪些註記反覆出現、哪些情境最容易失真

## 與 execution layer 的邊界
- core 只負責：
  - 接收 AB 邏輯選出的每日 30 檔輸入池
  - 判定目標交易日
  - 預測高點價格
  - 預測高點落點時段
  - 給出剩餘上行空間與預測信心
  - 定義每日校正欄位與 prediction quality 評估方法
- core 不負責：
  - 第一階段 / 第二階段掛價
  - 批次決策
  - 尾盤主動修正
  - 一次都賣 / 一次都留
  - 問「怎麼賣」時要用哪個 current 流程回應

## 與 AB 週線策略的關係
- AB 週線策略與 sell model 是上下游關係，不是同一層模型
- AB 邏輯在這裡不是可有可無的附帶資訊，而是每日正式輸入池的來源
- 每日 30 檔應由 AB 選股模型邏輯選出
- sell model core 的任務不是重新選股，而是針對這個每日 30 檔池，預測目標交易日高點
- A / B 身份、角色層級、是否 overlap，可作為 context feature，但不能把 sell model core 綁死成只有 AB 才能運作

## 暫不處理項目
- 第一階段 / 第二階段掛價細節
- 週五最後交易日出清流程
- 13:00 後主動現價 / 修正出清規則
- 一次都賣 / 一次都留的 execution policy
- `±1% / ±2%` 的正式 baseline 化
- API 自動下單與 fully auto execution
- 更細的盤中微結構 / tick 級 execution optimizer
