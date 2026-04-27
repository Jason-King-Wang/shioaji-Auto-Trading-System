# Project History

這份歷史不是流水帳，而是保留系統從「可以跑」到「可以安全解釋與修復」的工程脈絡。

## 核心演進

一開始的目標很小：讓 Shioaji / SinoPac API 能被穩定呼叫，並且確認股票帳號已經具備 live submission 條件。

後來問題很快變成更實際的交易系統問題：

- 如果上游訊號還沒確定，系統不能太早把舊檔案鎖成今日正式買進名單。
- 如果 live buy 中途失敗，不能只是修好 bug 後直接重跑，因為可能已經有部分委託或成交。
- 如果本地紀錄和券商狀態不同步，系統必須先對帳，而不是相信 dashboard。
- 如果同一個 strategy lot 已經有 active 或 filled buy，就不能再補送同一筆。

所以這個專案逐步從「下單腳本」變成「有時間軸、有狀態、有 guardrail、有回寫、有修復流程」的執行框架。

## Timeline

| 時間 | 階段 | 代表性成果 |
| --- | --- | --- |
| 2026-04-06 ~ 2026-04-10 | 早期週紀錄 | 建立 weekly note 的概念，開始把交易系統輸出同步到 Obsidian。 |
| 2026-04-20 | 專案啟動 | 明確切出 `SinoPac Auto Trading` 是獨立下游執行框架，不是上游模型本體。補上 docs、config example、schemas、examples、tests。 |
| 2026-04-20 | Live API 驗證 | 確認股票 API login、CA activation、signed 狀態；建立受 guard 的單股 live automation 驗證路徑。 |
| 2026-04-22 | 歷史與偏好整理 | 確立「舊筆記保留歷史，新偏好與實際腳本/產物優先」的判讀原則。 |
| 2026-04-24 ~ 2026-04-25 | Provider 與報表成形 | 把 selection source 改成標準化上游 export provider，建立 daily / weekly report、workflow status、Obsidian sync。 |
| 2026-04-27 | Materialization 與 repair policy | 固定 10:00 後才允許確認當日上游預選；加入 repair confirmation、duplicate-buy guard、budget snapshot 與 sell prepare-only 流程。 |

## What Changed Architecturally

早期版本比較像「拿到一份 CSV 就試著做 order planning」。

後來拆成幾個更穩定的邊界：

- `SelectionProvider` 只負責讀取標準化清單，不知道私有模型規則。
- `Finalizer` 負責把可變的上游輸出鎖成當日 final list。
- `Sizing` 負責預算、股數、hard budget 與週期授權。
- `Order Engine` 負責 order intent、追價、partial fill 與 cutoff。
- `BrokerAdapter` 把 dry-run 與 live broker 隔開。
- `State Store` 與 CSV/JSON artifacts 讓每一步可查、可回放。
- `Report Writer` 與 Obsidian sync 讓操作者看到「現在到底發生什麼」。

這些拆分讓系統可以接受不同上游來源，但不需要公開任何私有選股規則。

## Why The History Matters

交易系統真正困難的地方，不是只送出一筆 order。

更困難的是：

- 送之前知道自己為什麼可以送。
- 送之後知道 broker 和 local state 是否一致。
- 半途失敗時知道哪些已成交、哪些已送、哪些還沒送。
- 修復後不重複買、不越權補單、不吃錯日期的來源。

這份 repo 保留歷史，是為了呈現這些工程問題如何被逐步發現、約束、寫進程式與筆記。
