# auto_trade_final_list.csv

Required columns:

- `stock_id`
- `stock_name`
- `source`

Optional columns:

- `source_weight`
- `target_weight`
- `target_qty`
- `note`
- `a_flag`
- `b_flag`
- `role_level`
- `theme`
- `model_rank`
- `model_score`

Notes:

- If `target_qty` is provided, it has the highest sizing priority.
- Manual final lists override framework finalizer output.
