from __future__ import annotations

from typing import Any

from .config import Settings
from .order_planner import PlannedOrder


def _load_shioaji() -> Any:
    try:
        import shioaji as sj  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on local env
        raise RuntimeError(
            "shioaji is not installed. Install dependencies first with `python -m pip install -e .`."
        ) from exc
    return sj


def login(settings: Settings, *, simulation: bool, fetch_contract: bool = True) -> tuple[Any, list[Any]]:
    settings.require_api_credentials()
    sj = _load_shioaji()

    api = sj.Shioaji(simulation=simulation)
    accounts = api.login(
        api_key=settings.api_key,
        secret_key=settings.secret_key,
        fetch_contract=fetch_contract,
    )

    if not simulation:
        settings.require_live_setup()
        activated = api.activate_ca(
            ca_path=settings.normalized_ca_path(),
            ca_passwd=settings.ca_password,
            person_id=settings.person_id,
        )
        if not activated:
            raise RuntimeError("activate_ca returned False.")

    return api, list(accounts or [])


def describe_account(account: Any) -> str:
    return (
        f"account_type={getattr(account, 'account_type', '')} "
        f"broker_id={getattr(account, 'broker_id', '')} "
        f"account_id={getattr(account, 'account_id', '')} "
        f"signed={getattr(account, 'signed', '')}"
    )


def _resolve_stock_contract(api: Any, stock_id: str, exchange_hint: str = "") -> Any:
    candidates = []
    if exchange_hint:
        candidates.append(exchange_hint.upper())
    candidates.extend(["TSE", "OTC"])

    seen: set[str] = set()
    for market in candidates:
        if market in seen:
            continue
        seen.add(market)
        market_contracts = getattr(api.Contracts.Stocks, market, None)
        if market_contracts is None:
            continue
        try:
            contract = market_contracts[stock_id]
        except KeyError:
            continue
        if contract is not None and getattr(contract, "code", ""):
            return contract

    raise RuntimeError(f"Unable to resolve stock contract for {stock_id}.")


def _custom_field(prefix: str, rank: int) -> str:
    compact = "".join(char for char in prefix.upper() if char.isalnum())[:2] or "AT"
    return f"{compact}{rank:03d}"[:6]


def submit_stock_order(api: Any, order: PlannedOrder, *, custom_prefix: str = "AT", custom_field: str = "") -> Any:
    sj = _load_shioaji()
    contract = _resolve_stock_contract(api, order.stock_id, exchange_hint=order.exchange_hint)
    order_lot = getattr(sj.constant.StockOrderLot, order.order_lot)
    action = getattr(sj.constant.Action, order.side)
    normalized_custom_field = "".join(char for char in str(custom_field or "").upper() if char.isalnum())[:6] or _custom_field(custom_prefix, order.plan_rank)

    shioaji_order = api.Order(
        price=order.limit_price,
        quantity=order.quantity,
        action=action,
        price_type=sj.constant.StockPriceType.LMT,
        order_type=sj.constant.OrderType.ROD,
        order_lot=order_lot,
        custom_field=normalized_custom_field,
        account=api.stock_account,
    )
    return api.place_order(contract, shioaji_order)


def resolve_stock_contract(api: Any, stock_id: str, exchange_hint: str = "") -> Any:
    return _resolve_stock_contract(api, stock_id, exchange_hint=exchange_hint)
