from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time

from .quote_provider import QuoteProvider
from .selection_provider import SelectionItem


@dataclass(slots=True)
class FinalizerDecision:
    item: SelectionItem
    finalizer_score: float
    final_flag: bool
    include_reason: str
    exclude_reason: str = ""


@dataclass(slots=True)
class FinalizeResult:
    trade_date: date
    provider_name: str
    final_items: list[SelectionItem]
    decisions: list[FinalizerDecision]
    used_manual_final_list: bool
    used_provider_final_list: bool
    final_list_origin: str


def _default_final_list_origin(provider_name: str) -> str:
    normalized = str(provider_name).strip()
    if normalized == "manual_csv":
        return "manual_final_list"
    if normalized == "ab_llm_preselect_json":
        return "same_day_a_preselect_final_list"
    return "provider_final_list"


def _score_item(item: SelectionItem, quote_provider: QuoteProvider | None, trade_date: date) -> tuple[float, str]:
    score = item.normalized_source_weight() * 10.0
    reasons: list[str] = [f"source_weight={item.normalized_source_weight():.1f}"]

    if item.model_score is not None:
        score += item.model_score
        reasons.append(f"model_score={item.model_score:.2f}")

    if item.user_priority is not None:
        priority_score = max(0, 100 - item.user_priority)
        score += priority_score
        reasons.append(f"user_priority={item.user_priority}")

    if item.force_include:
        score += 10000
        reasons.append("force_include")

    if quote_provider:
        start = datetime.combine(trade_date, time(9, 0))
        end = datetime.combine(trade_date, time(10, 30))
        history = quote_provider.history_between(item.stock_id, start, end)
        if history:
            first = history[0]
            last = history[-1]
            if first.open_price:
                return_from_open = (last.last_price / first.open_price - 1.0) * 100
                score += return_from_open
                reasons.append(f"return_from_open={return_from_open:.2f}%")
            if last.volume_ratio is not None:
                score += last.volume_ratio
                reasons.append(f"volume_ratio={last.volume_ratio:.2f}")
            if last.vwap_position is not None:
                score += last.vwap_position * 5
                reasons.append(f"vwap_position={last.vwap_position:.2f}")

    return score, ", ".join(reasons)


def finalize_selection(
    trade_date: date,
    preselect_items: list[SelectionItem],
    provider_name: str,
    *,
    manual_final_list: list[SelectionItem] | None = None,
    final_list_origin: str | None = None,
    quote_provider: QuoteProvider | None = None,
    max_names: int | None = None,
) -> FinalizeResult:
    if manual_final_list:
        resolved_final_list_origin = final_list_origin or _default_final_list_origin(provider_name)
        decisions = [
            FinalizerDecision(
                item=item,
                finalizer_score=99999.0,
                final_flag=True,
                include_reason=resolved_final_list_origin,
            )
            for item in manual_final_list
        ]
        return FinalizeResult(
            trade_date=trade_date,
            provider_name=provider_name,
            final_items=list(manual_final_list),
            decisions=decisions,
            used_manual_final_list=resolved_final_list_origin == "manual_final_list",
            used_provider_final_list=resolved_final_list_origin != "manual_final_list",
            final_list_origin=resolved_final_list_origin,
        )

    decisions: list[FinalizerDecision] = []
    for item in preselect_items:
        if item.force_exclude:
            decisions.append(
                FinalizerDecision(
                    item=item,
                    finalizer_score=-1.0,
                    final_flag=False,
                    include_reason="",
                    exclude_reason="force_exclude",
                )
            )
            continue

        score, include_reason = _score_item(item, quote_provider, trade_date)
        decisions.append(
            FinalizerDecision(
                item=item,
                finalizer_score=score,
                final_flag=False,
                include_reason=include_reason,
            )
        )

    ranked = sorted(decisions, key=lambda item: item.finalizer_score, reverse=True)
    selected: list[FinalizerDecision] = []
    for decision in ranked:
        if decision.exclude_reason:
            continue
        decision.final_flag = True
        selected.append(decision)
        if max_names is not None and len(selected) >= max_names:
            break

    return FinalizeResult(
        trade_date=trade_date,
        provider_name=provider_name,
        final_items=[decision.item for decision in selected],
        decisions=ranked,
        used_manual_final_list=False,
        used_provider_final_list=False,
        final_list_origin="finalizer_ranked_selection",
    )
