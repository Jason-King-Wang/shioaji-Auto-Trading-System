# llm_selection_decisions.schema

Location:

- `data/inputs/YYYY-MM-DD/llm_selection_decisions.json`

Top-level fields:

- `trade_date`
- `provider_name`
- `workflow_type`
- `decisions`

Each `decision` row:

- `stock_id`
- `stock_name`
- `source`
- `source_weight`
- `a_flag`
- `b_flag`
- `role_level`
- `theme`
- `model_rank`
- `model_score`
- `user_priority`
- `target_weight`
- `target_qty`
- `selected`
- `llm_reason`
- `note`

Rules:

- only rows with `selected=true` are converted into `auto_trade_final_list.csv`
- `llm_reason` should explain the keep / drop decision at candidate level
- this file must not contain the private AB rule text itself
