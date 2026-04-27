# Broker Adapter Contract

The framework must not hard-code strategy logic into a single broker SDK.

Required broker methods:

- `get_account_summary()`
- `get_cash_available()`
- `get_positions()`
- `place_buy_order(stock_id, price, qty, order_lot, metadata)`
- `place_sell_order(stock_id, price, qty, order_lot, metadata)`
- `cancel_order(order_id)`
- `get_order_status(order_id)`
- `get_fills(since)`
- `is_market_open()`
- `supports_order_lot(order_lot)`

The first live implementation is `ShioajiSinoPacBrokerAdapter`, but the abstraction remains broker-agnostic.
