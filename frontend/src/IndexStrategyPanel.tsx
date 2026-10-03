import { Fragment, useMemo, useState } from "react";
import { fmt } from "./lib";

function money(value: unknown, signed = true) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  const text = number.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (!signed) return `₹${text}`;
  const prefix = number > 0 ? "+" : "";
  return `${prefix}₹${text}`;
}

export function IndexStrategyPanel({
  config,
  presets,
  decisions,
  onSave,
}: {
  config: any;
  presets: Record<string, any>;
  decisions: Record<string, any>;
  onSave: (patch: Record<string, unknown>) => void;
}) {
  const strategy = config.index_strategy || {};
  function patch(partial: Record<string, unknown>) {
    onSave({ index_strategy: { ...strategy, ...partial, preset: "CUSTOM" } });
  }
  function loadPreset(name: string) {
    const row = presets[name];
    if (!row) return;
    onSave({ index_strategy: row });
  }
  return (
    <section className="card">
      <h2>Index BUY / SELL strategy</h2>
      <p className="muted">Group scores: Trend 25, Trigger 20, OI 15, 3-bar/EMA9 momentum 10, RelVolume 10, RSI 10, Premium 10. Mandatory gates must all pass. HYPOTHETICAL / PAPER only. No broker orders. Session 09:30–14:45 (expiry buyers 09:30–13:30). Structural stop 0.5–1.2 ATR, 1R book 50%, chandelier/EMA20 trail, flat 15:15 (14:30 expiry).</p>
      <ul className="cond-list" style={{ marginBottom: 12 }}>
        {(Array.isArray(config.entry_validation) ? config.entry_validation : [
          "3m paper entries: 10:00-14:00 IST only (skip the open and late square-off).",
          "BUY/SELL threshold 80. Rising-edge only.",
          "Max 2 index paper trades per day. Cooldown 30 minutes after an entry.",
          "After STOP_LOSS_HIT, no new entry that session.",
          "If session paper P&L reaches -max_daily_loss, halt new entries.",
          "A 3m BUY/SELL is taken only if the last closed 5m bar decide() is the same side. Same EMA/RSI/ATR/VWAP. No lookahead.",
        ]).map((rule: unknown, index: number) => (
          <li key={`${index}-${String(rule)}`} className="pass">✓ {String(rule)}</li>
        ))}
      </ul>
      <div className="row" style={{ marginBottom: 8 }}>
        {["CONSERVATIVE", "BALANCED", "AGGRESSIVE"].map((name) => (
          <button key={name} type="button" className={strategy.preset === name ? "primary" : ""} onClick={() => loadPreset(name)}>{name}</button>
        ))}
        <button type="button" className={strategy.preset === "CUSTOM" ? "primary" : ""} onClick={() => patch({ preset: "CUSTOM", name: strategy.name || "Custom" })}>CUSTOM</button>
      </div>
      <div className="form">
        <label>Strategy name
          <input defaultValue={strategy.name || "Custom"} onBlur={(event) => patch({ name: event.target.value, preset: "CUSTOM" })} />
        </label>
        <label>Enable BUY
          <input type="checkbox" checked={strategy.buy_enabled !== false} onChange={(event) => patch({ buy_enabled: event.target.checked })} />
        </label>
        <label>Enable SELL
          <input type="checkbox" checked={strategy.sell_enabled !== false} onChange={(event) => patch({ sell_enabled: event.target.checked })} />
        </label>
        <label>BUY threshold
          <input type="number" defaultValue={strategy.buy_threshold ?? 80} onBlur={(event) => patch({ buy_threshold: Number(event.target.value) })} />
        </label>
        <label>SELL threshold
          <input type="number" defaultValue={strategy.sell_threshold ?? 80} onBlur={(event) => patch({ sell_threshold: Number(event.target.value) })} />
        </label>
        <label>EMA period
          <input type="number" defaultValue={strategy.ema_period ?? 20} onBlur={(event) => patch({ ema_period: Number(event.target.value) })} />
        </label>
        <label>VWAP
          <input type="checkbox" checked={strategy.vwap_enabled !== false} onChange={(event) => patch({ vwap_enabled: event.target.checked })} />
        </label>
        <label>Volume confirmation
          <input type="checkbox" checked={strategy.volume_enabled !== false} onChange={(event) => patch({ volume_enabled: event.target.checked })} />
        </label>
        <label>Breakout / breakdown
          <input type="checkbox" checked={strategy.breakout_enabled !== false} onChange={(event) => patch({ breakout_enabled: event.target.checked })} />
        </label>
        <label>BUY RSI min
          <input type="number" defaultValue={strategy.buy_rsi_min ?? 45} onBlur={(event) => patch({ buy_rsi_min: Number(event.target.value) })} />
        </label>
        <label>BUY RSI max
          <input type="number" defaultValue={strategy.buy_rsi_max ?? 72} onBlur={(event) => patch({ buy_rsi_max: Number(event.target.value) })} />
        </label>
        <label>SELL RSI min
          <input type="number" defaultValue={strategy.sell_rsi_min ?? 28} onBlur={(event) => patch({ sell_rsi_min: Number(event.target.value) })} />
        </label>
        <label>SELL RSI max
          <input type="number" defaultValue={strategy.sell_rsi_max ?? 55} onBlur={(event) => patch({ sell_rsi_max: Number(event.target.value) })} />
        </label>
        <label>Lots
          <input type="number" defaultValue={strategy.lots ?? 1} onBlur={(event) => patch({ lots: Number(event.target.value) })} />
        </label>
        <label>Stop %
          <input type="number" step="0.05" defaultValue={strategy.stop_percent ?? 0.35} onBlur={(event) => patch({ stop_percent: Number(event.target.value) })} />
        </label>
        <label>Target RR
          <input type="number" step="0.1" defaultValue={strategy.target_rr ?? 1.5} onBlur={(event) => patch({ target_rr: Number(event.target.value) })} />
        </label>
        <label>Trailing stop
          <input type="checkbox" checked={Boolean(strategy.trail_enabled)} onChange={(event) => patch({ trail_enabled: event.target.checked })} />
        </label>
        <label>Max hold (minutes)
          <input type="number" defaultValue={strategy.max_hold_minutes ?? 180} onBlur={(event) => patch({ max_hold_minutes: Number(event.target.value) })} />
        </label>
        <label>Cooldown (minutes)
          <input type="number" defaultValue={strategy.cooldown_minutes ?? 30} onBlur={(event) => patch({ cooldown_minutes: Number(event.target.value) })} />
        </label>
        <label>Max trades / day
          <input type="number" defaultValue={strategy.max_trades_per_day ?? 2} onBlur={(event) => patch({ max_trades_per_day: Number(event.target.value) })} />
        </label>
        <label>Session start
          <input type="time" defaultValue={strategy.session_start || "09:30"} onBlur={(event) => patch({ session_start: event.target.value })} />
        </label>
        <label>Session end
          <input type="time" defaultValue={strategy.session_end || "14:45"} onBlur={(event) => patch({ session_end: event.target.value })} />
        </label>
        <label>Force square-off
          <input type="time" defaultValue={strategy.square_off || "15:15"} onBlur={(event) => patch({ square_off: event.target.value })} />
        </label>
        <label>Stop ATR multiple
          <input type="number" step="0.1" defaultValue={strategy.stop_atr_mult ?? 1} onBlur={(event) => patch({ stop_atr_mult: Number(event.target.value) })} />
        </label>
        <label>Proximity ATR
          <input type="number" step="0.1" defaultValue={strategy.proximity_atr_mult ?? 1.5} onBlur={(event) => patch({ proximity_atr_mult: Number(event.target.value) })} />
        </label>
        <label>ADX min
          <input type="number" defaultValue={strategy.adx_min ?? 18} onBlur={(event) => patch({ adx_min: Number(event.target.value) })} />
        </label>
        <label>Exit variant
          <select defaultValue={strategy.exit_variant || "partial_trail"} onChange={(event) => patch({ exit_variant: event.target.value })}>
            <option value="fixed_rr">fixed 1:1.5</option>
            <option value="partial_trail">partial + trail</option>
            <option value="opposite_signal">opposite signal</option>
          </select>
        </label>
        <label>Cooldown bars
          <input type="number" defaultValue={strategy.cooldown_bars ?? 10} onBlur={(event) => patch({ cooldown_bars: Number(event.target.value) })} />
        </label>
        <label>Slippage (points)
          <input type="number" step="0.1" defaultValue={strategy.slippage_points ?? 0.5} onBlur={(event) => patch({ slippage_points: Number(event.target.value) })} />
        </label>
        <label>Max daily loss
          <input type="number" defaultValue={strategy.max_daily_loss ?? 5000} onBlur={(event) => patch({ max_daily_loss: Number(event.target.value) })} />
        </label>
        <label>No re-entry after stop
          <input type="checkbox" checked={strategy.no_reentry_after_stop !== false} onChange={(event) => patch({ no_reentry_after_stop: event.target.checked, allow_reentry: !event.target.checked })} />
        </label>
        <label>Confirm 3m with 5m
          <input type="checkbox" checked={strategy.confirm_higher_tf !== false} onChange={(event) => patch({ confirm_higher_tf: event.target.checked })} />
        </label>
      </div>
      <p className="muted">Brokerage and taxes use stored Zerodha NFO rates on each paper fill. Slippage is applied in points on entry and exit. Configuration is snapshotted on every historical paper trade.</p>
      <LiveScores decisions={decisions} />
    </section>
  );
}

function LiveScores({ decisions }: { decisions: Record<string, any> }) {
  const rows = decisions && typeof decisions === "object" && !Array.isArray(decisions) ? decisions : {};
  return (
    <div className="form" style={{ marginTop: 12 }}>
      {["NIFTY", "SENSEX"].map((symbol) => {
        const row = rows[symbol] || {};
        return (
          <article key={symbol} className="inner card">
            <h3>{symbol} · {String(row.decision || "HOLD")}</h3>
            <p>BUY {fmt(row.buy_score ?? row.buy?.score)} / {fmt(row.buy?.threshold ?? 80)} · SELL {fmt(row.sell_score ?? row.sell?.score)} / {fmt(row.sell?.threshold ?? 80)}</p>
            <p className="muted">{String(row.reason || "Waiting for candles.")}</p>
            {row.buy?.failed_gate || row.sell?.failed_gate ? (
              <p className="warn">Failed gate: {row.buy?.failed_gate || row.sell?.failed_gate}</p>
            ) : null}
            <ConditionList title="BUY gates" rows={row.buy?.gates} />
            <ConditionList title="BUY conditions" rows={row.buy?.conditions} rejected={row.buy?.rejected} />
            <ConditionList title="SELL gates" rows={row.sell?.gates} />
            <ConditionList title="SELL conditions" rows={row.sell?.conditions} rejected={row.sell?.rejected} />
          </article>
        );
      })}
    </div>
  );
}

function ConditionList({ title, rows, rejected }: { title: string; rows?: any[]; rejected?: string[] }) {
  if (!rows?.length) return null;
  return (
    <div>
      <p className="muted">{title}</p>
      <ul className="cond-list">
        {rows.map((row, index) => (
          <li key={String(row?.name || index)} className={row?.passed ? "pass" : "fail"}>{row?.passed ? "✓" : "✕"} {String(row?.name || "")} · {String(row?.current ?? "")} · {String(row?.required ?? "")}</li>
        ))}
      </ul>
      {rejected?.length ? <p className="muted">Rejected: {Array.isArray(rejected) ? rejected.map(String).join(", ") : String(rejected)}</p> : null}
    </div>
  );
}

export function AutoSafetyBar({ config, status, halted, onSave }: { config: any; status: any; halted: boolean; onSave: (patch: Record<string, unknown>) => void }) {
  return (
    <section className="card">
      <h2>Automatic paper controls</h2>
      <p className="paper">HYPOTHETICAL / PAPER signals only. No broker orders.</p>
      <div className="row">
        <button className="primary" type="button" onClick={() => onSave({ paper_mode: "AUTOMATIC", paper_instrument: "INDEX", enabled: true, auto_paused: false })}>Start automatic</button>
        <button type="button" onClick={() => onSave({ auto_paused: true })}>Pause</button>
        <button type="button" onClick={() => onSave({ enabled: false, auto_paused: false })}>Stop</button>
        <button type="button" onClick={() => onSave({ auto_paused: false, enabled: true, paper_mode: "AUTOMATIC" })}>Reset session</button>
      </div>
      <p>{status.message}</p>
      {config.auto_paused ? <p className="warn">Automatic entries paused. Open positions still managed.</p> : null}
      {halted ? <p className="error">Maximum daily loss lock. New paper entries are blocked.</p> : null}
      <p className="muted">Duplicate-signal cooldown {config.cooldown_minutes} min · max trades {config.max_trades_per_day}/day · stale quotes block entries.</p>
    </section>
  );
}

export function BacktestDashboard({ run, tab, setTab, replay, onReplay }: { run: any; tab: string; setTab: (value: string) => void; replay?: any; onReplay?: (action: string) => void }) {
  const perf = run.performance || {};
  const trades = run.trades || [];
  const [openId, setOpenId] = useState<number | null>(null);
  const shown = useMemo(() => {
    if (tab === "BUY") return trades.filter((row: any) => row.option_type === "BUY" || row.option_type === "CE");
    if (tab === "SELL") return trades.filter((row: any) => row.option_type === "SELL" || row.option_type === "PE");
    if (tab === "NIFTY") return trades.filter((row: any) => row.instrument === "NIFTY");
    if (tab === "SENSEX") return trades.filter((row: any) => row.instrument === "SENSEX");
    return trades;
  }, [tab, trades]);
  return (
    <section className="card">
      <h2>Results dashboard</h2>
      <p className="warn">HISTORICAL PAPER RESULTS — NOT LIVE PERFORMANCE</p>
      <p className="muted">{typeof run.same_candle_rule === "string" ? run.same_candle_rule : ""}</p>
      {(Array.isArray(run.warnings) ? run.warnings : []).map((item: unknown, index: number) => <p key={index} className="warn">{String(item)}</p>)}
      {run.paper_mode === "INDEX_FUTURES_PROXY" || !run.option_data_available ? <p className="warn">Option history unavailable. Paper BUY = long NIFTY/SENSEX, paper SELL = short NIFTY/SENSEX, filled at the index close.</p> : null}
      <div className="row">
        {["Combined", "BUY", "SELL", "NIFTY", "SENSEX"].map((name) => (
          <button key={name} type="button" className={tab === name ? "primary" : ""} onClick={() => setTab(name)}>{name}</button>
        ))}
      </div>
      <p>Trades {perf.total_trades ?? run.total_trades} · Open {perf.open_trades ?? 0} · Closed {perf.closed_trades ?? run.total_trades} · BUY {perf.buy_trades ?? "—"} · SELL {perf.sell_trades ?? "—"}</p>
      <p>Wins {perf.winning_trades ?? run.wins} · Losses {perf.losing_trades ?? run.losses} · Win rate {perf.win_rate == null ? "—" : `${fmt(perf.win_rate)}%`} · Trades/day {perf.trades_per_day ?? "—"}</p>
      <p>NET P&amp;L {money(perf.net_pnl ?? run.net_pnl)} · Gross profit {money(perf.gross_profit)} · Gross loss {money(perf.gross_loss)} · Costs {money(perf.total_costs ?? run.total_charges, false)}</p>
      <p>BUY P&amp;L {money(perf.buy_pnl)} · SELL P&amp;L {money(perf.sell_pnl)} · NIFTY {money(perf.nifty_pnl)} · SENSEX {money(perf.sensex_pnl)}</p>
      <p>Profit factor {perf.profit_factor ?? run.profit_factor ?? "—"} · Avg R {perf.average_r ?? "—"} · Expectancy {perf.expectancy ?? "—"} · Avg win {money(perf.average_profit)} · Avg loss {money(perf.average_loss)} · Largest win {money(perf.largest_win)} · Largest loss {money(perf.largest_loss)}</p>
      {Array.isArray(perf.grid) && perf.grid.length ? <p className="muted">Sweep grid is on Backdated Paper. {perf.grid.length} threshold × exit × split rows.</p> : null}
      <p>Max DD {money(perf.max_drawdown ?? run.max_drawdown)} · Avg hold {perf.average_holding_minutes == null ? "—" : `${fmt(perf.average_holding_minutes)} min`} · Win streak {perf.longest_winning_streak ?? "—"} · Loss streak {perf.longest_losing_streak ?? "—"}</p>
      <p className="muted">{perf.pnl_formula}</p>
      <PnlSpark points={equityPoints(shown)} label="Equity curve" />
      <PnlSpark points={drawdownPoints(shown)} label="Drawdown" stroke="#e07a5f" />
      <BarRow title="Daily P&amp;L" rows={dailyBars(shown)} />
      <BarRow title="Exit reasons" rows={reasonBars(shown)} />
      <BarRow title="BUY vs SELL NET" rows={[{ label: "BUY", value: Number(perf.buy_pnl || 0) }, { label: "SELL", value: Number(perf.sell_pnl || 0) }]} />
      <BarRow title="Strategy score at entry" rows={scoreBars(shown)} />
      {onReplay ? (
        <div className="row" style={{ marginTop: 8 }}>
          <button type="button" className="primary" onClick={() => onReplay(replay?.playing ? "pause" : "play")}>{replay?.playing ? "Pause" : "Play"}</button>
          <button type="button" onClick={() => onReplay("restart")}>Restart</button>
          <button type="button" onClick={() => onReplay("step-candle")}>Step candle</button>
          <button type="button" onClick={() => onReplay("step-trade")}>Step trade</button>
          <select value={replay?.speed || 1} onChange={(event) => onReplay(`speed:${event.target.value}`)}>
            {[1, 2, 5, 10, 20].map((n) => <option key={n} value={n}>{n}x</option>)}
          </select>
          <span className="muted">{replay?.label || "Replay uses this run's candles and trades in order. No future candles."}</span>
        </div>
      ) : null}
      <table>
        <thead>
          <tr>
            <th>ID</th><th>Und</th><th>Side</th><th>Entry</th><th>Exit</th><th>NET</th><th>Reason</th><th>BUY</th><th>SELL</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((row: any, index: number) => (
            <Fragment key={row.id || index}>
              <tr onClick={() => setOpenId(openId === index ? null : index)} style={{ cursor: "pointer" }}>
                <td>{row.id || index + 1}</td>
                <td>{row.instrument}</td>
                <td>{row.option_type}</td>
                <td>{fmt(row.entry)}</td>
                <td>{fmt(row.exit)}</td>
                <td className={row.net_pnl >= 0 ? "pass" : "fail"}>{money(row.net_pnl)}</td>
                <td>{row.exit_reason}</td>
                <td>{fmt(row.buy_score)}</td>
                <td>{fmt(row.sell_score)}</td>
              </tr>
              {openId === index ? (
                <tr>
                  <td colSpan={9}>
                    <p>{typeof row.explanation === "string" ? row.explanation : String(row.explanation || "")}</p>
                    <p className="muted">Qty {row.quantity} · lot {row.lot_size} · slippage {fmt(row.slippage)} · brokerage {money(row.brokerage, false)} · {row.strategy_name}</p>
                    <p className="muted">Entry {row.entry_time || row.time} · Exit {row.exit_time || "—"} · status {row.status}</p>
                    <p className="muted">Indicators {JSON.stringify(row.indicators || {})}</p>
                  </td>
                </tr>
              ) : null}
            </Fragment>
          ))}
        </tbody>
      </table>
      {!shown.length ? <p className="muted">No paper trades in this view.</p> : null}
    </section>
  );
}

function equityPoints(trades: any[]) {
  let running = 0;
  return trades.map((row, index) => {
    running += Number(row.net_pnl || 0);
    return { time: String(index), net_pnl: running };
  });
}

function drawdownPoints(trades: any[]) {
  let running = 0;
  let peak = 0;
  return trades.map((row, index) => {
    running += Number(row.net_pnl || 0);
    peak = Math.max(peak, running);
    return { time: String(index), net_pnl: running - peak };
  });
}

function dailyBars(trades: any[]) {
  const map = new Map<string, number>();
  for (const row of trades) {
    const day = String(row.date || row.entry_time || "").slice(0, 10) || "—";
    map.set(day, (map.get(day) || 0) + Number(row.net_pnl || 0));
  }
  return [...map.entries()].map(([label, value]) => ({ label, value }));
}

function reasonBars(trades: any[]) {
  const map = new Map<string, number>();
  for (const row of trades) {
    const key = String(row.exit_reason || "UNKNOWN");
    map.set(key, (map.get(key) || 0) + 1);
  }
  return [...map.entries()].map(([label, value]) => ({ label, value }));
}

function scoreBars(trades: any[]) {
  const buckets = [0, 0, 0, 0, 0];
  const labels = ["0-20", "20-40", "40-60", "60-80", "80-100"];
  for (const row of trades) {
    const score = Number(row.option_type === "SELL" || row.option_type === "PE" ? row.sell_score : row.buy_score);
    if (!Number.isFinite(score)) continue;
    buckets[Math.min(4, Math.floor(score / 20))] += 1;
  }
  return labels.map((label, index) => ({ label, value: buckets[index] }));
}

function PnlSpark({ points, label = "Equity curve", stroke = "#7dcea0" }: { points: { time: string; net_pnl: number }[]; label?: string; stroke?: string }) {
  if (!points.length) return <p className="muted">{label} appears after closed paper trades.</p>;
  const values = points.map((row) => row.net_pnl);
  const min = Math.min(0, ...values);
  const max = Math.max(0, ...values);
  const span = max - min || 1;
  const width = 520;
  const height = 120;
  const coords = points.map((row, index) => {
    const x = 20 + (index / Math.max(points.length - 1, 1)) * (width - 40);
    const y = 20 + (1 - (row.net_pnl - min) / span) * (height - 40);
    return `${x},${y}`;
  });
  return (
    <svg viewBox={`0 0 ${width} ${height}`} width="100%" height="140" role="img" aria-label={label}>
      <polyline fill="none" stroke={stroke} strokeWidth="2" points={coords.join(" ")} />
      <text x="20" y="14" fill="#a39888" fontSize="11">{label} — HISTORICAL PAPER RESULTS — NOT LIVE PERFORMANCE</text>
    </svg>
  );
}

function BarRow({ title, rows }: { title: string; rows: { label: string; value: number }[] }) {
  if (!rows.length) return null;
  const max = Math.max(1, ...rows.map((row) => Math.abs(row.value)));
  return (
    <div style={{ marginTop: 8 }}>
      <p className="muted">{title}</p>
      {rows.slice(0, 12).map((row) => (
        <p key={row.label} className="muted">
          {row.label} {row.value.toLocaleString("en-IN")}
          <span style={{ display: "inline-block", width: `${Math.max(4, (Math.abs(row.value) / max) * 180)}px`, height: 8, marginLeft: 8, background: row.value >= 0 ? "#2f7d4a" : "#8a4b32", verticalAlign: "middle" }} />
        </p>
      ))}
    </div>
  );
}

