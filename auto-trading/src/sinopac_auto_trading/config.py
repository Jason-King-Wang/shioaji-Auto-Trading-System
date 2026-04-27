from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from .paths import CONFIG_DIR, PROJECT_ROOT

DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover
    def load_dotenv(*_args, **_kwargs) -> bool:
        return False


load_dotenv(DEFAULT_ENV_PATH, override=False)

LIVE_SUBMIT_GUARD_MESSAGES: dict[str, str] = {
    "allow_live_submit_disabled": "Live submit is blocked. Set SINOPAC_ALLOW_LIVE_SUBMIT=1 in .env after review.",
    "config_live_disabled": "Live submit is blocked because auto_trading.live_enabled is false in config.",
    "weekly_execution_disabled": "Live submit is blocked because auto_trading.weekly_execution_enabled is false. Enable it only after the user's weekly command.",
    "weekly_budget_missing": "Live submit is blocked because auto_trading.weekly_budget must be greater than 0 for the approved week.",
    "weekly_execution_week_mismatch": "Live submit is blocked because auto_trading.weekly_execution_week_id does not match the trade date week.",
    "sizing_budget_snapshot_missing": "Live submit is blocked because sizing.csv was created before weekly budget metadata was recorded. Re-run finalize after approving the week.",
    "sizing_budget_mismatch": "Live submit is blocked because current weekly budget config differs from the sizing snapshot. Re-run finalize.",
    "sizing_week_mismatch": "Live submit is blocked because the sizing snapshot week does not match the requested trade date.",
    "confirm_live_missing": "Live submit is blocked. Re-run with --confirm-live after review.",
    "auto_trade_live_env_missing": "Live submit is blocked. Set AUTO_TRADE_LIVE=1 for the reviewed live session.",
    "live_confirmed": "Live submit guard passed.",
}


def _as_bool(raw: str | None, default: bool = False) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _as_config_bool(raw: object, default: bool = False) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return raw != 0
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def describe_live_submit_guard(reason: str) -> str:
    return LIVE_SUBMIT_GUARD_MESSAGES.get(reason, f"Live submit is blocked: {reason}.")


def weekly_execution_week_id_for(trade_date: date) -> str:
    iso_year, iso_week, _weekday = trade_date.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _load_layered_yaml(config_dir: Path, base_name: str) -> dict[str, Any]:
    example = _load_yaml(config_dir / f"{base_name}.example.yaml")
    actual = _load_yaml(config_dir / f"{base_name}.yaml")
    return _merge(example, actual)


def ensure_auto_trading_live_enabled(config_dir: Path | None = None) -> tuple[bool, Path]:
    resolved_config_dir = config_dir or CONFIG_DIR
    path = resolved_config_dir / "auto_trading.yaml"
    data = _load_yaml(path)
    if data.get("live_enabled") is True:
        return False, path

    path.parent.mkdir(parents=True, exist_ok=True)
    data["live_enabled"] = True
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return True, path


def set_auto_trading_weekly_execution(
    *,
    weekly_budget: float,
    weekly_execution_enabled: bool,
    weekly_execution_week_id: str,
    config_dir: Path | None = None,
) -> Path:
    if weekly_budget < 0:
        raise ValueError("weekly_budget must be greater than or equal to 0.")
    resolved_config_dir = config_dir or CONFIG_DIR
    path = resolved_config_dir / "auto_trading.yaml"
    data = _load_yaml(path)
    data["weekly_budget"] = float(weekly_budget)
    data["weekly_execution_enabled"] = bool(weekly_execution_enabled)
    data["weekly_execution_week_id"] = str(weekly_execution_week_id or "").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def _merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _default_obsidian_vault_root(project_root: Path) -> Path | None:
    candidate = project_root.parents[1] / "Obsidian Vault" / "shioaji Auto Trading System"
    return candidate if candidate.exists() else None


@dataclass(slots=True)
class FeeConfig:
    commission_rate: float = 0.001425
    commission_discount: float = 1.0
    minimum_commission: float = 20.0
    transaction_tax_rate_stock: float = 0.003
    transaction_tax_rate_day_trade: float = 0.0015
    other_fee_rules: dict[str, Any] = field(default_factory=dict)

    def estimate_buy_fee(self, gross_amount: float) -> float:
        return max(gross_amount * self.commission_rate * self.commission_discount, self.minimum_commission)

    def estimate_sell_fee(self, gross_amount: float) -> float:
        return max(gross_amount * self.commission_rate * self.commission_discount, self.minimum_commission)

    def estimate_sell_tax(self, gross_amount: float, *, day_trade: bool = False) -> float:
        rate = self.transaction_tax_rate_day_trade if day_trade else self.transaction_tax_rate_stock
        return gross_amount * rate


@dataclass(slots=True)
class AutoTradingConfig:
    live_enabled: bool = False
    weekly_budget: float = 0.0
    weekly_execution_enabled: bool = False
    weekly_execution_week_id: str = ""
    overrun_tolerance: float = 50000.0
    quote_stale_seconds: int = 15
    test_qty_single_source: int = 1
    test_qty_dual_source: int = 2
    test_max_total_buy_amount: float = 50000.0
    test_max_single_stock_amount: float = 10000.0
    basket_auto_exit_enabled: bool = False
    basket_recommendation_enabled: bool = True
    allow_buy_after_first_trade_day: bool = False
    a_preselect_confirmation_start_time: str = "10:00"
    enable_secondary_add: bool = False
    secondary_add_budget_pct_min: float = 0.30
    secondary_add_budget_pct_max: float = 0.40
    cost_buffer_multiplier: float = 1.015
    per_stock_profit_buffer_pct: float = 0.006
    per_stock_profit_buffer_min_twd: float = 100.0
    basket_profit_buffer_pct: float = 0.008
    basket_profit_buffer_min_twd: float = 3000.0
    max_loser_loss_ratio_to_winner_profit: float = 0.35

    @property
    def hard_budget(self) -> float:
        return self.weekly_budget + self.overrun_tolerance

    def weekly_execution_week_matches(self, trade_date: date | None = None) -> bool:
        configured = str(self.weekly_execution_week_id or "").strip()
        if not configured:
            return True
        resolved_date = trade_date or datetime.now().date()
        return configured == weekly_execution_week_id_for(resolved_date)


@dataclass(slots=True)
class ProviderConfig:
    active: str = "manual_csv"
    definitions: dict[str, dict[str, Any]] = field(default_factory=dict)

    def options(self, provider_name: str | None = None) -> dict[str, Any]:
        resolved_name = provider_name or self.active
        return dict(self.definitions.get(resolved_name, {}))


@dataclass(slots=True)
class Settings:
    api_key: str | None
    secret_key: str | None
    person_id: str | None
    ca_path: Path | None
    ca_password: str | None
    default_simulation: bool
    allow_live_submit: bool
    default_order_lot: str
    budget_per_order: float
    price_buffer_pct: float
    max_orders: int
    auto_trading: AutoTradingConfig = field(default_factory=AutoTradingConfig)
    fees: FeeConfig = field(default_factory=FeeConfig)
    providers: ProviderConfig = field(default_factory=ProviderConfig)
    project_root: Path = PROJECT_ROOT
    obsidian_vault_root: Path | None = None

    @classmethod
    def load(cls, project_root: Path | None = None) -> "Settings":
        root = project_root or PROJECT_ROOT
        config_dir = root / "config"
        auto_yaml = _load_layered_yaml(config_dir, "auto_trading")
        fee_yaml = _load_layered_yaml(config_dir, "fee")
        providers_yaml = _load_layered_yaml(config_dir, "providers")
        providers_section = providers_yaml.get("providers", {})

        ca_path_raw = os.getenv("SINOPAC_CA_PATH")
        obsidian_vault_raw = os.getenv("SINOPAC_OBSIDIAN_VAULT_ROOT")
        return cls(
            api_key=os.getenv("SINOPAC_API_KEY"),
            secret_key=os.getenv("SINOPAC_SECRET_KEY"),
            person_id=os.getenv("SINOPAC_PERSON_ID"),
            ca_path=Path(ca_path_raw) if ca_path_raw else None,
            ca_password=os.getenv("SINOPAC_CA_PASSWORD"),
            default_simulation=_as_bool(os.getenv("SINOPAC_DEFAULT_SIMULATION"), default=True),
            allow_live_submit=_as_bool(os.getenv("SINOPAC_ALLOW_LIVE_SUBMIT"), default=False),
            default_order_lot=os.getenv("SINOPAC_DEFAULT_ORDER_LOT", "IntradayOdd"),
            budget_per_order=float(os.getenv("SINOPAC_BUDGET_PER_ORDER", "100000")),
            price_buffer_pct=float(os.getenv("SINOPAC_PRICE_BUFFER_PCT", "0.3")),
            max_orders=int(os.getenv("SINOPAC_MAX_ORDERS", "5")),
            auto_trading=AutoTradingConfig(
                live_enabled=_as_config_bool(auto_yaml.get("live_enabled"), False),
                weekly_budget=float(auto_yaml.get("weekly_budget", 0.0)),
                weekly_execution_enabled=_as_config_bool(auto_yaml.get("weekly_execution_enabled"), False),
                weekly_execution_week_id=str(auto_yaml.get("weekly_execution_week_id", "") or ""),
                overrun_tolerance=float(auto_yaml.get("overrun_tolerance", 50000.0)),
                quote_stale_seconds=int(auto_yaml.get("quote_stale_seconds", 15)),
                test_qty_single_source=int(auto_yaml.get("test_qty_single_source", 1)),
                test_qty_dual_source=int(auto_yaml.get("test_qty_dual_source", 2)),
                test_max_total_buy_amount=float(auto_yaml.get("test_max_total_buy_amount", 50000.0)),
                test_max_single_stock_amount=float(auto_yaml.get("test_max_single_stock_amount", 10000.0)),
                basket_auto_exit_enabled=_as_config_bool(auto_yaml.get("basket_auto_exit_enabled"), False),
                basket_recommendation_enabled=_as_config_bool(auto_yaml.get("basket_recommendation_enabled"), True),
                allow_buy_after_first_trade_day=_as_config_bool(
                    auto_yaml.get("allow_buy_after_first_trade_day"),
                    False,
                ),
                a_preselect_confirmation_start_time=str(
                    auto_yaml.get("a_preselect_confirmation_start_time", "10:00") or "10:00"
                ),
                enable_secondary_add=_as_config_bool(auto_yaml.get("enable_secondary_add"), False),
                secondary_add_budget_pct_min=float(auto_yaml.get("secondary_add_budget_pct_min", 0.30)),
                secondary_add_budget_pct_max=float(auto_yaml.get("secondary_add_budget_pct_max", 0.40)),
                cost_buffer_multiplier=float(auto_yaml.get("cost_buffer_multiplier", 1.015)),
                per_stock_profit_buffer_pct=float(auto_yaml.get("per_stock_profit_buffer_pct", 0.006)),
                per_stock_profit_buffer_min_twd=float(auto_yaml.get("per_stock_profit_buffer_min_twd", 100.0)),
                basket_profit_buffer_pct=float(auto_yaml.get("basket_profit_buffer_pct", 0.008)),
                basket_profit_buffer_min_twd=float(auto_yaml.get("basket_profit_buffer_min_twd", 3000.0)),
                max_loser_loss_ratio_to_winner_profit=float(
                    auto_yaml.get("max_loser_loss_ratio_to_winner_profit", 0.35)
                ),
            ),
            fees=FeeConfig(
                commission_rate=float(fee_yaml.get("commission_rate", 0.001425)),
                commission_discount=float(fee_yaml.get("commission_discount", 1.0)),
                minimum_commission=float(fee_yaml.get("minimum_commission", 20.0)),
                transaction_tax_rate_stock=float(fee_yaml.get("transaction_tax_rate_stock", 0.003)),
                transaction_tax_rate_day_trade=float(fee_yaml.get("transaction_tax_rate_day_trade", 0.0015)),
                other_fee_rules=dict(fee_yaml.get("other_fee_rules", {})),
            ),
            providers=ProviderConfig(
                active=str(providers_section.get("active", "manual_csv")),
                definitions={
                    key: value
                    for key, value in providers_section.items()
                    if key != "active" and isinstance(value, dict)
                },
            ),
            project_root=root,
            obsidian_vault_root=Path(obsidian_vault_raw) if obsidian_vault_raw else _default_obsidian_vault_root(root),
        )

    @classmethod
    def from_env(cls) -> "Settings":
        return cls.load(PROJECT_ROOT)

    def require_api_credentials(self) -> None:
        if not self.api_key or not self.secret_key:
            raise RuntimeError("Missing SINOPAC_API_KEY or SINOPAC_SECRET_KEY in .env.")

    def require_live_setup(self) -> None:
        missing: list[str] = []
        if not self.person_id:
            missing.append("SINOPAC_PERSON_ID")
        if not self.ca_path:
            missing.append("SINOPAC_CA_PATH")
        if not self.ca_password:
            missing.append("SINOPAC_CA_PASSWORD")
        if missing:
            raise RuntimeError(f"Missing live trading config: {', '.join(missing)}")

    def normalized_ca_path(self) -> str:
        if not self.ca_path:
            raise RuntimeError("SINOPAC_CA_PATH is not configured.")
        return str(self.ca_path).replace("\\", "/")

    def provider_options(self, provider_name: str | None = None) -> dict[str, Any]:
        return self.providers.options(provider_name)

    def live_trading_confirmed(self, *, confirm_live: bool = False) -> bool:
        return (
            self.auto_trading.live_enabled
            and confirm_live
            and _as_bool(os.getenv("AUTO_TRADE_LIVE"), default=False)
        )

    def evaluate_live_submit_guard(
        self,
        *,
        confirm_live: bool = False,
        trade_date: date | None = None,
    ) -> tuple[bool, str]:
        if not self.allow_live_submit:
            return False, "allow_live_submit_disabled"
        if not self.auto_trading.live_enabled:
            return False, "config_live_disabled"
        if not self.auto_trading.weekly_execution_enabled:
            return False, "weekly_execution_disabled"
        if self.auto_trading.weekly_budget <= 0:
            return False, "weekly_budget_missing"
        if not self.auto_trading.weekly_execution_week_matches(trade_date):
            return False, "weekly_execution_week_mismatch"
        if not confirm_live:
            return False, "confirm_live_missing"
        if not self.live_trading_confirmed(confirm_live=confirm_live):
            return False, "auto_trade_live_env_missing"
        return True, "live_confirmed"
