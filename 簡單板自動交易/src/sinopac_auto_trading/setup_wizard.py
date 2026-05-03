from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .paths import PROJECT_ROOT


SINOPAC_ENV_KEYS = {
    "api_key": "SINOPAC_API_KEY",
    "secret_key": "SINOPAC_SECRET_KEY",
    "person_id": "SINOPAC_PERSON_ID",
    "ca_path": "SINOPAC_CA_PATH",
    "ca_password": "SINOPAC_CA_PASSWORD",
}


OFFICIAL_SETUP_SUMMARY = """永豐 / Shioaji 程式交易前置條件

1. 需要先擁有永豐金證券帳戶。
2. 到永豐 API 管理頁面申請 API Key / Secret Key。
3. API Key 權限至少要包含交易模型需要的項目；若未來要真實下單，Key 也要允許正式環境。
4. 下載 CA 憑證，準備憑證檔路徑與憑證密碼。
5. 新用戶需先簽署 API 相關文件，並在模擬環境完成 login 與 place_order 測試報告。
6. 官方文件提到測試報告服務時間為週一至週五 08:00-20:00，18:00-20:00 僅允許台灣 IP。
7. 這套簡化版預設只跑 simulation_only，不會送出真實下單。

官方文件：
- https://sinotrade.github.io/zh/tutor/prepare/token/
- https://sinotrade.github.io/zh/tutor/prepare/terms/
- https://sinotrade.github.io/zh/tutor/login/
"""


@dataclass(slots=True)
class SetupStatus:
    env_path: Path
    complete: bool
    missing_keys: list[str]


def read_env_file(path: Path | None = None) -> dict[str, str]:
    env_path = path or PROJECT_ROOT / ".env"
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def setup_status(path: Path | None = None) -> SetupStatus:
    env_path = path or PROJECT_ROOT / ".env"
    values = read_env_file(env_path)
    missing = [
        key
        for key in SINOPAC_ENV_KEYS.values()
        if not values.get(key) or values.get(key, "").startswith("<")
    ]
    return SetupStatus(env_path=env_path, complete=not missing, missing_keys=missing)


def write_sinopac_env(values: dict[str, str], path: Path | None = None) -> Path:
    env_path = path or PROJECT_ROOT / ".env"
    existing = read_env_file(env_path)
    updates = {
        SINOPAC_ENV_KEYS["api_key"]: values.get("api_key", "").strip(),
        SINOPAC_ENV_KEYS["secret_key"]: values.get("secret_key", "").strip(),
        SINOPAC_ENV_KEYS["person_id"]: values.get("person_id", "").strip(),
        SINOPAC_ENV_KEYS["ca_path"]: values.get("ca_path", "").strip(),
        SINOPAC_ENV_KEYS["ca_password"]: values.get("ca_password", "").strip(),
        "SINOPAC_DEFAULT_SIMULATION": "1",
        "SINOPAC_ALLOW_LIVE_SUBMIT": existing.get("SINOPAC_ALLOW_LIVE_SUBMIT", "0") or "0",
        "SINOPAC_DEFAULT_ORDER_LOT": existing.get("SINOPAC_DEFAULT_ORDER_LOT", "IntradayOdd") or "IntradayOdd",
    }
    existing.update(updates)

    ordered_keys = [
        SINOPAC_ENV_KEYS["api_key"],
        SINOPAC_ENV_KEYS["secret_key"],
        SINOPAC_ENV_KEYS["person_id"],
        SINOPAC_ENV_KEYS["ca_path"],
        SINOPAC_ENV_KEYS["ca_password"],
        "SINOPAC_DEFAULT_SIMULATION",
        "SINOPAC_ALLOW_LIVE_SUBMIT",
        "SINOPAC_DEFAULT_ORDER_LOT",
    ]
    lines = ["# SinoPac / Shioaji credentials. Keep this file private."]
    for key in ordered_keys:
        lines.append(f"{key}={existing.get(key, '')}")
    for key in sorted(existing):
        if key not in ordered_keys:
            lines.append(f"{key}={existing[key]}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return env_path
