from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Iterable

from ..selection_provider import SelectionItem, SelectionProvider


class AbLlmPreselectJsonSelectionProvider(SelectionProvider):
    def __init__(
        self,
        *,
        preselect_dir: str | Path,
        daily_output_dir: str | Path | None = None,
        use_a_preselect_as_final_list: bool = True,
    ) -> None:
        self.preselect_dir = Path(preselect_dir)
        self.daily_output_dir = Path(daily_output_dir) if daily_output_dir else None
        self.use_a_preselect_as_final_list = use_a_preselect_as_final_list

    def _load_json(self, path: Path) -> dict[str, object]:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _as_float(value: object) -> float | None:
        if value in (None, ""):
            return None
        if isinstance(value, str) and value.strip().upper() in {"NA", "N/A", "NONE", "NULL"}:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _as_bool(value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "a", "ab"}

    @staticmethod
    def _as_date(value: object) -> date | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None

    @classmethod
    def _payload_trade_date(cls, payload: dict[str, object], fallback_path: Path) -> date | None:
        for key in ("trade_date", "source_run_date"):
            resolved = cls._as_date(payload.get(key))
            if resolved is not None:
                return resolved
        return cls._as_date(fallback_path.stem)

    @classmethod
    def _payload_targets_trade_date(cls, payload: dict[str, object], trade_date: date) -> bool:
        for key in ("target_trade_date", "source_target_trade_date"):
            resolved = cls._as_date(payload.get(key))
            if resolved == trade_date:
                return True
        return False

    @classmethod
    def _payload_has_target_trade_date(cls, payload: dict[str, object]) -> bool:
        return any(cls._as_date(payload.get(key)) is not None for key in ("target_trade_date", "source_target_trade_date"))

    @classmethod
    def _payload_is_usable_for_trade_date(cls, payload: dict[str, object], trade_date: date, fallback_path: Path) -> bool:
        if cls._payload_targets_trade_date(payload, trade_date):
            return True
        if cls._payload_has_target_trade_date(payload):
            return False
        return cls._payload_trade_date(payload, fallback_path) == trade_date

    def _candidate_json_paths(self, root: Path) -> Iterable[Path]:
        if not root.exists():
            return []
        dated = sorted(path for path in root.glob("*.json") if path.name.lower() != "latest.json")
        latest = root / "latest.json"
        return [*dated, latest] if latest.exists() else dated

    def _resolve_source_path(self, trade_date: date) -> tuple[date, Path] | None:
        exact_path = self.preselect_dir / f"{trade_date.isoformat()}.json"
        if exact_path.exists():
            payload = self._load_json(exact_path)
            if self._payload_is_usable_for_trade_date(payload, trade_date, exact_path):
                return self._payload_trade_date(payload, exact_path) or trade_date, exact_path
        candidates: list[tuple[float, date, Path]] = []
        for path in self._candidate_json_paths(self.preselect_dir):
            try:
                payload = self._load_json(path)
            except (OSError, json.JSONDecodeError):
                continue
            if not self._payload_targets_trade_date(payload, trade_date):
                continue
            source_date = self._payload_trade_date(payload, path) or trade_date
            try:
                modified_at = path.stat().st_mtime
            except OSError:
                modified_at = 0.0
            candidates.append((modified_at, source_date, path))
        if candidates:
            dated_candidates = [item for item in candidates if item[2].name.lower() != "latest.json"]
            _modified_at, source_date, path = max(
                dated_candidates or candidates,
                key=lambda item: (item[0], item[2].name),
            )
            return source_date, path
        return None

    def _resolve_daily_output_path(self, source_date: date, target_date: date) -> Path | None:
        if not self.daily_output_dir:
            return None
        for candidate_date in (source_date, target_date):
            exact_path = self.daily_output_dir / f"{candidate_date.isoformat()}.json"
            if exact_path.exists():
                return exact_path
        candidates: list[tuple[float, Path]] = []
        for path in self._candidate_json_paths(self.daily_output_dir):
            try:
                payload = self._load_json(path)
            except (OSError, json.JSONDecodeError):
                continue
            if not self._payload_targets_trade_date(payload, target_date):
                continue
            try:
                modified_at = path.stat().st_mtime
            except OSError:
                modified_at = 0.0
            candidates.append((modified_at, path))
        if candidates:
            dated_candidates = [item for item in candidates if item[1].name.lower() != "latest.json"]
            return max(dated_candidates or candidates, key=lambda item: (item[0], item[1].name))[1]
        return None

    def _daily_output_rows_by_stock(self, source_date: date, target_date: date) -> tuple[Path | None, dict[str, dict[str, object]]]:
        path = self._resolve_daily_output_path(source_date, target_date)
        if path is None:
            return None, {}
        payload = self._load_json(path)
        rows = payload.get("rows")
        if not isinstance(rows, list):
            return path, {}
        result: dict[str, dict[str, object]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            stock_id = str(row.get("stock_id", "")).strip()
            if stock_id:
                result[stock_id] = row
        return path, result

    def _reference_price(
        self,
        row: dict[str, object],
        raw_item: dict[str, object],
        candidate_item: dict[str, object],
    ) -> float | None:
        row_price = (
            self._as_float(row.get("close_price"))
            or self._as_float(row.get("open_price"))
            or self._as_float(row.get("week_entry_price"))
        )
        if row_price is not None:
            return row_price
        raw_signals = raw_item.get("signals")
        if isinstance(raw_signals, dict):
            signal_price = (
                self._as_float(raw_signals.get("close"))
                or self._as_float(raw_signals.get("last_price"))
                or self._as_float(raw_signals.get("reference_price"))
            )
            if signal_price is not None:
                return signal_price
        candidate_signals = candidate_item.get("signals")
        if isinstance(candidate_signals, dict):
            signal_price = (
                self._as_float(candidate_signals.get("close"))
                or self._as_float(candidate_signals.get("last_price"))
                or self._as_float(candidate_signals.get("reference_price"))
            )
            if signal_price is not None:
                return signal_price
        return self._as_float(raw_item.get("reference_price"))

    def _model_score(
        self,
        row: dict[str, object],
        raw_item: dict[str, object],
        candidate_item: dict[str, object],
    ) -> float | None:
        for source in (raw_item.get("scores"), row.get("scores"), candidate_item.get("scores")):
            if not isinstance(source, dict):
                continue
            score = self._as_float(source.get("FinalScore")) or self._as_float(source.get("final_score"))
            if score is not None:
                return score
        return None

    def _selection_tag_has_b(self, row: dict[str, object]) -> bool:
        selection_tag = str(row.get("selection_tag") or "").strip().upper()
        return self._as_bool(row.get("b_flag")) or selection_tag == "AB"

    def source_path_for_trade_date(self, trade_date: date) -> Path | None:
        resolved = self._resolve_source_path(trade_date)
        return resolved[1] if resolved else None

    def _candidate_pool_rows_by_stock(self, payload: dict[str, object]) -> dict[str, dict[str, object]]:
        candidate_pool = payload.get("candidate_pool")
        candidates = candidate_pool.get("candidates") if isinstance(candidate_pool, dict) else None
        if not isinstance(candidates, list):
            return {}
        result: dict[str, dict[str, object]] = {}
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            stock_id = str(candidate.get("stock_id") or "").strip()
            if stock_id:
                result[stock_id] = candidate
        return result

    def _load_a_preselect_items(self, trade_date: date) -> list[SelectionItem]:
        resolved = self._resolve_source_path(trade_date)
        if resolved is None:
            return []
        source_date, source_path = resolved
        payload = self._load_json(source_path)
        raw_a_preselect = payload.get("a_preselect")
        if not isinstance(raw_a_preselect, list):
            raise RuntimeError(f"AB LLM preselect JSON missing a_preselect list: {source_path}")

        target_date = self._as_date(payload.get("target_trade_date")) or self._as_date(
            payload.get("source_target_trade_date")
        ) or trade_date
        daily_output_path, rows_by_stock = self._daily_output_rows_by_stock(source_date, target_date)
        pool_rows_by_stock = self._candidate_pool_rows_by_stock(payload)
        preselect_source = str(payload.get("source") or payload.get("preselect_source") or "").strip()
        candidate_pool_file = str(payload.get("candidate_pool_file") or "").strip()
        items: list[SelectionItem] = []
        for index, raw_item in enumerate(raw_a_preselect, start=1):
            if not isinstance(raw_item, dict):
                continue
            stock_id = str(raw_item.get("stock_id", "")).strip()
            if not stock_id:
                continue
            row = rows_by_stock.get(stock_id, {})
            pool_row = pool_rows_by_stock.get(stock_id, {})
            stock_name = str(row.get("stock_name") or raw_item.get("stock_name") or stock_id).strip() or stock_id
            a_reason = str(raw_item.get("reason") or row.get("a_reason") or "").strip()
            reference_price = self._reference_price(row, raw_item, pool_row)
            note_parts = [
                f"ab_llm_preselect_source={source_path.name}",
                f"source_trade_date={source_date.isoformat()}",
            ]
            if target_date != source_date or target_date != trade_date:
                note_parts.append(f"target_trade_date={target_date.isoformat()}")
            if daily_output_path is not None:
                note_parts.append(f"ab_daily_output_source={daily_output_path.name}")
            if preselect_source:
                note_parts.append(f"preselect_source={preselect_source}")
            if candidate_pool_file:
                note_parts.append(f"candidate_pool_file={candidate_pool_file}")
            if a_reason:
                note_parts.append(f"a_reason={a_reason}")
            if reference_price is not None:
                note_parts.append(f"reference_price={reference_price}")
            items.append(
                SelectionItem(
                    stock_id=stock_id,
                    stock_name=stock_name,
                    source="A",
                    source_weight=1.0,
                    a_flag=True,
                    b_flag=self._selection_tag_has_b(row),
                    role_level=str(row.get("role_level") or raw_item.get("role_level") or "").strip() or None,
                    theme=str(row.get("theme") or raw_item.get("theme") or "").strip() or None,
                    model_rank=index,
                    model_score=self._model_score(row, raw_item, pool_row),
                    user_priority=index,
                    reference_price=reference_price,
                    note="; ".join(note_parts),
                )
            )
        if not items:
            raise RuntimeError(f"AB LLM preselect JSON contains no usable A items: {source_path}")
        return items

    def _legacy_daily_output_rows_by_stock(self, source_date: date) -> dict[str, dict[str, object]]:
        path, rows = self._daily_output_rows_by_stock(source_date, source_date)
        if path is None:
            return {}
        return rows

    def load_preselect(self, trade_date: date) -> list[SelectionItem]:
        return self._load_a_preselect_items(trade_date)

    def load_final_list(self, trade_date: date) -> list[SelectionItem] | None:
        if not self.use_a_preselect_as_final_list:
            return None
        return self._load_a_preselect_items(trade_date)

    def provider_name(self) -> str:
        return "ab_llm_preselect_json"
