from __future__ import annotations

from dataclasses import dataclass, field


class OrderRefused(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class OrderRequest:
    idempotency_key: str
    side: str
    exchange: str
    tradingsymbol: str
    token: int
    quantity: int
    order_type: str
    price: float | None
    product: str
    tag: str
    mode: str


@dataclass
class BrokerOrder:
    broker_order_id: str
    status: str
    filled_quantity: int = 0
    average_price: float | None = None


class OrderGateway:
    def find_by_tag(self, tag: str) -> BrokerOrder | None:
        raise NotImplementedError

    def place(self, request: OrderRequest, market_protection_percent: float | None) -> BrokerOrder:
        raise NotImplementedError


@dataclass
class ExecutionResult:
    status: str
    broker_order_id: str | None = None
    reason: str = ""
    created: bool = False


@dataclass
class LiveBroker:
    """Places at most one live order per tag, and only when this process is armed."""

    armed: bool
    kill_switch: bool
    market_protection_percent: float | None
    supports_market_protection: bool
    recent_places: list[float] = field(default_factory=list)
    max_per_second: int = 5

    def submit(self, request: OrderRequest, gateway: OrderGateway, now_monotonic: float) -> ExecutionResult:
        if request.mode != "LIVE":
            raise OrderRefused("live broker received a non-live request")
        if not self.armed:
            raise OrderRefused("live trading is not armed in this process")
        if self.kill_switch:
            raise OrderRefused("kill switch is on")
        if request.order_type == "MARKET":
            if not self.supports_market_protection or not self.market_protection_percent or self.market_protection_percent <= 0:
                raise OrderRefused("MARKET orders require a non-zero market protection field")
        existing = gateway.find_by_tag(request.tag)
        if existing is not None:
            return ExecutionResult(
                status=existing.status,
                broker_order_id=existing.broker_order_id,
                reason="existing order found for this tag",
                created=False,
            )
        window = [stamp for stamp in self.recent_places if now_monotonic - stamp < 1]
        self.recent_places = window
        if len(window) >= self.max_per_second:
            raise OrderRefused("order rate guard")
        placed = gateway.place(request, self.market_protection_percent)
        self.recent_places.append(now_monotonic)
        return ExecutionResult(status=placed.status, broker_order_id=placed.broker_order_id, created=True)


def analysis_blocks_orders(mode: str) -> bool:
    return mode == "ANALYSIS"
