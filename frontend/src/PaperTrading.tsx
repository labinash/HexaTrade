import { Component, useEffect, useState, type ReactNode } from "react";
import { DateField } from "./fields";
import { api } from "./api";
import { fmt, usePoll } from "./lib";
import { AutoSafetyBar, BacktestDashboard, IndexStrategyPanel } from "./IndexStrategyPanel";
import { requestSignalNotifyPermission } from "./notifyPermission";

function money(value: unknown, signed = true) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  const text = number.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (!signed) return `₹${text}`;
  const prefix = number > 0 ? "+" : "";
  return `${prefix}₹${text}`;
}

class PanelGuard extends Component<{ children: ReactNode; resetKey?: string }, { failed: boolean; error: string }> {
  state = { failed: false, error: "" };
  static getDerivedStateFromError(error: Error) {
    return { failed: true, error: String(error?.message || error || "") };
  }
  componentDidCatch(error: Error) {
    this.setState({ failed: true, error: String(error?.message || error || "") });
  }
  componentDidUpdate(prevProps: { resetKey?: string }) {
    if (this.state.failed && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ failed: false, error: "" });
    }
  }
  render() {
    if (this.state.failed) {
      return (
        <p className="error">
          Automatic paper panel failed to render. Manual paper trading is still available. HYPOTHETICAL / PAPER only.
          {this.state.error ? ` ${this.state.error}` : ""}
          {" "}
          <button type="button" onClick={() => this.setState({ failed: false, error: "" })}>Retry panel</button>
        </p>
      );
    }
    return this.props.children;
  }
}

export function PaperTrading() {
  const { data, error } = usePoll<any>("/api/paper-trading", 4000);
  const [detail, setDetail] = useState<any>(null);
  const [message, setMessage] = useState("");
  const [mode, setMode] = useState("");

  useEffect(() => {
    requestSignalNotifyPermission();
  }, []);

  async function save(patch: Record<string, unknown>) {
    setMessage("");
    try {
      await api("/api/paper-trading/settings", { method: "PUT", body: JSON.stringify(patch) });
      setMessage("Saved. PAPER TRADING — NO REAL ORDERS.");
    } catch (err: any) {
      setMessage(err.message);
    }
  }

  async function openDetail(id: number) {
    const row = await api<any>(`/api/paper-trading/trades/${id}`);
    setDetail(row);
  }

  if (!data && error) return <p className="error">{error}</p>;
  if (!data) return <p>Loading paper trading…</p>;
  const summary = data.summary || {};
  const config = data.config || {};
  const status = data.status || {};
  const activeMode = mode || config.paper_mode || "MANUAL";

  return (
    <>
      <h1>Paper Trading</h1>
      <p className="paper"><strong>HYPOTHETICAL / PAPER signals only. No broker orders.</strong></p>
      <p className="muted">Paper can trade CE/PE options or the index future itself (BUY/SELL). Index auto-paper uses 3-minute candles with the same validation as the chart. Scores are transparent and not a profit forecast.</p>
      {error ? <p className="warn">{error} The last loaded paper data stays on screen.</p> : null}
      <section className="card">
        <h2>Mode</h2>
        <div className="row">
          {[
            ["MANUAL", "Manual Paper Trading"],
            ["AUTOMATIC", "Automatic Paper Trading"],
            ["HISTORICAL_BACKTEST", "Historical Backtest"],
            ["HISTORICAL_REPLAY", "Historical Replay"],
          ].map(([value, label]) => (
            <button key={value} type="button" className={activeMode === value ? "primary" : ""} onClick={() => { setMode(value); save({ paper_mode: value, paper_instrument: value === "MANUAL" ? config.paper_instrument : "INDEX" }); }}>{label}</button>
          ))}
        </div>
      </section>

      <section className="card">
        <h2>Today</h2>
        <p className={(summary.net_pnl ?? 0) >= 0 ? "pass" : "fail"} style={{ fontSize: 22 }}>
          NET P&amp;L {money(summary.net_pnl)}
        </p>
        <p>Trades {summary.trades ?? 0} · Open {summary.open ?? 0} · Closed {summary.closed ?? 0}</p>
        <p>Gross P&amp;L {money(summary.gross_pnl)} · Charges {money(summary.charges, false)}</p>
        <p>Win rate {summary.win_rate == null ? "—" : `${fmt(summary.win_rate)}%`}</p>
        {data.halted ? <p className="error">PAPER TRADING HALTED. Daily loss limit reached. Net P&amp;L: {money(summary.net_pnl)}</p> : null}
        <PnlChart points={data.curve || []} />
      </section>

      {activeMode === "AUTOMATIC" ? (
        <PanelGuard resetKey={activeMode}>
          <AutoSafetyBar config={config} status={status} halted={Boolean(data.halted)} onSave={save} />
        </PanelGuard>
      ) : null}
      <PanelGuard resetKey={`${activeMode}:${(data.live_decisions?.NIFTY?.decision) || ""}:${(data.live_decisions?.SENSEX?.decision) || ""}`}>
        <IndexStrategyPanel config={config} presets={data.index_presets || {}} decisions={data.live_decisions || {}} onSave={save} />
      </PanelGuard>

      {activeMode === "MANUAL" ? <IndexFuturesPaper quotes={data.index_quotes || {}} onMessage={setMessage} /> : (
        <section className="card">
          <h2>Index futures</h2>
          <p className="muted">Live LTP remains visible. Manual BUY/SELL is available in Manual Paper Trading mode.</p>
          {["NIFTY", "SENSEX"].map((symbol) => {
            const quote = (data.index_quotes || {})[symbol] || {};
            return <p key={symbol}>{symbol} {quote.tradingsymbol || ""} · LTP {quote.ltp == null ? "—" : `₹${fmt(quote.ltp)}`} · Lot {quote.lot_size ?? "—"} · Exp {quote.expiry || "—"}</p>;
          })}
        </section>
      )}

      <HistoricalRangePaper coverage={data.historical?.coverage || []} config={config} mode={activeMode} />

      <section className="card">
        <h2>Settings</h2>
        <p className="muted">Auto paper trade default is OFF. Enabling it still never sends a live order. Index BUY/SELL uses 3-minute candles, 10:00–14:00 IST, score 80, max 2/day, 30 min cooldown, no re-entry after a stop, and 5-minute confirmation.</p>
        <div className="form">
          <label>Auto Paper Trade
            <input type="checkbox" checked={Boolean(config.enabled)} onChange={(event) => save({ enabled: event.target.checked })} />
          </label>
          <label>Paper instrument
            <select value={config.paper_instrument === "INDEX" ? "INDEX" : "OPTIONS"} onChange={(event) => save({ paper_instrument: event.target.value })}>
              <option value="OPTIONS">Options (CE/PE BUY)</option>
              <option value="INDEX">Index futures (NIFTY/SENSEX BUY &amp; SELL)</option>
            </select>
          </label>
          <label>{config.paper_instrument === "INDEX" ? "Index BUY (long)" : "CE Strategy"}
            <input type="checkbox" checked={config.ce_strategy_enabled !== false} onChange={(event) => save({ ce_strategy_enabled: event.target.checked })} />
          </label>
          <label>{config.paper_instrument === "INDEX" ? "Index SELL (short)" : "PE Strategy"}
            <input type="checkbox" checked={config.pe_strategy_enabled !== false} onChange={(event) => save({ pe_strategy_enabled: event.target.checked })} />
          </label>
          <label>Minimum Signal Score %
            <input type="number" defaultValue={config.min_signal_score} onBlur={(event) => save({ min_signal_score: Number(event.target.value) })} />
          </label>
          {config.paper_instrument === "INDEX" ? null : (
            <label>Candidate Strike Range (ATM ±)
              <input type="number" defaultValue={config.candidate_strike_range} onBlur={(event) => save({ candidate_strike_range: Number(event.target.value) })} />
            </label>
          )}
          <label>Max Open Paper Positions
            <input type="number" defaultValue={config.max_open_positions} onBlur={(event) => save({ max_open_positions: Number(event.target.value) })} />
          </label>
          <label>Max Trades Per Day
            <input type="number" defaultValue={config.max_trades_per_day} onBlur={(event) => save({ max_trades_per_day: Number(event.target.value) })} />
          </label>
          <label>Max Daily Loss
            <input type="number" defaultValue={config.max_daily_loss} onBlur={(event) => save({ max_daily_loss: Number(event.target.value) })} />
          </label>
          <label>Max Loss Per Trade
            <input type="number" defaultValue={config.max_loss_per_trade} onBlur={(event) => save({ max_loss_per_trade: Number(event.target.value) })} />
          </label>
          <label>Auto Square-Off
            <input type="time" defaultValue={config.square_off} onBlur={(event) => save({ square_off: event.target.value })} />
          </label>
          <label>Re-entry Cooldown (minutes)
            <input type="number" defaultValue={config.cooldown_minutes} onBlur={(event) => save({ cooldown_minutes: Number(event.target.value) })} />
          </label>
          <label>Allow Re-entry
            <input type="checkbox" checked={Boolean(config.allow_reentry)} onChange={(event) => save({ allow_reentry: event.target.checked })} />
          </label>
          <label>Lots
            <input type="number" defaultValue={config.lots} onBlur={(event) => save({ lots: Number(event.target.value) })} />
          </label>
          <label>Entry Price Source
            <select defaultValue={config.entry_price_source} onChange={(event) => save({ entry_price_source: event.target.value })}>
              <option>LTP</option>
              <option>ASK</option>
              <option>MID</option>
            </select>
          </label>
        </div>
        <p className="muted">{status.message} Threshold {status.threshold}%. Open {status.open_positions}/{status.max_open_positions}.</p>
        <p className="muted">{message}</p>
      </section>

      <section className="card">
        <h2>Open positions</h2>
        {(data.trades || []).filter((row: any) => row.status === "OPEN").map((row: any) => (
          <article key={row.id} className="inner card" onClick={() => openDetail(row.id)} style={{ cursor: "pointer" }}>
            <h3>{row.instrument_kind === "INDEX" ? `${row.underlying} ${row.option_type}` : `${row.underlying} ${fmt(row.strike)} ${row.option_type}`}</h3>
            <p>{row.instrument_kind === "INDEX" ? row.option_type : "BUY"} · {row.lots} lot · Qty {row.quantity}</p>
            <p>Entry ₹{fmt(row.entry_price)} · Current ₹{fmt(row.current)}</p>
            <p>Gross P&amp;L {money(row.gross_pnl)}</p>
            <p>Estimated Charges −₹{fmt(row.total_charges)}</p>
            <p className={row.net_pnl >= 0 ? "pass" : "fail"}><strong>Est. Net P&amp;L {money(row.net_pnl)}</strong></p>
            <p className="muted">Signal {fmt(row.signal_percent)}% · {row.passed}/{row.total} · OPEN</p>
          </article>
        ))}
        {(data.trades || []).every((row: any) => row.status !== "OPEN") ? <p className="muted">No open paper positions.</p> : null}
      </section>

      <h2>Today's paper trades</h2>
      <table>
        <thead>
          <tr>
            <th className="left">Time</th><th>Und</th><th>Side</th><th>Strike</th><th>Expiry</th>
            <th>Qty</th><th>Entry</th><th>Exit</th><th>Gross</th><th>Charges</th><th>NET</th><th>Signal</th><th>Reason</th><th>Status</th>
          </tr>
        </thead>
        <tbody>
          {(data.trades || []).map((row: any) => (
            <tr key={row.id} onClick={() => openDetail(row.id)} style={{ cursor: "pointer" }}>
              <td className="left">{String(row.entry_time || "").slice(11, 16)}</td>
              <td>{row.underlying}</td>
              <td>{row.option_type}</td>
              <td>{fmt(row.strike)}</td>
              <td>{row.expiry || "—"}</td>
              <td>{row.quantity}</td>
              <td>{fmt(row.entry_price)}</td>
              <td>{fmt(row.exit_price)}</td>
              <td className={row.gross_pnl >= 0 ? "pass" : "fail"}>{money(row.gross_pnl)}</td>
              <td>{fmt(row.total_charges)}</td>
              <td className={row.net_pnl >= 0 ? "pass" : "fail"}>{money(row.net_pnl)}</td>
              <td>{fmt(row.signal_percent)}%</td>
              <td>{row.exit_reason || "—"}</td>
              <td>{row.status}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>Auto trade log</h2>
      <ul className="cond-list">
        {(data.events || []).map((row: any) => (
          <li key={row.id} className="muted">{String(row.time || "").slice(11, 19)} {row.message}</li>
        ))}
      </ul>

      {detail ? (
        <div className="modal" onClick={() => setDetail(null)}>
          <section className="card" onClick={(event) => event.stopPropagation()}>
            <h2>Trade details</h2>
            <p className="paper">PAPER TRADING — NO REAL ORDERS</p>
            <p><strong>{detail.instrument_kind === "INDEX" ? `${detail.underlying} ${detail.option_type}` : `${detail.underlying} ${fmt(detail.strike)} ${detail.option_type}`}</strong></p>
            <p>Signal {fmt(detail.signal_percent)}% · {detail.passed}/{detail.total}</p>
            <p>Entry ₹{fmt(detail.entry_price)} · Exit ₹{fmt(detail.exit_price)} · Qty {detail.quantity}</p>
            <p>Gross P&amp;L {money(detail.gross_pnl)}</p>
            <p>{detail.charges_estimated ? "Estimated charges" : "Actual charges"}</p>
            <table>
              <tbody>
                {Object.entries(detail.charges || {}).filter(([key]) => key !== "estimated").map(([key, value]) => (
                  <tr key={key}><td className="left">{key}</td><td>{fmt(value)}</td></tr>
                ))}
              </tbody>
            </table>
            <p className={detail.net_pnl >= 0 ? "pass" : "fail"}><strong>NET {detail.status === "OPEN" ? "estimated" : "actual"} P&amp;L {money(detail.net_pnl)}</strong></p>
            <p>Exit reason {detail.exit_reason || "—"}</p>
            <ul className="cond-list">
              {(detail.conditions || []).map((row: any) => (
                <li key={row.name} className={row.passed ? "pass" : "fail"}>{row.passed ? "✓" : "✕"} {row.name} · {row.current} · {row.required}</li>
              ))}
            </ul>
            <button type="button" onClick={() => setDetail(null)}>Close</button>
          </section>
        </div>
      ) : null}
    </>
  );
}

function IndexFuturesPaper({ quotes, onMessage }: { quotes: Record<string, any>; onMessage: (text: string) => void }) {
  const [busy, setBusy] = useState("");
  const [local, setLocal] = useState("");

  async function submit(instrument: string, side: "BUY" | "SELL") {
    setBusy(`${instrument}-${side}`);
    setLocal("");
    onMessage("");
    try {
      const result = await api<any>("/api/paper-trading/index", {
        method: "POST",
        body: JSON.stringify({ instrument, side }),
      });
      const text = `Paper ${side} ${instrument} @ ₹${fmt(result.entry_price)} · qty ${result.quantity}. PAPER — NO REAL ORDER.`;
      setLocal(text);
      onMessage(text);
    } catch (err: any) {
      setLocal(err.message);
      onMessage(err.message);
    } finally {
      setBusy("");
    }
  }

  return (
    <section className="card">
      <h2>Index futures paper</h2>
      <p className="muted">BUY or SELL NIFTY / SENSEX at the live front-month futures LTP. Lot size comes from the instrument dump. Not option premium. Never a broker order.</p>
      <div className="form">
        {["NIFTY", "SENSEX"].map((symbol) => {
          const quote = quotes[symbol] || {};
          return (
            <div key={symbol} className="inner card">
              <h3>{symbol}</h3>
              <p>{quote.tradingsymbol || "Front-month future"} · LTP {quote.ltp == null ? "—" : `₹${fmt(quote.ltp)}`} · Lot {quote.lot_size ?? "—"}</p>
              {quote.expiry ? <p className="muted">Expiry {quote.expiry}</p> : null}
              {quote.reason && !quote.available ? <p className="warn">{quote.reason}</p> : null}
              <div className="row" style={{ marginTop: 8 }}>
                <button className="primary" type="button" disabled={Boolean(busy)} onClick={() => submit(symbol, "BUY")}>
                  {busy === `${symbol}-BUY` ? "…" : `Paper BUY ${symbol}`}
                </button>
                <button type="button" disabled={Boolean(busy)} onClick={() => submit(symbol, "SELL")}>
                  {busy === `${symbol}-SELL` ? "…" : `Paper SELL ${symbol}`}
                </button>
              </div>
            </div>
          );
        })}
      </div>
      {local ? <p style={{ marginTop: 12 }}>{local}</p> : null}
    </section>
  );
}

function HistoricalRangePaper({ coverage, config, mode }: { coverage: any[]; config: any; mode?: string }) {
  const [instrument, setInstrument] = useState("NIFTY");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [message, setMessage] = useState("");
  const [runId, setRunId] = useState<number | null>(null);
  const [tab, setTab] = useState("Combined");
  const [replay, setReplay] = useState({ playing: false, speed: 1, cursor: 0, label: "" });
  const { data: runPoll } = usePoll<any>(runId ? `/api/historical-paper/runs/${runId}` : "", 2000);
  const run = runId && runPoll && runPoll.id === runId ? runPoll : null;
  const cover = instrument === "BOTH"
    ? { available: coverage.some((row) => row.instrument === "NIFTY" && row.available) && coverage.some((row) => row.instrument === "SENSEX" && row.available), from_date: coverage.find((row) => row.instrument === "NIFTY")?.from_date, to_date: coverage.find((row) => row.instrument === "NIFTY")?.to_date, trading_days: coverage.find((row) => row.instrument === "NIFTY")?.trading_days, candles: coverage.find((row) => row.instrument === "NIFTY")?.candles, timeframe: "1Minute" }
    : coverage.find((row) => row.instrument === instrument);
  const storedFrom = cover?.from_date || "";
  const storedTo = cover?.to_date || "";

  useEffect(() => {
    if (!storedFrom || !storedTo) return;
    setFromDate((current) => current || storedFrom);
    setToDate((current) => current || storedTo);
  }, [storedFrom, storedTo]);

  useEffect(() => {
    const series = run?.performance?.score_series || [];
    const total = series.length || run?.trades?.length || 0;
    if (!replay.playing || !total) return;
    const id = window.setInterval(() => {
      setReplay((current) => {
        const next = Math.min(current.cursor + 1, total);
        const sample = series[next - 1];
        const label = sample
          ? `${sample.t} · candle ${sample.n}/${sample.total} · BUY ${sample.buy} · SELL ${sample.sell} · ${sample.decision}`
          : `Trade ${next}/${total}`;
        if (next >= total) return { ...current, playing: false, cursor: next, label };
        return { ...current, cursor: next, label };
      });
    }, Math.max(80, 800 / replay.speed));
    return () => window.clearInterval(id);
  }, [replay.playing, replay.speed, run?.trades?.length, run?.performance?.score_series?.length]);

  async function start() {
    setMessage("");
    try {
      const result = await api<any>("/api/historical-paper/runs", {
        method: "POST",
        body: JSON.stringify({
          instrument,
          date_from: fromDate || storedFrom,
          date_to: toDate || storedTo,
          threshold: config.index_strategy?.buy_threshold ?? config.min_signal_score ?? 80,
          strike_range: config.candidate_strike_range ?? 5,
          timeframe: "3Minute",
          strategy: config.index_strategy || {},
        }),
      });
      setRunId(result.id);
      setReplay({ playing: false, speed: 1, cursor: 0, label: "" });
      setMessage(result.status === "QUEUED" ? "Paper backtest queued. HYPOTHETICAL / PAPER only." : result.status);
    } catch (err: any) {
      setMessage(err.message);
    }
  }

  function onReplay(action: string) {
    const series = run?.performance?.score_series || [];
    const tradeTotal = run?.trades?.length || 0;
    const seriesTotal = series.length;
    if (action === "play") setReplay((current) => ({ ...current, playing: true }));
    if (action === "pause") setReplay((current) => ({ ...current, playing: false }));
    if (action === "restart") setReplay({ playing: false, speed: replay.speed, cursor: 0, label: "Restarted. Decisions appear only as the playhead reaches each candle." });
    if (action === "step-candle") {
      setReplay((current) => {
        const next = Math.min(current.cursor + 1, seriesTotal || tradeTotal);
        const sample = series[next - 1];
        return {
          ...current,
          playing: false,
          cursor: next,
          label: sample
            ? `${sample.t} · candle ${sample.n}/${sample.total} · BUY ${sample.buy} · SELL ${sample.sell} · ${sample.decision}`
            : `Step ${next}`,
        };
      });
    }
    if (action === "step-trade") {
      setReplay((current) => {
        const next = Math.min(current.cursor + 1, tradeTotal);
        const row = run?.trades?.[next - 1];
        return {
          ...current,
          playing: false,
          cursor: next,
          label: row ? `Trade ${next}/${tradeTotal} ${row.instrument} ${row.option_type} NET ${row.net_pnl}` : `Trade ${next}/${tradeTotal}`,
        };
      });
    }
    if (action.startsWith("speed:")) setReplay((current) => ({ ...current, speed: Number(action.slice(6)) || 1 }));
  }

  const replayTrades = (() => {
    if (!run || mode !== "HISTORICAL_REPLAY" || run.status !== "COMPLETED") return run?.trades;
    const series = run.performance?.score_series || [];
    if (series.length) {
      const sample = series[Math.max(replay.cursor - 1, 0)];
      const cutoff = sample?.t;
      if (!cutoff) return [];
      return (run.trades || []).filter((row: any) => {
        const stamp = String(row.entry_time || `${row.date} ${row.time}`).replace("T", " ").slice(0, 16);
        return stamp <= cutoff;
      });
    }
    return (run.trades || []).slice(0, Math.max(replay.cursor, 0));
  })();
  const replayRun = run && mode === "HISTORICAL_REPLAY" && run.status === "COMPLETED"
    ? { ...run, trades: replayTrades || [] }
    : run;

  return (
    <section className="card">
      <h2>{mode === "HISTORICAL_REPLAY" ? "Historical replay" : "Paper trade on historical range"}</h2>
      <p className="muted">Engine walks 3-minute index bars built from stored 1-minute candles. Same 3m validation as live paper and the chart. Signals fire at candle close. No future bar is used. When CE/PE history is missing, paper BUY/SELL is on NIFTY and SENSEX themselves.</p>
      {cover?.available ? (
        <p className="muted">Stored {instrument}: {storedFrom} → {storedTo} · {cover.trading_days} sessions · {cover.candles} candles</p>
      ) : (
        <p className="warn">No stored historical candles for {instrument}. Import data on Historical Data first.</p>
      )}
      <div className="form">
        <label>Instrument
          <select value={instrument} onChange={(event) => { setInstrument(event.target.value); setFromDate(""); setToDate(""); }}>
            <option>NIFTY</option>
            <option>SENSEX</option>
            <option value="BOTH">BOTH</option>
          </select>
        </label>
        <label>From Date
          <DateField min={storedFrom} max={storedTo} value={fromDate} onChange={setFromDate} />
        </label>
        <label>To Date
          <DateField min={storedFrom} max={storedTo} value={toDate} onChange={setToDate} />
        </label>
      </div>
      <div className="row" style={{ marginTop: 12 }}>
        <button type="button" disabled={!cover?.available} onClick={() => { setFromDate(storedFrom); setToDate(storedTo); }}>Use full stored range</button>
        <button className="primary" disabled={!fromDate || !toDate} onClick={start}>Run paper on this range</button>
      </div>
      {message ? <p>{message}</p> : null}
      {run?.status === "QUEUED" || run?.status === "RUNNING" ? <p>Replay {run.status}…</p> : null}
      {run?.status === "FAILED" ? <p className="error">{run.last_error}</p> : null}
      {run?.status === "COMPLETED" ? (
        <BacktestDashboard run={replayRun} tab={tab} setTab={setTab} replay={replay} onReplay={mode === "HISTORICAL_REPLAY" ? onReplay : undefined} />
      ) : null}
    </section>
  );
}

function PnlChart({ points }: { points: { time: string; net_pnl: number }[] }) {
  if (!points.length) return <p className="muted">Cumulative NET P&amp;L appears after the first closed paper trade.</p>;
  const values = points.map((row) => row.net_pnl);
  const min = Math.min(0, ...values);
  const max = Math.max(0, ...values);
  const span = max - min || 1;
  const width = 520;
  const height = 140;
  const coords = points.map((row, index) => {
    const x = 20 + (index / Math.max(points.length - 1, 1)) * (width - 40);
    const y = 20 + (1 - (row.net_pnl - min) / span) * (height - 40);
    return `${x},${y}`;
  });
  return (
    <svg viewBox={`0 0 ${width} ${height}`} width="100%" height="160" role="img" aria-label="Cumulative net P&L">
      <polyline fill="none" stroke="#7dcea0" strokeWidth="2" points={coords.join(" ")} />
      {points.map((row, index) => {
        const [x, y] = coords[index].split(",");
        return <circle key={row.time} cx={x} cy={y} r="3" fill="#7dcea0" />;
      })}
      <text x="20" y="14" fill="#a39888" fontSize="11">Cumulative NET P&amp;L after charges</text>
    </svg>
  );
}
