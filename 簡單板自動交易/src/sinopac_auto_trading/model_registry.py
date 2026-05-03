from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .paths import PROJECT_ROOT


@dataclass(slots=True)
class InstalledModel:
    code: str
    name: str
    description: str
    order_file: Path
    root: Path


def discover_installed_models(project_root: Path | None = None) -> list[InstalledModel]:
    root = project_root or PROJECT_ROOT
    models_root = root / "models"
    if not models_root.exists():
        return []
    models: list[InstalledModel] = []
    for manifest_path in sorted(models_root.glob("*/model.json")):
        model = _load_model_manifest(manifest_path)
        if model is not None:
            models.append(model)
    return models


def find_model_by_code(code: str, project_root: Path | None = None) -> InstalledModel:
    normalized = normalize_model_code(code)
    for model in discover_installed_models(project_root):
        if normalize_model_code(model.code) == normalized:
            return model
    raise ValueError(f"model code not installed: {code}")


def normalize_model_code(raw: str) -> str:
    return "".join(char.lower() for char in str(raw or "").strip() if char.isalnum() or char in {"_", "-"})


def _load_model_manifest(path: Path) -> InstalledModel | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    code = normalize_model_code(str(payload.get("code", path.parent.name)))
    if not code:
        return None
    order_file = Path(str(payload.get("order_file", "orders.json") or "orders.json"))
    if not order_file.is_absolute():
        order_file = path.parent / order_file
    return InstalledModel(
        code=code,
        name=str(payload.get("name", code) or code),
        description=str(payload.get("description", "") or ""),
        order_file=order_file,
        root=path.parent,
    )
