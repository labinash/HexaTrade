from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.candles import Bar
from app.domain.exits import ExitContext, ExitPolicyParams, PositionState, advance_trail, choose_exit, initial_levels
from app.domain.paper import slippage_price, trade_charges
from app.domain.strategy import StrategyParams, evaluate_snapshot, snapshot_from_bars
from app.core.clock import to_ist


@dataclass
class SimTrade:
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    quantity: int
    gross_pnl: float
    net_pnl: float
    exit_reason: str
    holding_minutes: float


@dataclass
class BacktestResult:
    trades: list[SimTrade]
    trade_count: int
    wins: int
    losses: int
    gross_pnl: float
    net_pnl: float
    max_drawdown: float
    average_win: float
    average_loss: float
    win_rate: float
    profit_factor: float | None
    max_consecutive_losses: int
    average_holding_minutes: float
    option_pnl_available: bool
    note: str


def run_backtest(
    bars: list[Bar],
    params: StrategyParams,
    exit_policy: ExitPolicyParams,
    *,
    atr_threshold: float | None,
    slippage_points: float,
    brokerage_per_order: float,
    statutory_bps: float,
    quantity: int,
    option_bars: list[Bar] | None = None,
) -> BacktestResult:
    """Signals use only bars up to the decision bar. Fills use the next bar's open."""
    trades: list[SimTrade] = []
    open_trade: dict | None = None
    option_pnl = option_bars is not None and len(option_bars) == len(bars)
    note = ""
    if option_bars is None:
        note = "Option premium history was not stored for this range. Analysis and P&L use NIFTY/SENSEX index closes (BUY = long index). HYPOTHETICAL / PAPER only."
    elif not option_pnl:
        note = "Option candles do not cover every underlying bar. Analysis uses the NIFTY/SENSEX index series instead."

    for i in range(len(bars) - 1):
        visible = bars[: i + 1]
        evaluation = evaluate_snapshot(snapshot_from_bars(visible, params), params, atr_threshold, live=False)
        nxt = bars[i + 1]
        fill_source = option_bars[i + 1] if option_pnl else nxt
        if open_trade is None and evaluation.passed:
            entry = slippage_price("BUY", fill_source.open, slippage_points)
            stop, target = initial_levels(entry, evaluation.atr, exit_policy)
            open_trade = {
                "entry_time": nxt.start,
                "entry_price": entry,
                "entry_index": i + 1,
                "stop": stop,
                "target": target,
                "trail": None,
                "atr": evaluation.atr,
                "mfe": entry,
            }
            continue
        if open_trade is None:
            continue
        mark = fill_source.close
        state = PositionState(
            entry_price=open_trade["entry_price"],
            entry_time=open_trade["entry_time"],
            stop_loss=open_trade["stop"],
            target=open_trade["target"],
            trail_price=open_trade["trail"],
            atr_at_entry=open_trade["atr"],
            mfe_price=open_trade["mfe"],
        )
        state.trail_price = advance_trail(state, mark, exit_policy, evaluation.atr)
        open_trade["trail"] = state.trail_price
        open_trade["mfe"] = mark if state.mfe_price is None else max(state.mfe_price, mark)
        clock = to_ist(nxt.start).time().replace(second=0, microsecond=0)
        reason = choose_exit(
            state,
            ExitContext(
                premium=mark,
                now=nxt.start,
                clock=clock,
                close=evaluation.close,
                ema=evaluation.ema,
                vwap=evaluation.vwap,
                rsi=evaluation.rsi,
                rsi_min=params.rsi_min,
                live_atr=evaluation.atr,
                kill_switch=False,
                daily_loss_hit=False,
                data_stale=False,
            ),
            exit_policy,
        )
        last_bar = i + 1 == len(bars) - 1
        if reason or last_bar:
            exit_price = slippage_price("SELL", fill_source.close, slippage_points)
            gross = (exit_price - open_trade["entry_price"]) * quantity
            charges = trade_charges(open_trade["entry_price"], quantity, brokerage_per_order, statutory_bps, sides=1)
            charges += trade_charges(exit_price, quantity, brokerage_per_order, statutory_bps, sides=1)
            holding = (nxt.start - open_trade["entry_time"]).total_seconds() / 60
            trades.append(
                SimTrade(
                    entry_time=open_trade["entry_time"],
                    exit_time=nxt.start,
                    entry_price=open_trade["entry_price"],
                    exit_price=exit_price,
                    quantity=quantity,
                    gross_pnl=gross,
                    net_pnl=gross - charges,
                    exit_reason=reason or "END_OF_DATA",
                    holding_minutes=max(holding, 0),
                )
            )
            open_trade = None

    return _summarize(trades, option_pnl, note)


def _summarize(trades: list[SimTrade], option_pnl: bool, note: str) -> BacktestResult:
    wins = [trade for trade in trades if trade.net_pnl > 0]
    losses = [trade for trade in trades if trade.net_pnl <= 0]
    gross = sum(trade.gross_pnl for trade in trades)
    net = sum(trade.net_pnl for trade in trades)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    streak = 0
    worst_streak = 0
    for trade in trades:
        equity += trade.net_pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        if trade.net_pnl <= 0:
            streak += 1
            worst_streak = max(worst_streak, streak)
        else:
            streak = 0
    gross_win = sum(trade.net_pnl for trade in wins)
    gross_loss = abs(sum(trade.net_pnl for trade in losses))
    count = len(trades)
    return BacktestResult(
        trades=trades,
        trade_count=count,
        wins=len(wins),
        losses=len(losses),
        gross_pnl=gross,
        net_pnl=net,
        max_drawdown=max_dd,
        average_win=(gross_win / len(wins)) if wins else 0,
        average_loss=(sum(trade.net_pnl for trade in losses) / len(losses)) if losses else 0,
        win_rate=(len(wins) / count) if count else 0,
        profit_factor=(gross_win / gross_loss) if gross_loss else None,
        max_consecutive_losses=worst_streak,
        average_holding_minutes=(sum(trade.holding_minutes for trade in trades) / count) if count else 0,
        option_pnl_available=option_pnl,
        note=note,
    )


def coverage_gaps(start: datetime, end: datetime, bars: list[Bar], minutes: int) -> bool:
    if not bars:
        return True
    if bars[0].start > start or bars[-1].start < end - timedelta(minutes=minutes):
        return True
    for prev, nxt in zip(bars, bars[1:]):
        delta = (nxt.start - prev.start).total_seconds() / 60
        if delta > minutes:
            return True
    return False
