from __future__ import annotations


def ema(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    k = 2 / (period + 1)
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(closes: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if period <= 0 or len(closes) <= period:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = _rsi_value(avg_gain, avg_loss)
    for i in range(period + 1, len(closes)):
        change = closes[i] - closes[i - 1]
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = _rsi_value(avg_gain, avg_loss)
    return out


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _true_range(high: float, low: float, prev_close: float) -> float:
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def atr(highs: list[float], lows: list[float], closes: list[float], period: int) -> list[float | None]:
    n = len(closes)
    out: list[float | None] = [None] * n
    if period <= 0 or n <= period or len(highs) != n or len(lows) != n:
        return out
    trs = [highs[0] - lows[0]]
    for i in range(1, n):
        trs.append(_true_range(highs[i], lows[i], closes[i - 1]))
    prev = sum(trs[:period]) / period
    out[period - 1] = prev
    for i in range(period, n):
        prev = (prev * (period - 1) + trs[i]) / period
        out[i] = prev
    return out


def adx(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[float | None]:
    """Wilder ADX. Same ATR family as the existing engine. No lookahead."""
    n = len(closes)
    out: list[float | None] = [None] * n
    if period <= 0 or n <= period * 2 or len(highs) != n or len(lows) != n:
        return out
    plus_dm = [0.0]
    minus_dm = [0.0]
    trs = [highs[0] - lows[0]]
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        trs.append(_true_range(highs[i], lows[i], closes[i - 1]))
    atr_s = sum(trs[:period]) / period
    plus_s = sum(plus_dm[:period])
    minus_s = sum(minus_dm[:period])
    dx_s: list[float] = []
    for i in range(period, n):
        if i > period:
            atr_s = (atr_s * (period - 1) + trs[i]) / period
            plus_s = plus_s - (plus_s / period) + plus_dm[i]
            minus_s = minus_s - (minus_s / period) + minus_dm[i]
        if atr_s <= 0:
            continue
        plus_di = 100.0 * plus_s / atr_s
        minus_di = 100.0 * minus_s / atr_s
        denom = plus_di + minus_di
        dx_s.append(0.0 if denom <= 0 else 100.0 * abs(plus_di - minus_di) / denom)
        if len(dx_s) == period:
            out[i] = sum(dx_s) / period
        elif len(dx_s) > period:
            prev = out[i - 1]
            if prev is not None:
                out[i] = (prev * (period - 1) + dx_s[-1]) / period
    return out


def median(values: list[float]) -> float | None:
    ordered = sorted(v for v in values if v is not None)
    if not ordered:
        return None
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def session_vwap(typical: list[float], volumes: list[float], session_keys: list[object]) -> list[float | None]:
    out: list[float | None] = []
    cum_pv = 0.0
    cum_v = 0.0
    current = object()
    for price, volume, key in zip(typical, volumes, session_keys):
        if key != current:
            cum_pv = 0.0
            cum_v = 0.0
            current = key
        cum_pv += price * volume
        cum_v += volume
        out.append(cum_pv / cum_v if cum_v else None)
    return out
