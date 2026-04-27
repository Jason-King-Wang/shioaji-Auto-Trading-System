# Selection Provider Contract

`SelectionProvider` is the public boundary between this framework and any upstream stock-selection logic.

Required methods:

- `load_preselect(trade_date) -> list[SelectionItem]`
- `load_final_list(trade_date) -> list[SelectionItem] | None`
- `provider_name() -> str`

Selection items must expose normalized fields such as:

- `stock_id`
- `stock_name`
- `source`
- `source_weight`
- `a_flag`
- `b_flag`
- `model_rank`
- `model_score`
- `user_priority`
- `target_qty`
- `note`

Provider priority:

1. manual final list
2. manual preselect
3. StockModelVault export provider
4. generic CSV / JSON
5. static or mock providers
