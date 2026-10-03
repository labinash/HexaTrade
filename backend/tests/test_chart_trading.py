"""Chart CE/PE trading: dynamic strikes, lot-based qty, paper vs live split."""

from datetime import time

from app.domain.chart_trading import (
    DEFAULT_MARKET_PROTECTION_PERCENT,
    LIVE_ORDER_PHRASE,
    STATUS_BLOCKED,
    STATUS_COMPLETE,
    STATUS_OPEN,
    STATUS_REJECTED,
    STATUS_SUBMITTED,
    assert_underlying,
    chart_order_tag,
    current_value,
    display_status,
    invested_value,
    live_badge,
    live_unrealized_pnl,
    market_protection_percent,
    quantity_from_lots,
    recommended_action,
    recommended_option,
    three_strikes,
    validate_hint,
)
from app.domain.execution import OrderRefused


def test_ce_and_pe_itm_otm_are_opposite():
    strikes = [23400.0, 23450.0, 23500.0, 23550.0, 23600.0]
    ce = three_strikes(strikes, 23500.0, "CE")
    pe = three_strikes(strikes, 23500.0, "PE")
    assert ce == {"ITM": 23450.0, "ATM": 23500.0, "OTM": 23550.0}
    assert pe == {"ITM": 23550.0, "ATM": 23500.0, "OTM": 23450.0}


def test_quantity_comes_from_lot_size_not_a_hard_coded_65():
    assert quantity_from_lots(1, 65) == 65
    assert quantity_from_lots(2, 20) == 40
    try:
        quantity_from_lots(1, 0)
        assert False
    except OrderRefused:
        pass


def test_browser_quantity_that_does_not_match_lots_is_rejected():
    expected = {"option_type": "CE", "strike": 23500.0, "tradingsymbol": "NIFTY26O0623500CE", "quantity": 65}
    try:
        validate_hint(expected=expected, quantity=75)
        assert False
    except OrderRefused as exc:
        assert "lot size" in exc.reason


def test_browser_strike_that_does_not_match_ladder_is_rejected():
    expected = {"option_type": "CE", "strike": 23500.0, "tradingsymbol": "NIFTY26O0623500CE", "quantity": 65}
    try:
        validate_hint(expected=expected, strike=23999)
        assert False
    except OrderRefused as exc:
        assert "ATM ladder" in exc.reason


def test_market_protection_zero_is_refused():
    try:
        market_protection_percent(0)
        assert False
    except OrderRefused:
        pass
    try:
        market_protection_percent(None)
        assert False
    except OrderRefused:
        pass
    assert market_protection_percent(DEFAULT_MARKET_PROTECTION_PERCENT) == 2.0


def test_recommended_side_follows_existing_trend_mapping():
    assert recommended_option("BULLISH") == "CE"
    assert recommended_option("BEARISH") == "PE"
    assert recommended_option("NEUTRAL") is None
    assert recommended_action("BULLISH", True) == "BUY CE"
    assert recommended_action("BEARISH", False) == "WATCH PE"
    assert recommended_action("NOT READY") == "NO TRADE"


def test_only_nifty_and_sensex_are_allowed():
    assert assert_underlying("nifty") == "NIFTY"
    try:
        assert_underlying("BANKNIFTY")
        assert False
    except OrderRefused:
        pass


def test_live_unrealized_pnl_uses_ltp_minus_average_times_qty():
    assert live_unrealized_pnl(121.20, 128.50, 65) == 474.5
    assert invested_value(121.20, 65) == 7878.0
    assert current_value(128.50, 65) == 8352.5
    assert live_unrealized_pnl(100, 90, -50) == 500.0


def test_order_status_does_not_call_submit_complete():
    assert display_status("OPEN", submitted=True) == STATUS_OPEN
    assert display_status("COMPLETE") == STATUS_COMPLETE
    assert display_status("REJECTED") == STATUS_REJECTED
    assert display_status(None, submitted=True) == STATUS_SUBMITTED
    assert STATUS_BLOCKED.startswith("LIVE ORDER BLOCKED")
    assert LIVE_ORDER_PHRASE == "CONFIRM LIVE BUY"


def test_paper_and_live_badges_stay_distinct():
    assert live_badge(False) == "PAPER TRADING"
    assert live_badge(True) == "LIVE TRADING ENABLED"


def test_idempotency_tag_is_stable_and_short():
    tag = chart_order_tag("abc-123")
    assert tag == chart_order_tag("abc-123")
    assert tag != chart_order_tag("abc-124")
    assert len(tag) <= 20
    assert tag.startswith("C")


def test_live_broker_refuses_market_without_protection():
    from app.domain.execution import LiveBroker, OrderRequest

    class Gateway:
        def find_by_tag(self, tag):
            return None

        def place(self, request, protection):
            raise AssertionError("must not place")

    broker = LiveBroker(armed=True, kill_switch=False, market_protection_percent=None, supports_market_protection=True)
    request = OrderRequest(
        idempotency_key="k", side="BUY", exchange="NFO", tradingsymbol="NIFTY", token=1,
        quantity=65, order_type="MARKET", price=None, product="MIS", tag="Cabc", mode="LIVE",
    )
    try:
        broker.submit(request, Gateway(), 1.0)
        assert False
    except OrderRefused as exc:
        assert "market protection" in exc.reason


def test_idempotent_gateway_replay_does_not_place_twice():
    from app.domain.execution import BrokerOrder, LiveBroker, OrderRequest

    class Gateway:
        def __init__(self):
            self.calls = 0
            self.existing = None

        def find_by_tag(self, tag):
            return self.existing

        def place(self, request, protection):
            self.calls += 1
            self.existing = BrokerOrder(broker_order_id="2601", status="OPEN")
            return self.existing

    gateway = Gateway()
    broker = LiveBroker(armed=True, kill_switch=False, market_protection_percent=2.0, supports_market_protection=True)
    request = OrderRequest(
        idempotency_key="k", side="BUY", exchange="NFO", tradingsymbol="NIFTY", token=1,
        quantity=65, order_type="MARKET", price=None, product="MIS", tag="Cabc", mode="LIVE",
    )
    first = broker.submit(request, gateway, 1.0)
    second = broker.submit(request, gateway, 2.0)
    assert gateway.calls == 1
    assert first.broker_order_id == second.broker_order_id == "2601"


def test_trade_audit_model_has_no_secret_columns():
    from app.db.models import TradingOrderAudit

    names = set(TradingOrderAudit.__table__.columns.keys())
    forbidden = {"access_token", "api_secret", "api_key", "request_token", "session_token", "access_token_encrypted"}
    assert names.isdisjoint(forbidden)
    assert "broker_order_id" in names
    assert "signal_direction" in names


def test_risk_blocks_daily_loss():
    from app.domain.risk import RiskContext, evaluate_risk

    ctx = RiskContext(
        mode="LIVE", kill_switch=False, data_stale=False, token_valid=True,
        now=time(10, 0), session_start=time(9, 15), session_end=time(15, 30),
        entry_start=time(9, 30), entry_stop=time(15, 0), square_off=time(15, 15),
        mis_cutoff=time(15, 20), daily_pnl=-5000, max_daily_loss=5000,
        trades_today=0, max_trades_per_day=3, open_positions=0, max_open_positions=2,
        exposure=0, max_exposure=200000, quantity=65, lot_size=65, max_quantity=5000,
        capital_required=1000, max_capital=100000, spread=0.5, max_spread=2,
        option_volume=100, min_option_volume=0, option_oi=100, min_option_oi=0,
        exchange="NFO", expected_exchange="NFO", product="MIS",
        underlying_trades=0, max_trades_per_underlying=2, cooldown_active=False,
        duplicate_signal=False, redis_ok=True, database_ok=True,
    )
    decision = evaluate_risk(ctx)
    assert not decision.allowed
    assert "daily loss" in decision.reasons[0]
