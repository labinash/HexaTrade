"""Alert-only CE/PE scoring. Does not place orders or change paper auto."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time


NEW = "NEW"
ACTIVE = "ACTIVE"
TARGET1 = "TARGET 1 HIT"
TARGET2 = "TARGET 2 HIT"
STOP_HIT = "STOP-LOSS HIT"
EXPIRED = "EXPIRED"
CANCELLED = "CANCELLED"
BUY = "BUY"
WAIT = "WAIT"
OPEN_STATUSES = (NEW, ACTIVE, TARGET1)

STATUS_CODE = {
    NEW: "NEW",
    ACTIVE: "ACTIVE",
    TARGET1: "TARGET_HIT",
    TARGET2: "TARGET_HIT",
    STOP_HIT: "STOP_LOSS_HIT",
    EXPIRED: "EXPIRED",
    CANCELLED: "CANCELLED",
    WAIT: "REJECTED",
}


def public_status(status: str | None, *, blocked: bool = False) -> str:
    if blocked and (not status or status == WAIT):
        return "REJECTED"
    return STATUS_CODE.get(status or "", status or "REJECTED")


@dataclass
class AlertConfig:
    min_confidence: float = 70
    min_score_separation: float = 8
    cooldown_seconds: int = 300
    max_spread_percent: float = 2.0
    min_option_volume: int = 100
    min_option_oi: int = 500
    min_rr: float = 1.5
    sl_percent: float = 20
    target1_rr: float = 1.5
    target2_rr: float = 2.5
    supertrend_atr_mult: float = 3.0
    time_start: time = time(9, 20)
    time_end: time = time(15, 10)
    nifty_enabled: bool = True
    sensex_enabled: bool = True
    ce_alerts: bool = True
    pe_alerts: bool = True
    notifications_enabled: bool = True
    sound_enabled: bool = False
    browser_notify: bool = False
    atr_chop_ratio: float = 2.0


@dataclass
class ScoreReason:
    name: str
    delta: float
    detail: str
    passed: bool

    def as_dict(self) -> dict:
        return {"name": self.name, "delta": self.delta, "detail": self.detail, "passed": self.passed}


@dataclass
class MarketFacts:
    instrument: str
    clock: time
    stale: bool
    close: float | None
    vwap: float | None
    ema20: float | None
    ema9: float | None
    ema21: float | None
    rsi: float | None
    atr: float | None
    atr_threshold: float | None
    volume: float | None
    volume_ema: float | None
    momentum: float | None
    trend: str
    supertrend: str | None
    support: float | None
    resistance: float | None
    pcr: float | None
    data_time: datetime | None
    now: datetime
    last_buy_at: datetime | None = None
    last_buy_side: str | None = None
    last_buy_strike: float | None = None


@dataclass
class OptionFacts:
    option_type: str
    strike: float | None
    expiry: str | None
    tradingsymbol: str = ""
    ltp: float | None = None
    bid: float | None = None
    ask: float | None = None
    volume: float | None = None
    oi: float | None = None
    oi_change: float | None = None
    iv: float | None = None
    strategy_score: float = 0.0
    strategy_passed: int = 0
    strategy_total: int = 0
    token: int | None = None


@dataclass
class AlertDecision:
    instrument: str
    option_type: str
    signal: str
    status: str
    strike: float | None
    expiry: str | None
    tradingsymbol: str
    entry_price: float | None
    entry_low: float | None
    entry_high: float | None
    stop_loss: float | None
    target1: float | None
    target2: float | None
    risk_reward: float | None
    confidence: float
    reasons: list[ScoreReason]
    blocked: list[str]
    warning: str
    data_time: datetime | None
    signal_time: datetime
    ce_score: float
    pe_score: float
    alert_only: bool = True

    def as_dict(self) -> dict:
        return {
            "instrument": self.instrument,
            "option_type": self.option_type,
            "signal": self.signal,
            "status": self.status,
            "strike": self.strike,
            "expiry": self.expiry,
            "tradingsymbol": self.tradingsymbol,
            "entry_price": self.entry_price,
            "entry_low": self.entry_low,
            "entry_high": self.entry_high,
            "stop_loss": self.stop_loss,
            "target1": self.target1,
            "target2": self.target2,
            "risk_reward": self.risk_reward,
            "confidence": self.confidence,
            "reasons": [row.as_dict() for row in self.reasons],
            "blocked": self.blocked,
            "warning": self.warning,
            "data_time": self.data_time.isoformat() if self.data_time else None,
            "signal_time": self.signal_time.isoformat(),
            "ce_score": self.ce_score,
            "pe_score": self.pe_score,
            "alert_only": True,
            "note": "Analytical signal only. Not a guaranteed prediction. No broker order is placed.",
        }


def in_cooldown(last: datetime | None, now: datetime, seconds: int, same_side: bool) -> bool:
    if last is None or seconds <= 0 or not same_side:
        return False
    return (now - last).total_seconds() < seconds


def scores_too_close(ce_score: float, pe_score: float, min_sep: float, min_conf: float) -> bool:
    return abs(ce_score - pe_score) < min_sep and ce_score >= min_conf and pe_score >= min_conf


def session_open(clock: time, start: time, end: time) -> bool:
    return start <= clock <= end


def spread_percent(bid: float | None, ask: float | None, ltp: float | None) -> float | None:
    if bid is None or ask is None or ask < bid:
        return None
    mid = (bid + ask) / 2 if (bid + ask) else ltp
    if not mid:
        return None
    return (ask - bid) / mid * 100


def levels(entry: float, sl_percent: float, t1_rr: float, t2_rr: float) -> tuple[float, float, float, float]:
    stop = entry * (1 - sl_percent / 100)
    risk = entry - stop
    t1 = entry + risk * t1_rr
    t2 = entry + risk * t2_rr
    rr = t1_rr
    return stop, t1, t2, rr


def score_side(facts: MarketFacts, option: OptionFacts, config: AlertConfig) -> tuple[float, list[ScoreReason], list[str]]:
    reasons: list[ScoreReason] = []
    blocked: list[str] = []
    bullish = option.option_type == "CE"
    wanted = "BULLISH" if bullish else "BEARISH"

    def add(name: str, delta: float, ok: bool, detail: str, required: bool = False) -> None:
        reasons.append(ScoreReason(name, delta if ok else 0.0, detail, ok))
        if required and not ok:
            blocked.append(detail)

    add(
        "Underlying trend",
        18,
        facts.trend == wanted,
        f"Live tape is {facts.trend}; {option.option_type} wants {wanted}.",
        required=True,
    )
    if bullish:
        vwap_ok = facts.close is not None and facts.vwap is not None and facts.close > facts.vwap
        brk = facts.close is not None and facts.resistance is not None and facts.close > facts.resistance
        add("VWAP / resistance", 12, vwap_ok or brk, _rel("Close", facts.close, "VWAP", facts.vwap, ">") + (f" Resistance {facts.resistance}." if facts.resistance else ""), required=True)
        ema_ok = facts.close is not None and facts.ema21 is not None and facts.close > facts.ema21
        stack = facts.ema9 is not None and facts.ema21 is not None and facts.ema9 > facts.ema21
        add("EMA 9/21", 10, ema_ok or stack, _rel("Close", facts.close, "EMA21", facts.ema21, ">") + f" EMA9 {facts.ema9}.", required=True)
        add("EMA stack", 8, stack, f"EMA9 {facts.ema9} vs EMA21 {facts.ema21}.")
        add("Supertrend", 8, facts.supertrend == "BULLISH", f"Supertrend is {facts.supertrend or 'unavailable'}.")
        add("Momentum", 6, facts.momentum is not None and facts.momentum > 0, f"Momentum {facts.momentum}.", required=True)
    else:
        vwap_ok = facts.close is not None and facts.vwap is not None and facts.close < facts.vwap
        brk = facts.close is not None and facts.support is not None and facts.close < facts.support
        add("VWAP / support", 12, vwap_ok or brk, _rel("Close", facts.close, "VWAP", facts.vwap, "<") + (f" Support {facts.support}." if facts.support else ""), required=True)
        ema_ok = facts.close is not None and facts.ema21 is not None and facts.close < facts.ema21
        stack = facts.ema9 is not None and facts.ema21 is not None and facts.ema9 < facts.ema21
        add("EMA 9/21", 10, ema_ok or stack, _rel("Close", facts.close, "EMA21", facts.ema21, "<") + f" EMA9 {facts.ema9}.", required=True)
        add("EMA stack", 8, stack, f"EMA9 {facts.ema9} vs EMA21 {facts.ema21}.")
        add("Supertrend", 8, facts.supertrend == "BEARISH", f"Supertrend is {facts.supertrend or 'unavailable'}.")
        add("Momentum", 6, facts.momentum is not None and facts.momentum < 0, f"Momentum {facts.momentum}.", required=True)

    vol_ok = facts.volume is not None and facts.volume_ema is not None and facts.volume > facts.volume_ema
    add("Volume", 8, vol_ok, f"Volume {facts.volume} vs EMA {facts.volume_ema}.", required=True)

    if bullish:
        rsi_ok = facts.rsi is not None and 45 <= facts.rsi <= 68
    else:
        rsi_ok = facts.rsi is not None and 32 <= facts.rsi <= 55
    add("RSI", 6, rsi_ok, f"RSI {facts.rsi}.")

    strat = min(12.0, (option.strategy_score or 0) * 0.12)
    reasons.append(ScoreReason("Existing CE/PE setup", round(strat, 2), f"Strategy match {option.strategy_passed}/{option.strategy_total} = {option.strategy_score}%.", option.strategy_score >= 50))

    spr = spread_percent(option.bid, option.ask, option.ltp)
    liquid = (
        option.ltp is not None
        and option.volume is not None
        and option.oi is not None
        and option.volume >= config.min_option_volume
        and option.oi >= config.min_option_oi
        and spr is not None
        and spr <= config.max_spread_percent
    )
    add(
        "Liquidity / spread",
        8,
        liquid,
        f"LTP {option.ltp}, vol {option.volume}, OI {option.oi}, spread {None if spr is None else round(spr, 2)}% (max {config.max_spread_percent}%).",
        required=True,
    )
    if spr is not None and spr > config.max_spread_percent:
        blocked.append(f"Option spread {spr:.2f}% is wider than {config.max_spread_percent}%.")
    if option.volume is None or option.oi is None or option.volume < config.min_option_volume or option.oi < config.min_option_oi:
        blocked.append("Option liquidity is insufficient.")

    if option.oi_change is not None:
        reasons.append(
            ScoreReason(
                "Option OI change",
                0,
                f"OI change {option.oi_change}. An OI increase is not proof of long buildup.",
                True,
            )
        )
    if option.iv is not None:
        reasons.append(ScoreReason("IV", 0, f"Implied vol {round(option.iv * 100, 2)}%.", True))
    if facts.pcr is not None:
        reasons.append(ScoreReason("PCR", 0, f"Put-call ratio {round(facts.pcr, 2)}.", True))

    score = round(sum(row.delta for row in reasons), 2)
    return score, reasons, blocked


def decide(facts: MarketFacts, ce: OptionFacts | None, pe: OptionFacts | None, config: AlertConfig) -> list[AlertDecision]:
    warning = ""
    global_block: list[str] = []
    if facts.stale:
        warning = "Market data is delayed or stale."
        global_block.append(warning)
    if not session_open(facts.clock, config.time_start, config.time_end):
        global_block.append("Outside configured trading hours.")
    missing = [name for name, value in (("close", facts.close), ("VWAP", facts.vwap), ("volume", facts.volume)) if value is None]
    if missing:
        global_block.append("Missing " + ", ".join(missing) + ".")
    if facts.trend == "NEUTRAL":
        global_block.append("Market is sideways / unconfirmed.")
    if facts.atr is not None and facts.atr_threshold and facts.atr > facts.atr_threshold * config.atr_chop_ratio and facts.trend == "NEUTRAL":
        global_block.append("Volatility is elevated without trend confirmation.")

    ce_score, ce_reasons, ce_block = score_side(facts, ce, config) if ce else (0.0, [], ["CE candidate unavailable."])
    pe_score, pe_reasons, pe_block = score_side(facts, pe, config) if pe else (0.0, [], ["PE candidate unavailable."])

    if scores_too_close(ce_score, pe_score, config.min_score_separation, config.min_confidence):
        global_block.append(f"CE {ce_score} and PE {pe_score} are too close (min gap {config.min_score_separation}).")

    out = []
    for kind, option, score, reasons, extra in (
        ("CE", ce, ce_score, ce_reasons, ce_block),
        ("PE", pe, pe_score, pe_reasons, pe_block),
    ):
        blocked = list(global_block) + list(extra)
        if kind == "CE" and not config.ce_alerts:
            blocked.append("CE alerts are disabled.")
        if kind == "PE" and not config.pe_alerts:
            blocked.append("PE alerts are disabled.")
        if facts.instrument == "NIFTY" and not config.nifty_enabled:
            blocked.append("NIFTY alerts are disabled.")
        if facts.instrument == "SENSEX" and not config.sensex_enabled:
            blocked.append("SENSEX alerts are disabled.")
        same = facts.last_buy_side == kind and facts.last_buy_strike == (option.strike if option else None)
        if in_cooldown(facts.last_buy_at, facts.now, config.cooldown_seconds, same):
            blocked.append("Duplicate suppressed by cooldown.")
        entry = option.ltp if option else None
        stop = t1 = t2 = rr = None
        if entry is not None and entry > 0:
            stop, t1, t2, rr = levels(entry, config.sl_percent, config.target1_rr, config.target2_rr)
            if rr < config.min_rr:
                blocked.append(f"Risk/reward {rr} is below {config.min_rr}.")
            else:
                reasons = reasons + [ScoreReason("Risk/reward", 4, f"R:R {rr} using SL {config.sl_percent}% / T1 {config.target1_rr}x.", True)]
                score = min(100.0, round(score + 4, 2))
        else:
            blocked.append("Option LTP is unavailable.")
        buy = score >= config.min_confidence and not blocked and option is not None
        out.append(
            AlertDecision(
                instrument=facts.instrument,
                option_type=kind,
                signal=BUY if buy else WAIT,
                status=NEW if buy else WAIT,
                strike=option.strike if option else None,
                expiry=option.expiry if option else None,
                tradingsymbol=(option.tradingsymbol if option else "") or f"{facts.instrument} {kind}",
                entry_price=entry,
                entry_low=round(entry * 0.997, 2) if entry else None,
                entry_high=round(entry * 1.003, 2) if entry else None,
                stop_loss=round(stop, 2) if stop else None,
                target1=round(t1, 2) if t1 else None,
                target2=round(t2, 2) if t2 else None,
                risk_reward=rr,
                confidence=score,
                reasons=reasons,
                blocked=blocked,
                warning=warning,
                data_time=facts.data_time,
                signal_time=facts.now,
                ce_score=ce_score,
                pe_score=pe_score,
            )
        )
    return out


def next_status(status: str, premium: float | None, stop: float | None, t1: float | None, t2: float | None, expired: bool) -> str:
    if status in {STOP_HIT, TARGET2, EXPIRED, CANCELLED}:
        return status
    if expired:
        return EXPIRED
    if premium is None:
        return status
    if stop is not None and premium <= stop:
        return STOP_HIT
    if t2 is not None and premium >= t2:
        return TARGET2
    if t1 is not None and premium >= t1:
        return TARGET1
    if status == NEW:
        return ACTIVE
    return status


def _rel(left: str, left_v, right: str, right_v, op: str) -> str:
    return f"{left} {left_v} {op} {right} {right_v}."
