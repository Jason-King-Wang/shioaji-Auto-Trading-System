# auto_trade_preselect.csv

Required columns:

- `stock_id`
- `stock_name`
- `source`

Optional columns:

- `user_priority`
- `force_include`
- `force_exclude`
- `note`
- `source_weight`
- `a_flag`
- `b_flag`
- `role_level`
- `theme`
- `catalyst_flag`
- `model_rank`
- `model_score`
- `target_weight`
- `target_qty`

Notes:

- `source` may be `A`, `B`, `A+B`, `manual`, `model`, `unknown`, or any custom label.
- `target_qty` overrides framework sizing when present.
