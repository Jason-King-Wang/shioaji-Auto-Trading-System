# Portfolio Guide

這個資料夾是面試導覽版，不是私有交易策略全文。

它的目的，是讓讀者快速看懂這個專案不是單純的 API wrapper，而是一套從「上游訊號輸入」到「券商下單、回寫、對帳、報表、修復」的自動交易執行系統。

## 建議閱讀順序

1. [project-history.md](project-history.md)
   看專案怎麼從 dry-run、單股 live guard，逐步演進成整包交易框架。

2. [engineering-blueprint.md](engineering-blueprint.md)
   看系統架構、資料流、模組切分，以及每一層在保護什麼。

3. [design-decisions.md](design-decisions.md)
   看幾個關鍵工程決策：10:00 materialization、budget snapshot、duplicate-buy guard、repair confirmation。

4. [interview-talking-points.md](interview-talking-points.md)
   面試時可以怎麼講，如何把這個 repo 連成一個完整工程故事。

## Public Boundary

這個 repo 展示的是執行層：

- 如何讀取已標準化的上游訊號輸出。
- 如何把訊號轉成 sizing、order intent、broker action。
- 如何在 live trading 前建立多層 guardrail。
- 如何把 broker/local artifacts 回寫成報表與 Obsidian 紀錄。
- 如何在 partial failure 後避免重複下單。

刻意不展示：

- 私有選股模型規則。
- 真實 API key、CA 憑證、帳號資訊。
- 原始 live trading database、完整真實成交紀錄。
- 其他獨立模型專案。
