from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path

from sinopac_auto_trading.setup_wizard import OFFICIAL_SETUP_SUMMARY, setup_status, write_sinopac_env


class SetupWizardTests(unittest.TestCase):
    def _case_dir(self, name: str) -> Path:
        path = Path(__file__).resolve().parent / "_tmp" / f"{name}-{uuid.uuid4().hex}"
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def test_write_sinopac_env_creates_required_private_settings(self) -> None:
        env_path = self._case_dir("env") / ".env"

        write_sinopac_env(
            {
                "api_key": "key",
                "secret_key": "secret",
                "person_id": "A123456789",
                "ca_path": "C:/Sinopac.pfx",
                "ca_password": "capass",
            },
            env_path,
        )

        content = env_path.read_text(encoding="utf-8")
        self.assertIn("SINOPAC_API_KEY=key", content)
        self.assertIn("SINOPAC_SECRET_KEY=secret", content)
        self.assertIn("SINOPAC_PERSON_ID=A123456789", content)
        self.assertIn("SINOPAC_DEFAULT_SIMULATION=1", content)
        self.assertIn("SINOPAC_ALLOW_LIVE_SUBMIT=0", content)
        self.assertTrue(setup_status(env_path).complete)

    def test_setup_summary_mentions_official_requirements(self) -> None:
        self.assertIn("API Key", OFFICIAL_SETUP_SUMMARY)
        self.assertIn("CA 憑證", OFFICIAL_SETUP_SUMMARY)
        self.assertIn("模擬環境完成 login 與 place_order", OFFICIAL_SETUP_SUMMARY)


if __name__ == "__main__":
    unittest.main()
