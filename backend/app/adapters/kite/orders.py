import inspect
import logging

from app.domain.execution import BrokerOrder, OrderRequest

log = logging.getLogger("hextrade.kite.orders")


class KiteOrderGateway:
    """Live order gateway. Retries never call place_order until the tag is confirmed absent."""

    def __init__(self, kite) -> None:
        self.kite = kite
        signature = inspect.signature(kite.place_order)
        self.supports_market_protection = "market_protection" in signature.parameters

    def find_by_tag(self, tag: str) -> BrokerOrder | None:
        orders = self.kite.orders()
        for order in orders:
            if order.get("tag") == tag:
                return BrokerOrder(
                    broker_order_id=str(order.get("order_id")),
                    status=str(order.get("status") or "UNKNOWN"),
                    filled_quantity=int(order.get("filled_quantity") or 0),
                    average_price=order.get("average_price"),
                )
        return None

    def place(self, request: OrderRequest, market_protection_percent: float | None) -> BrokerOrder:
        existing = self.find_by_tag(request.tag)
        if existing is not None:
            return existing
        kwargs = dict(
            variety=self.kite.VARIETY_REGULAR,
            exchange=request.exchange,
            tradingsymbol=request.tradingsymbol,
            transaction_type=request.side,
            quantity=request.quantity,
            product=request.product,
            order_type=request.order_type,
            price=request.price,
            validity=self.kite.VALIDITY_DAY,
            tag=request.tag[:20],
        )
        if request.order_type == "MARKET":
            if not self.supports_market_protection:
                raise RuntimeError("MARKET orders require a Kite client that accepts market_protection")
            if market_protection_percent is None or float(market_protection_percent) <= 0:
                raise RuntimeError("MARKET orders require a non-zero market protection field")
            kwargs["market_protection"] = float(market_protection_percent)
            kwargs.pop("price", None)
        order_id = self.kite.place_order(**kwargs)
        return BrokerOrder(broker_order_id=str(order_id), status="OPEN")

    def cancel(self, order_id: str) -> None:
        variety = self.kite.VARIETY_REGULAR
        self.kite.cancel_order(variety, order_id)

    def positions(self) -> dict:
        payload = self.kite.positions()
        if not isinstance(payload, dict):
            return {"net": [], "day": []}
        return {"net": payload.get("net") or [], "day": payload.get("day") or []}

    def orders(self) -> list[dict]:
        rows = self.kite.orders()
        return list(rows or [])

    def trades(self) -> list[dict]:
        rows = self.kite.trades()
        return list(rows or [])

    def order_history(self, order_id: str) -> list[dict]:
        rows = self.kite.order_history(order_id)
        return list(rows or [])

    def margins(self) -> dict:
        payload = self.kite.margins("equity")
        return payload if isinstance(payload, dict) else {}

    def profile(self) -> dict:
        payload = self.kite.profile()
        return payload if isinstance(payload, dict) else {}

    def order(self, order_id: str) -> dict | None:
        for row in self.orders():
            if str(row.get("order_id") or "") == str(order_id):
                return row
        return None
