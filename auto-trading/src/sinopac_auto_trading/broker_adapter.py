from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .config import Settings
from .order_engine import QuoteState
from .order_planner import PlannedOrder
from .basket import broker_custom_field_for_strategy_lot
from .shioaji_client import describe_account, login, resolve_stock_contract, submit_stock_order
from .time_utils import TAIPEI


@dataclass(slots=True)
class AccountSummary:
    broker_id: str
    account_id: str
    signed: bool
    account_type: str


@dataclass(slots=True)
class PositionSnapshot:
    stock_id: str
    stock_name: str
    quantity: int
    avg_price: float


@dataclass(slots=True)
class BrokerOrderResult:
    stock_id: str
    side: str
    order_price: float
    order_qty: int
    order_lot: str
    status: str
    order_id: str = ""
    detail: str = ""
    raw: Any = None


@dataclass(slots=True)
class ManagedOrderSnapshot:
    order_id: str
    stock_id: str
    order_price: float
    order_qty: int
    filled_qty: int
    remaining_qty: int
    status: str


class BrokerAdapter(ABC):
    @abstractmethod
    def get_account_summary(self) -> AccountSummary:
        raise NotImplementedError

    @abstractmethod
    def get_cash_available(self) -> float:
        raise NotImplementedError

    @abstractmethod
    def get_positions(self) -> list[PositionSnapshot]:
        raise NotImplementedError

    @abstractmethod
    def place_buy_order(self, stock_id: str, price: float, qty: int, order_lot: str, metadata: dict[str, Any]) -> BrokerOrderResult:
        raise NotImplementedError

    @abstractmethod
    def place_sell_order(self, stock_id: str, price: float, qty: int, order_lot: str, metadata: dict[str, Any]) -> BrokerOrderResult:
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, order_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_order_status(self, order_id: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def get_managed_order(self, order_id: str) -> ManagedOrderSnapshot | None:
        raise NotImplementedError

    def get_managed_order_by_custom_field(
        self,
        custom_field: str,
        *,
        side: str | None = None,
        stock_id: str | None = None,
    ) -> ManagedOrderSnapshot | None:
        return None

    @abstractmethod
    def get_fills(self, since: datetime | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def is_market_open(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def supports_order_lot(self, order_lot: str) -> bool:
        raise NotImplementedError


@dataclass(slots=True)
class FakeBrokerAdapter(BrokerAdapter):
    cash_available: float = 0.0
    market_open: bool = True
    signed: bool = True
    account_id: str = "FAKE0001"
    broker_id: str = "FAKE"
    orders: dict[str, BrokerOrderResult] = field(default_factory=dict)
    fills: list[dict[str, Any]] = field(default_factory=list)

    def get_account_summary(self) -> AccountSummary:
        return AccountSummary(
            broker_id=self.broker_id,
            account_id=self.account_id,
            signed=self.signed,
            account_type="Stock",
        )

    def get_cash_available(self) -> float:
        return self.cash_available

    def get_positions(self) -> list[PositionSnapshot]:
        return []

    def place_buy_order(self, stock_id: str, price: float, qty: int, order_lot: str, metadata: dict[str, Any]) -> BrokerOrderResult:
        order_id = f"DRY-{len(self.orders) + 1:04d}"
        result = BrokerOrderResult(stock_id, "Buy", price, qty, order_lot, "dry_run", order_id, raw=metadata)
        self.orders[order_id] = result
        return result

    def place_sell_order(self, stock_id: str, price: float, qty: int, order_lot: str, metadata: dict[str, Any]) -> BrokerOrderResult:
        order_id = f"DRY-{len(self.orders) + 1:04d}"
        result = BrokerOrderResult(stock_id, "Sell", price, qty, order_lot, "dry_run", order_id, raw=metadata)
        self.orders[order_id] = result
        return result

    def cancel_order(self, order_id: str) -> None:
        if order_id in self.orders:
            self.orders[order_id].status = "cancelled"

    def get_order_status(self, order_id: str) -> str:
        return self.orders.get(order_id, BrokerOrderResult("", "", 0, 0, "", "unknown")).status

    def get_managed_order(self, order_id: str) -> ManagedOrderSnapshot | None:
        order = self.orders.get(order_id)
        if order is None:
            return None
        return ManagedOrderSnapshot(
            order_id=order.order_id,
            stock_id=order.stock_id,
            order_price=order.order_price,
            order_qty=order.order_qty,
            filled_qty=order.order_qty if order.status == "filled" else 0,
            remaining_qty=0 if order.status == "filled" else order.order_qty,
            status=order.status,
        )

    def get_managed_order_by_custom_field(
        self,
        custom_field: str,
        *,
        side: str | None = None,
        stock_id: str | None = None,
    ) -> ManagedOrderSnapshot | None:
        target_field = str(custom_field or "").strip()
        if not target_field:
            return None
        side_text = str(side or "").strip().lower()
        stock_text = str(stock_id or "").strip()
        for order in self.orders.values():
            metadata = order.raw if isinstance(order.raw, dict) else {}
            if str(metadata.get("broker_custom_field", "")).strip() != target_field:
                continue
            if side_text and str(order.side or "").strip().lower() != side_text:
                continue
            if stock_text and str(order.stock_id or "").strip() != stock_text:
                continue
            return self.get_managed_order(order.order_id)
        return None

    def get_fills(self, since: datetime | None = None) -> list[dict[str, Any]]:
        return list(self.fills)

    def is_market_open(self) -> bool:
        return self.market_open

    def supports_order_lot(self, order_lot: str) -> bool:
        return order_lot.lower() in {"intraday_odd_lot", "common"}


class ShioajiSinoPacBrokerAdapter(BrokerAdapter):
    def __init__(self, settings: Settings, *, simulation: bool = False) -> None:
        self.settings = settings
        self.simulation = simulation
        self.api, self.accounts = login(settings, simulation=simulation, fetch_contract=True)
        self.stock_account = next(
            (account for account in self.accounts if "Stock" in str(getattr(account, "account_type", ""))),
            None,
        ) or getattr(self.api, "stock_account", None)

    def get_account_summary(self) -> AccountSummary:
        account = self.stock_account
        return AccountSummary(
            broker_id=str(getattr(account, "broker_id", "")),
            account_id=str(getattr(account, "account_id", "")),
            signed=bool(getattr(account, "signed", False)),
            account_type=str(getattr(account, "account_type", "")),
        )

    def get_cash_available(self) -> float:
        balance = self.api.account_balance(account=self.api.stock_account)
        return float(getattr(balance, "acc_balance", 0.0))

    def get_positions(self) -> list[PositionSnapshot]:
        try:
            positions = self.api.list_positions(self.api.stock_account)
        except Exception:
            return []
        snapshots: list[PositionSnapshot] = []
        for position in positions or []:
            snapshots.append(
                PositionSnapshot(
                    stock_id=str(getattr(position, "code", "")),
                    stock_name=str(getattr(position, "name", "")),
                    quantity=int(getattr(position, "quantity", 0)),
                    avg_price=float(getattr(position, "price", 0.0)),
                )
            )
        return snapshots

    def _submit(self, stock_id: str, price: float, qty: int, order_lot: str, side: str, metadata: dict[str, Any]) -> BrokerOrderResult:
        lot_map = {"intraday_odd_lot": "IntradayOdd", "common": "Common"}
        strategy_lot_id = str(metadata.get("strategy_lot_id", "")).strip()
        broker_custom_field = str(metadata.get("broker_custom_field", "")).strip()
        if not broker_custom_field and strategy_lot_id:
            broker_custom_field = broker_custom_field_for_strategy_lot(
                strategy_lot_id,
                prefix=metadata.get("custom_prefix", "AT"),
            )
        planned = PlannedOrder(
            plan_rank=1,
            stock_id=stock_id,
            stock_name=metadata.get("stock_name", stock_id),
            exchange_hint=metadata.get("exchange_hint", ""),
            side=side,
            order_lot=lot_map.get(order_lot.lower(), order_lot),
            quantity=qty,
            reference_price=price,
            limit_price=price,
            budget_twd=price * qty,
            confidence=None,
            model_rank=None,
            stage_1_price=None,
            stage_2_price=None,
            target_price=None,
            source_csv="manual",
            note=metadata.get("note", ""),
        )
        trade = submit_stock_order(
            self.api,
            planned,
            custom_prefix=metadata.get("custom_prefix", "AT"),
            custom_field=broker_custom_field,
        )
        return BrokerOrderResult(
            stock_id=stock_id,
            side=side,
            order_price=price,
            order_qty=qty,
            order_lot=order_lot,
            status=str(getattr(getattr(trade, "status", None), "status", "unknown")),
            order_id=str(getattr(getattr(trade, "order", None), "id", "")),
            detail=str(trade),
            raw=trade,
        )

    def place_buy_order(self, stock_id: str, price: float, qty: int, order_lot: str, metadata: dict[str, Any]) -> BrokerOrderResult:
        return self._submit(stock_id, price, qty, order_lot, "Buy", metadata)

    def place_sell_order(self, stock_id: str, price: float, qty: int, order_lot: str, metadata: dict[str, Any]) -> BrokerOrderResult:
        return self._submit(stock_id, price, qty, order_lot, "Sell", metadata)

    def cancel_order(self, order_id: str) -> None:
        trade = self.get_trade(order_id)
        if trade is None:
            return
        self.api.cancel_order(trade)

    def get_order_status(self, order_id: str) -> str:
        trade = self.get_trade(order_id)
        if trade is None:
            return "unknown"
        return self.classify_trade_state(trade)

    def get_managed_order(self, order_id: str) -> ManagedOrderSnapshot | None:
        trade = self.get_trade(order_id)
        if trade is None:
            return None
        order = getattr(trade, "order", None)
        quantity = int(getattr(order, "quantity", 0) or 0)
        filled_qty = self._filled_qty(trade)
        remaining_qty = max(quantity - filled_qty, 0)
        return ManagedOrderSnapshot(
            order_id=self._order_id(trade),
            stock_id=str(getattr(getattr(trade, "contract", None), "code", "")),
            order_price=float(getattr(order, "price", 0.0) or 0.0),
            order_qty=quantity,
            filled_qty=filled_qty,
            remaining_qty=remaining_qty,
            status=self.classify_trade_state(trade),
        )

    def get_managed_order_by_custom_field(
        self,
        custom_field: str,
        *,
        side: str | None = None,
        stock_id: str | None = None,
    ) -> ManagedOrderSnapshot | None:
        target_field = str(custom_field or "").strip()
        if not target_field:
            return None
        side_text = str(side or "").strip().lower()
        stock_text = str(stock_id or "").strip()
        self.api.update_status(self.api.stock_account)
        for trade in self.api.list_trades() or []:
            order = getattr(trade, "order", None)
            if str(getattr(order, "custom_field", "") or "").strip() != target_field:
                continue
            if side_text and side_text not in str(getattr(order, "action", "") or "").strip().lower():
                continue
            if stock_text and str(getattr(getattr(trade, "contract", None), "code", "")).strip() != stock_text:
                continue
            snapshot = self.get_managed_order(self._order_id(trade))
            if snapshot and snapshot.status in {"active", "filled"}:
                return snapshot
        return None

    def get_fills(self, since: datetime | None = None) -> list[dict[str, Any]]:
        self.api.update_status(self.api.stock_account)
        fills: list[dict[str, Any]] = []
        for trade in self.api.list_trades() or []:
            status = getattr(trade, "status", None)
            filled_qty = self._filled_qty(trade)
            if filled_qty <= 0:
                continue
            fill_time = self._fill_time(trade)
            if since is not None:
                if fill_time is None:
                    continue
                comparable_fill_time = fill_time
                if comparable_fill_time.tzinfo is None and since.tzinfo is not None:
                    comparable_fill_time = comparable_fill_time.replace(tzinfo=since.tzinfo)
                if comparable_fill_time < since:
                    continue
            fills.append(
                {
                    "order_id": self._order_id(trade),
                    "stock_id": str(getattr(getattr(trade, "contract", None), "code", "")),
                    "side": str(getattr(getattr(trade, "order", None), "action", "")),
                    "fill_qty": filled_qty,
                    "fill_price": float(getattr(getattr(trade, "order", None), "price", 0.0) or 0.0),
                    "fill_time": fill_time.isoformat() if fill_time else "",
                    "broker_custom_field": str(getattr(getattr(trade, "order", None), "custom_field", "") or ""),
                }
            )
        return fills

    def is_market_open(self) -> bool:
        now = datetime.now(TAIPEI)
        return now.weekday() < 5 and (9, 0) <= (now.hour, now.minute) <= (13, 30)

    def supports_order_lot(self, order_lot: str) -> bool:
        return order_lot.lower() in {"intraday_odd_lot", "common"}

    def resolve_reference_price(self, stock_id: str, exchange_hint: str = "") -> tuple[str, float, str]:
        contract = resolve_stock_contract(self.api, stock_id, exchange_hint=exchange_hint)
        return str(contract.name), float(contract.reference), str(contract.exchange)

    def describe_accounts(self) -> list[str]:
        return [describe_account(account) for account in self.accounts]

    @staticmethod
    def _order_id(trade: Any) -> str:
        return str(getattr(getattr(trade, "order", None), "id", ""))

    @staticmethod
    def _order_qty(trade: Any) -> int:
        status = getattr(trade, "status", None)
        order = getattr(trade, "order", None)
        raw = getattr(status, "order_quantity", None)
        if raw in (None, "", 0):
            raw = getattr(order, "quantity", 0)
        return int(raw or 0)

    @staticmethod
    def _filled_qty(trade: Any) -> int:
        status = getattr(trade, "status", None)
        for field_name in ("deal_quantity", "filled_quantity", "deal_qty"):
            raw = getattr(status, field_name, None)
            if raw not in (None, ""):
                return int(raw or 0)
        return 0

    @staticmethod
    def _cancel_qty(trade: Any) -> int:
        status = getattr(trade, "status", None)
        return int(getattr(status, "cancel_quantity", 0) or 0)

    @staticmethod
    def _fill_time(trade: Any) -> datetime | None:
        status = getattr(trade, "status", None)
        raw = getattr(status, "order_datetime", None)
        if isinstance(raw, datetime):
            return raw
        if raw in (None, ""):
            return None
        try:
            return datetime.fromisoformat(str(raw))
        except ValueError:
            return None

    def classify_trade_state(self, trade: Any) -> str:
        status_name = str(getattr(getattr(trade, "status", None), "status", "")).lower()
        order_qty = self._order_qty(trade)
        filled_qty = self._filled_qty(trade)
        cancel_qty = self._cancel_qty(trade)

        if order_qty > 0 and filled_qty >= order_qty:
            return "filled"
        if any(keyword in status_name for keyword in ("failed", "fail", "rejected", "reject", "error")):
            return "failed"
        if any(keyword in status_name for keyword in ("cancel", "cancelled", "canceled")) or (order_qty > 0 and cancel_qty >= order_qty):
            return "cancelled"
        if "filled" in status_name:
            return "filled"
        return "active"

    def get_trade(self, order_id: str) -> Any | None:
        self.api.update_status(self.api.stock_account)
        for trade in self.api.list_trades() or []:
            if self._order_id(trade) == order_id:
                return trade
        return None

    @staticmethod
    def _snapshot_timestamp(snapshot: Any) -> str:
        raw = getattr(snapshot, "ts", None)
        if raw in (None, ""):
            return datetime.now(TAIPEI).isoformat()
        try:
            return datetime.fromtimestamp(int(raw) / 1_000_000_000, tz=TAIPEI).isoformat()
        except Exception:
            return datetime.now(TAIPEI).isoformat()

    def get_quote_state(self, stock_id: str, exchange_hint: str = "") -> tuple[QuoteState, str, str, str]:
        contract = resolve_stock_contract(self.api, stock_id, exchange_hint=exchange_hint)
        snapshots = self.api.snapshots([contract])
        snapshot = snapshots[0]
        last_price = float(getattr(snapshot, "close", 0) or getattr(snapshot, "sell_price", 0) or getattr(snapshot, "buy_price", 0) or 0)
        if last_price <= 0:
            raise RuntimeError(f"Snapshot for {stock_id} does not have a usable last price.")
        quote = QuoteState(
            last_price=last_price,
            bid1=float(getattr(snapshot, "buy_price", 0) or 0) or None,
            ask1=float(getattr(snapshot, "sell_price", 0) or 0) or None,
            limit_up_price=float(getattr(contract, "limit_up", 0) or 0) or None,
            limit_down_price=float(getattr(contract, "limit_down", 0) or 0) or None,
        )
        return quote, str(contract.name), str(contract.exchange), self._snapshot_timestamp(snapshot)
