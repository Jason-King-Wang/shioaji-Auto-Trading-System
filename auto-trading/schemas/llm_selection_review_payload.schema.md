# llm_selection_review_payload.schema

Location:

- `data/inputs/YYYY-MM-DD/llm_selection_review_payload.json`

Top-level fields:

- `trade_date`
- `generated_at`
- `provider_name`
- `workflow_type`
- `instructions`
- `candidates`

Each `candidate` row:

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
- `note`
- `provider_name`
- `preselect_flag`
- `manual_final_flag`

Rules:

- this file is the LLM review input bundle
- it may include candidate-level metadata
- it must not include private AB rule text
