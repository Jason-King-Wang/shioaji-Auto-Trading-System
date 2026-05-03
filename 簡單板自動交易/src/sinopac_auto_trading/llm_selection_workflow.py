from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .basket import normalize_basket_tag
from .paths import (
    input_dir_for,
    llm_selection_brief_path,
    llm_selection_decisions_path,
    llm_selection_payload_path,
    llm_selection_template_path,
)
from .selection_provider import SelectionItem
from .time_utils import TAIPEI


@dataclass(slots=True)
class LLMReviewCandidate:
    stock_id: str
    stock_name: str
    source: str
    basket_tag: str
    source_weight: float
    a_flag: bool | None
    b_flag: bool | None
    role_level: str | None
    theme: str | None
    model_rank: int | None
    model_score: float | None
    user_priority: int | None
    target_weight: float | None
    target_qty: int | None
    reference_price: float | None
    note: str
    provider_name: str
    preselect_flag: bool
    manual_final_flag: bool


def _decision_key(item: SelectionItem) -> str:
    return f"{item.stock_id}::{item.normalized_basket_tag()}"


def _candidate_from_item(item: SelectionItem, *, provider_name: str, manual_final_keys: set[str]) -> LLMReviewCandidate:
    return LLMReviewCandidate(
        stock_id=item.stock_id,
        stock_name=item.stock_name,
        source=item.source,
        basket_tag=item.normalized_basket_tag(),
        source_weight=item.normalized_source_weight(),
        a_flag=item.a_flag,
        b_flag=item.b_flag,
        role_level=item.role_level,
        theme=item.theme,
        model_rank=item.model_rank,
        model_score=item.model_score,
        user_priority=item.user_priority,
        target_weight=item.target_weight,
        target_qty=item.target_qty,
        reference_price=item.reference_price,
        note=item.note,
        provider_name=provider_name,
        preselect_flag=True,
        manual_final_flag=_decision_key(item) in manual_final_keys,
    )


def build_llm_review_payload(
    *,
    trade_date: date,
    provider_name: str,
    preselect_items: list[SelectionItem],
    manual_final_list: list[SelectionItem] | None = None,
) -> dict[str, Any]:
    manual_final_keys = {_decision_key(item) for item in manual_final_list or []}
    candidates = [_candidate_from_item(item, provider_name=provider_name, manual_final_keys=manual_final_keys) for item in preselect_items]
    return {
        "trade_date": trade_date.isoformat(),
        "generated_at": datetime.now(TAIPEI).isoformat(timespec="seconds"),
        "provider_name": provider_name,
        "workflow_type": "llm_assisted_selection",
        "instructions": [
            "Use this payload as the review boundary for AB + LLM stock selection.",
            "Before making keep/drop decisions, read the latest notes/Obsidian description of the AB stock-selection method for this trade date.",
            "Treat the note-defined AB method as the source of truth because the AB selection method can change over time.",
            "Do not copy private AB rule text into the public repo or reports.",
            "Decisions should reflect Codex / GPT judgment on top of the candidate list, not a static CSV-only process.",
            "Write final decisions to llm_selection_decisions.json once the review is complete.",
        ],
        "candidates": [asdict(candidate) for candidate in candidates],
    }


def _decision_template_entry(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "stock_id": candidate["stock_id"],
        "stock_name": candidate["stock_name"],
        "source": candidate["source"],
        "basket_tag": candidate["basket_tag"],
        "source_weight": candidate["source_weight"],
        "a_flag": candidate["a_flag"],
        "b_flag": candidate["b_flag"],
        "role_level": candidate["role_level"],
        "theme": candidate["theme"],
        "model_rank": candidate["model_rank"],
        "model_score": candidate["model_score"],
        "user_priority": candidate["user_priority"],
        "target_weight": candidate["target_weight"],
        "target_qty": candidate["target_qty"],
        "reference_price": candidate["reference_price"],
        "selected": bool(candidate["manual_final_flag"]),
        "llm_reason": "manual_final_list" if candidate["manual_final_flag"] else "",
        "note": candidate["note"],
    }


def _brief_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# {payload['trade_date']} LLM Selection Brief",
        "",
        "## Purpose",
        "- This file tracks the AB + LLM review boundary for the current trade date.",
        "- The final selection must come from Codex / GPT judgment on top of candidate inputs.",
        "- Before reviewing candidates, read the latest note/Obsidian description of the current AB selection method.",
        "- The AB selection method is mutable; do not assume the repo contains a permanent frozen copy.",
        "- Do not paste private AB rule text here; only record candidate-level reasoning.",
        "",
        "## Generated From",
        f"- provider_name: `{payload['provider_name']}`",
        f"- generated_at: `{payload['generated_at']}`",
        "",
        "## Output Files",
        f"- payload_json: `{llm_selection_payload_path(date.fromisoformat(payload['trade_date']))}`",
        f"- decisions_template: `{llm_selection_template_path(date.fromisoformat(payload['trade_date']))}`",
        f"- decisions_final: `{llm_selection_decisions_path(date.fromisoformat(payload['trade_date']))}`",
        "",
        "## Candidate Summary",
        f"- candidate_count: `{len(payload['candidates'])}`",
        "",
        "## Review Checklist",
        "- Read the latest note/Obsidian description of the current AB selection method first.",
        "- Confirm whether each name should survive to final_list.",
        "- Record `selected=true/false` for each reviewed row.",
        "- Add a short `llm_reason` explaining why the name stays or is removed.",
        "- Keep the process as LLM-assisted stock selection, not a static export replay.",
        "",
        "## Candidates",
        "",
        "| stock_id | stock_name | source | basket_tag | weight | manual_final_flag | note |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for candidate in payload["candidates"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(candidate.get("stock_id", "")),
                    str(candidate.get("stock_name", "")),
                    str(candidate.get("source", "")),
                    str(candidate.get("basket_tag", "")),
                    str(candidate.get("source_weight", "")),
                    str(candidate.get("manual_final_flag", "")),
                    str(candidate.get("note", "")).replace("\n", " "),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def write_llm_review_bundle(
    *,
    trade_date: date,
    provider_name: str,
    preselect_items: list[SelectionItem],
    manual_final_list: list[SelectionItem] | None = None,
) -> dict[str, Path]:
    input_dir = input_dir_for(trade_date)
    input_dir.mkdir(parents=True, exist_ok=True)

    payload = build_llm_review_payload(
        trade_date=trade_date,
        provider_name=provider_name,
        preselect_items=preselect_items,
        manual_final_list=manual_final_list,
    )
    payload_path = llm_selection_payload_path(trade_date)
    payload_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    template = {
        "trade_date": trade_date.isoformat(),
        "provider_name": provider_name,
        "workflow_type": "llm_assisted_selection_decisions",
        "decisions": [_decision_template_entry(candidate) for candidate in payload["candidates"]],
    }
    template_path = llm_selection_template_path(trade_date)
    template_path.write_text(json.dumps(template, indent=2, ensure_ascii=False), encoding="utf-8")

    brief_path = llm_selection_brief_path(trade_date)
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    brief_path.write_text(_brief_markdown(payload), encoding="utf-8")

    written = {
        "payload_path": payload_path,
        "template_path": template_path,
        "brief_path": brief_path,
    }
    decisions_path = llm_selection_decisions_path(trade_date)
    if manual_final_list and not decisions_path.exists():
        decisions_path.write_text(json.dumps(template, indent=2, ensure_ascii=False), encoding="utf-8")
        written["decisions_path"] = decisions_path

    return written


def load_llm_decision_items(path: Path) -> list[SelectionItem]:
    if not path.exists():
        raise FileNotFoundError(f"LLM decision file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    raw_decisions = data.get("decisions", [])
    if not isinstance(raw_decisions, list):
        raise RuntimeError("llm_selection_decisions.json must contain a `decisions` list.")

    items: list[SelectionItem] = []
    for decision in raw_decisions:
        if not isinstance(decision, dict):
            continue
        if not bool(decision.get("selected", False)):
            continue
        stock_id = str(decision.get("stock_id", "")).strip()
        if not stock_id:
            continue
        items.append(
            SelectionItem(
                stock_id=stock_id,
                stock_name=str(decision.get("stock_name", stock_id)).strip() or stock_id,
                source=str(decision.get("source", "unknown")).strip() or "unknown",
                basket_tag=normalize_basket_tag(decision.get("basket_tag", "main")),
                source_weight=float(decision["source_weight"]) if decision.get("source_weight") not in (None, "") else None,
                a_flag=bool(decision["a_flag"]) if decision.get("a_flag") is not None else None,
                b_flag=bool(decision["b_flag"]) if decision.get("b_flag") is not None else None,
                role_level=str(decision.get("role_level", "")).strip() or None,
                theme=str(decision.get("theme", "")).strip() or None,
                model_rank=int(decision["model_rank"]) if decision.get("model_rank") not in (None, "") else None,
                model_score=float(decision["model_score"]) if decision.get("model_score") not in (None, "") else None,
                user_priority=int(decision["user_priority"]) if decision.get("user_priority") not in (None, "") else None,
                target_weight=float(decision["target_weight"]) if decision.get("target_weight") not in (None, "") else None,
                target_qty=int(decision["target_qty"]) if decision.get("target_qty") not in (None, "") else None,
                reference_price=float(decision["reference_price"]) if decision.get("reference_price") not in (None, "") else None,
                note=str(decision.get("llm_reason") or decision.get("note") or "").strip(),
            )
        )
    return items


def write_final_list_csv(path: Path, items: list[SelectionItem]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "stock_id",
        "stock_name",
        "source",
        "basket_tag",
        "source_weight",
        "a_flag",
        "b_flag",
        "role_level",
        "theme",
        "catalyst_flag",
        "model_rank",
        "model_score",
        "user_priority",
        "force_include",
        "force_exclude",
        "target_weight",
        "target_qty",
        "reference_price",
        "note",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in items:
            writer.writerow(asdict(item))
