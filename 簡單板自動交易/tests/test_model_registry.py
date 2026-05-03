from __future__ import annotations

import json
import shutil
import unittest
import uuid
from pathlib import Path

from sinopac_auto_trading.model_registry import discover_installed_models, find_model_by_code


class ModelRegistryTests(unittest.TestCase):
    def _case_dir(self, name: str) -> Path:
        path = Path(__file__).resolve().parent / "_tmp" / f"{name}-{uuid.uuid4().hex}"
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def test_discovers_model_manifest_by_code(self) -> None:
        base = self._case_dir("models")
        model_dir = base / "models" / "alpha"
        model_dir.mkdir(parents=True)
        (model_dir / "model.json").write_text(
            json.dumps({"code": "ALPHA", "name": "Alpha", "order_file": "orders.json"}),
            encoding="utf-8",
        )
        (model_dir / "orders.json").write_text('{"orders":[]}', encoding="utf-8")

        models = discover_installed_models(base)
        found = find_model_by_code("alpha", base)

        self.assertEqual(len(models), 1)
        self.assertEqual(found.code, "alpha")
        self.assertEqual(found.order_file, model_dir / "orders.json")


if __name__ == "__main__":
    unittest.main()
