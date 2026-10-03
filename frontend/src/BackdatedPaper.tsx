import { useEffect, useState } from "react";
import { DateField } from "./fields";
import { api } from "./api";
import { fmt, usePoll } from "./lib";

function money(value: unknown) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  const text = number.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const prefix = number > 0 ? "+" : "";
  return `${prefix}₹${text}`;
}

export function BackdatedPaper() {
  const { data, error } = usePoll<any>("/api/historical-paper", 3000);
  const [instrument, setInstrument] = useState("NIFTY");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [threshold, setThreshold] = useState(75);
  const [capital, setCapital] = useState(100000);
  const [range, setRange] = useState(5);
  const [message, setMessage] = useState("");
  const [runId, setRunId] = useState<number | null>(null);
  const { data: runPoll } = usePoll<any>(runId ? `/api/historical-paper/runs/${runId}` : "/api/historical-paper", 1500);
  const run = runId && runPoll && runPoll.id === runId ? runPoll : null;
  const cover = instrument === "BOTH"
    ? {
        available: (data?.coverage || []).some((row: any) => row.instrument === "NIFTY" && row.available)
          && (data?.coverage || []).some((row: any) => row.instrument === "SENSEX" && row.available),
        from_date: (data?.coverage || []).find((row: any) => row.instrument === "NIFTY")?.from_date,
        to_date: (data?.coverage || []).find((row: any) => row.instrument === "NIFTY")?.to_date,
        trading_days: (data?.coverage || []).find((row: any) => row.instrument === "NIFTY")?.trading_days,
        candles: (data?.coverage || []).find((row: any) => row.instrument === "NIFTY")?.candles,
        timeframe: "1Minute",
      }
    : (data?.coverage || []).find((row: any) => row.instrument === instrument);
  const storedFrom = cover?.from_date || "";
  const storedTo = cover?.to_date || "";

  useEffect(() => {
    if (!storedFrom || !storedTo) return;
    setFromDate((current) => current || storedFrom);
    setToDate((current) => current || storedTo);
  }, [storedFrom, storedTo]);

  function applyFullRange() {
    if (!storedFrom || !storedTo) return;
    setFromDate(storedFrom);
    setToDate(storedTo);
  }

  async function startSweep() {
    setMessage("");
    try {
      const result = await api<any>("/api/historical-paper/runs", {
        method: "POST",
        body: JSON.stringify({
          instrument,
          date_from: "2023-10-03",
          date_to: "2026-10-01",
          threshold,
          initial_capital: capital,
          strike_range: range,
          timeframe: "3Minute",
          strategy: {},
          sweep: {
            thresholds: [65, 70, 75, 80, 85],
            exit_variants: ["fixed_rr", "partial_trail", "opposite_signal"],
            train_from: "2023-10-03",
            train_to: "2025-08-31",
            test_from: "2025-09-01",
            test_to: "2026-10-01",
          },
        }),
      });
      setRunId(result.id);
      setMessage("Sweep queued. Train 2023-10-03→2025-08-31, test 2025-09-01→2026-10-01. HYPOTHETICAL / PAPER only.");
    } catch (err: any) {
      setMessage(err.message);
    }
  }

  async function start() {
    setMessage("");
    try {
      const result = await api<any>("/api/historical-paper/runs", {
        method: "POST",
        body: JSON.stringify({
          instrument,
          date_from: fromDate || storedFrom,
          date_to: toDate || storedTo,
          threshold,
          initial_capital: capital,
          strike_range: range,
          timeframe: cover?.timeframe || "1Minute",
          strategy: {},
        }),
      });
      setRunId(result.id);
      setMessage(result.status === "QUEUED" ? "Paper replay queued on the chosen historical range." : result.status);
    } catch (err: any) {
      setMessage(err.message);
    }
  }

  if (error && !data) return <p className="error">{error}</p>;
  if (!data) return <p>Loading…</p>;

  return (
    <>
      <h1>Backdated Paper Trading</h1>
      <p className="paper"><strong>PAPER SIMULATION — NO REAL ORDERS</strong></p>
      <p className="muted">{data.note}</p>
      <CoverageNote coverage={data.coverage || []} instrument={instrument} />

      <section className="card">
        <h2>Choose historical range</h2>
        <div className="form">
          <label>Instrument
            <select value={instrument} onChange={(event) => { setInstrument(event.target.value); setFromDate(""); setToDate(""); }}>
              <option value="NIFTY">NIFTY</option>
              <option value="SENSEX">SENSEX</option>
              <option value="BOTH">BOTH</option>
            </select>
          </label>
          <label>From Date
            <DateField min={storedFrom} max={storedTo} value={fromDate} onChange={setFromDate} />
          </label>
          <label>To Date
            <DateField min={storedFrom} max={storedTo} value={toDate} onChange={setToDate} />
          </label>
          <label>Strategy
            <input value="Current Strategy" readOnly />
          </label>
          <label>Starting Capital
            <input type="number" value={capital} onChange={(event) => setCapital(Number(event.target.value))} />
          </label>
          <label>Strike Range (ATM ±, used only if CE/PE history exists)
            <input type="number" value={range} onChange={(event) => setRange(Number(event.target.value))} />
          </label>
          <label>Entry Threshold %
            <input type="number" value={threshold} onChange={(event) => setThreshold(Number(event.target.value))} />
          </label>
        </div>
        <div className="row" style={{ marginTop: 12 }}>
          <button type="button" onClick={applyFullRange} disabled={!cover?.available}>Use full stored range</button>
          <button className="primary" onClick={start} disabled={!fromDate || !toDate}>Run paper on this range</button>
          <button type="button" onClick={startSweep} disabled={!cover?.available}>Run 65–85 × exit sweep (train/test)</button>
        </div>
        {!cover?.available ? <p className="warn">No stored candles for {instrument}. Import historical data first.</p> : null}
        {message ? <p>{message}</p> : null}
      </section>

      {run ? <Result run={run} /> : null}

      <section className="card">
        <h2>Previous runs</h2>
        <table>
          <thead>
            <tr><th>Id</th><th className="left">Instrument</th><th>Range</th><th>Trades</th><th>NET P&amp;L</th><th>Status</th></tr>
          </thead>
          <tbody>
            {(data.runs || []).map((row: any) => (
              <tr key={row.id} onClick={() => setRunId(row.id)} style={{ cursor: "pointer" }}>
                <td>{row.id}</td>
                <td className="left">{row.instrument}</td>
                <td>{row.from_date} → {row.to_date}</td>
                <td>{row.total_trades}</td>
                <td className={row.net_pnl >= 0 ? "pass" : "fail"}>{money(row.net_pnl)}</td>
                <td>{row.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}

function Result({ run }: { run: any }) {
  if (run.status === "QUEUED" || run.status === "RUNNING") {
    return (
      <section className="card">
        <h2>Backtest {run.status}</h2>
        <p>{run.instrument} {run.from_date} → {run.to_date}</p>
      </section>
    );
  }
  if (run.status === "FAILED") {
    return (
      <section className="card">
        <h2>Backtest failed</h2>
        <p className="error">{run.last_error}</p>
      </section>
    );
  }
  return (
    <>
      <section className="card">
        <h2>Backtest Result</h2>
        <p><strong>{run.instrument}</strong> {run.from_date} → {run.to_date}</p>
        <SweepGrid grid={run.performance?.grid} />
        <p>Initial Capital ₹{fmt(run.initial_capital)} · {run.strategy_label}</p>
        <p>Total Trades {run.total_trades} · Winning {run.wins} · Losing {run.losses}</p>
        <p>Win Rate {run.win_rate == null ? "—" : `${fmt(run.win_rate)}%`}</p>
        <p>Gross P&amp;L {money(run.gross_pnl)}</p>
        <p>Total Charges {money(run.total_charges)}</p>
        <p className={(run.net_pnl ?? 0) >= 0 ? "pass" : "fail"} style={{ fontSize: 20 }}>NET P&amp;L {money(run.net_pnl)}</p>
        <p>Maximum Drawdown {money(run.max_drawdown)}</p>
        <p>Profit Factor {run.profit_factor == null ? "—" : fmt(run.profit_factor)}</p>
        {run.data_incomplete ? <p className="warn">⚠ Historical data incomplete. Metrics are not a complete backtest.</p> : null}
        {run.paper_mode === "INDEX_FUTURES_PROXY" || !run.option_data_available ? (
          <p className="warn">INDEX PAPER — CE/PE history missing. BUY = long NIFTY/SENSEX, SELL = short NIFTY/SENSEX at the index close. Not option premium P&amp;L.</p>
        ) : null}
        <p className="muted">{run.option_data_note}</p>
      </section>

      <section className="card">
        <h2>Strategy Verification</h2>
        <Stats title="BUY CONDITIONS" rows={run.condition_stats?.BUY} />
        <Stats title="SELL CONDITIONS" rows={run.condition_stats?.SELL} />
        <Stats title="INDEX CONDITIONS" rows={run.condition_stats?.INDEX} />
        {run.condition_stats?.CE?.length ? <Stats title="CE CONDITIONS" rows={run.condition_stats.CE} /> : null}
        {run.condition_stats?.PE?.length ? <Stats title="PE CONDITIONS" rows={run.condition_stats.PE} /> : null}
      </section>

      <section className="card">
        <h2>Signal Threshold Analysis</h2>
        <table>
          <thead>
            <tr><th>Threshold</th><th>Signals</th><th>Trades</th><th>Win Rate</th><th>NET P&amp;L</th></tr>
          </thead>
          <tbody>
            {(run.threshold_analysis || []).map((row: any) => (
              <tr key={row.threshold}>
                <td>{row.threshold}%</td>
                <td>{fmt(row.signals)}</td>
                <td>{row.trades == null ? "—" : fmt(row.trades)}</td>
                <td>{row.win_rate == null ? "—" : `${fmt(row.win_rate)}%`}</td>
                <td>{row.net_pnl == null ? "—" : money(row.net_pnl)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted">Signal counts are bars where the best BUY/SELL index score reached that threshold. When CE/PE premiums are missing, trades fill on NIFTY/SENSEX index closes.</p>
      </section>

      <section className="card">
        <h2>Backtest Trades</h2>
        <table>
          <thead>
            <tr>
              <th className="left">Date</th><th>Time</th><th>Und</th><th>Side</th><th>Strike</th><th>Expiry</th>
              <th>Entry</th><th>Exit</th><th>Qty</th><th>Signal %</th><th>Gross</th><th>Charges</th><th>NET</th><th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {(run.trades || []).map((row: any, index: number) => (
              <tr key={index}>
                <td className="left">{row.date}</td>
                <td>{row.time}</td>
                <td>{row.instrument}</td>
                <td>{row.option_type}</td>
                <td>{fmt(row.strike)}</td>
                <td>{row.expiry || "—"}</td>
                <td>{fmt(row.entry)}</td>
                <td>{fmt(row.exit)}</td>
                <td>{row.quantity}</td>
                <td>{fmt(row.signal_percent)}</td>
                <td>{money(row.gross_pnl)}</td>
                <td>{money(row.charges)}</td>
                <td className={row.net_pnl >= 0 ? "pass" : "fail"}>{money(row.net_pnl)}</td>
                <td>{row.exit_reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!run.trades?.length ? <p className="muted">No simulated paper trades in this range.</p> : null}
      </section>
    </>
  );
}

function SweepGrid({ grid }: { grid?: any[] }) {
  if (!grid?.length) return null;
  return (
    <div className="table-scroll" style={{ marginTop: 12 }}>
      <p className="paper">SCORE/EXIT SWEEP — HISTORICAL PAPER — NOT LIVE PERFORMANCE</p>
      <table>
        <thead>
          <tr>
            <th className="left">Split</th>
            <th>Threshold</th>
            <th className="left">Exit</th>
            <th>Trades</th>
            <th>Win %</th>
            <th>Avg R</th>
            <th>Expectancy</th>
            <th>PF</th>
            <th>Max DD</th>
            <th>Expiry / other</th>
          </tr>
        </thead>
        <tbody>
          {grid.map((row, index) => (
            <tr key={`${row.split}-${row.threshold}-${row.exit_variant}-${index}`}>
              <td className="left">{row.split} {row.from_date}→{row.to_date}</td>
              <td>{row.threshold}</td>
              <td className="left">{row.exit_variant}</td>
              <td>{row.trades}</td>
              <td>{row.win_pct ?? "—"}</td>
              <td>{row.avg_r ?? "—"}</td>
              <td>{row.expectancy ?? "—"}</td>
              <td>{row.profit_factor ?? "—"}</td>
              <td>{row.max_drawdown ?? "—"}</td>
              <td>{row.expiry_trades}/{row.non_expiry_trades}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CoverageNote({ coverage, instrument }: { coverage: any[]; instrument: string }) {
  const symbols = instrument === "BOTH" ? ["NIFTY", "SENSEX"] : [instrument];
  const rows = symbols.map((symbol) => coverage.find((item) => item.instrument === symbol)).filter(Boolean);
  if (!rows.length || rows.some((row) => !row.available)) {
    return <p className="warn">Imported historical range is empty for {instrument}. Import NIFTY/SENSEX index candles first.</p>;
  }
  return (
    <>
      {rows.map((row: any) => (
        <p key={row.instrument} className="muted">
          Stored {row.instrument} {row.timeframe}: {row.from_date} → {row.to_date} · {row.trading_days} sessions · {row.candles} candles
        </p>
      ))}
      <p className="muted">When CE/PE history is missing for a date, paper trading uses that index itself.</p>
    </>
  );
}

function Stats({ title, rows }: { title: string; rows: any[] | undefined }) {
  return (
    <div>
      <h3>{title}</h3>
      {(rows || []).map((row) => (
        <p key={row.name}>{row.name} {fmt(row.pass_percent)}% passed · unavailable {fmt(row.unavailable)}</p>
      ))}
      {!rows?.length ? <p className="muted">No evaluations yet.</p> : null}
    </div>
  );
}
