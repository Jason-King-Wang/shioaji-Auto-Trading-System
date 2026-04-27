# README_trading_pipeline

<!-- WRITE_LOG_START -->
## 寫入 / 更新紀錄
- 寫入：2026-04-20 Asia/Taipei
<!-- WRITE_LOG_END -->

## 目的
- 這個 vault 用來管理 `Shioaji` 自動交易系統
- 結構刻意模仿 `private_model_vault`，讓日常查看、回顧、補寫與自動化維護更順手
- 這裡專注在「券商 API 下單 / 風控 / 委託回報 / 交易日誌」，不跟選股模型主 vault 混在一起

## 核心路徑
- `90_current`：當前可執行版本、帳號狀態、命令與待辦
- `10_models`：下單規則、風控規則、委託流程設計
- `20_weeks`：週檢討、問題追蹤、流程修正紀錄
- `30_daily`：每日下單、成交、異常與回顧
- `40_data`：schema、原始回報、手動核對資料
- `50_trade_runs`：每次測試 / 正式下單的執行紀錄
- `99_templates`：每日與每週模板

## 目前狀態
- 已完成永豐 API Key / Secret Key / CA 憑證設定
- 股票帳號 API 目前已通過正式簽署流程
- 已可使用 `Shioaji` 正式登入與啟用 CA
- 已安排測試與開盤自動掛單流程

## 與既有專案的關係
- 模型與研究主體仍在 `private_model_vault`
- 真正送單程式目前在：
  - `<AUTO_TRADING_PROJECT>`
- 這個 vault 負責：
  - 交易流程文檔
  - API 操作紀錄
  - 每日執行與檢查筆記
  - 後續自動交易系統的長期維護

## 常用指令
```powershell
python run.py login-check --live --no-fetch-contract
python run.py api-test-stock
python run.py manual-stock-order --stock-id 2330 --price 2025 --quantity 1 --order-lot IntradayOdd --action Buy --live --submit
```
