import { useState } from "react";
import { api } from "./api";
import { fmt, usePoll } from "./lib";

function bar(percent: number) {
  const width = Math.max(0, Math.min(100, Number(percent) || 0));
  return (
    <div className="progress-track">
      <div className="progress-fill" style={{ width: `${width}%` }} />
    </div>
  );
}

export function HistoricalData() {
  const { data, error } = usePoll<any>("/api/historical-data", 2000);
  const [instrument, setInstrument] = useState("BOTH");
  const [timeframe, setTimeframe] = useState("1Minute");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [message, setMessage] = useState("");
  const [jobId, setJobId] = useState<number | null>(null);
  const jobPath = jobId ? `/api/historical-data/jobs/${jobId}` : "";
  const { data: liveJob } = usePoll<any>(jobPath || "/api/historical-data", 1500);
  const job = jobId && liveJob && liveJob.id ? liveJob : data?.job;

  async function start(mode: string) {
    setMessage("");
    try {
      const body: Record<string, unknown> = {
        instruments: instrument,
        timeframe,
        mode,
        date_from: fromDate || data.default_from,
        date_to: toDate || data.default_to,
      };
      if (body.date_from && body.date_to && String(body.date_from) > String(body.date_to)) {
        setMessage("To date is before from date.");
        return;
      }
      const result = await api<any>("/api/historical-data/import", { method: "POST", body: JSON.stringify(body) });
      setJobId(result.id);
      setMessage(`Import ${result.status}. PAPER/live candle feed is not used.`);
    } catch (err: any) {
      setMessage(err.message);
    }
  }

  async function resume() {
    if (!job?.id) return;
    try {
      const result = await api<any>(`/api/historical-data/jobs/${job.id}/resume`, { method: "POST" });
      setJobId(result.id);
      setMessage("Resume queued");
    } catch (err: any) {
      setMessage(err.message);
    }
  }

  if (error && !data) return <p className="error">{error}</p>;
  if (!data) return <p>Loading…</p>;

  return (
    <>
      <h1>Historical Market Data</h1>
      <p className="muted">
        Stores a dedicated 1-minute NIFTY/SENSEX series for backtests. Does not write into the live candle table and never sends an order.
      </p>
      <p className="warn">{data.option_data_note}</p>

      <section className="card">
        <h2>Import</h2>
        <div className="form">
          <label>Instrument
            <select value={instrument} onChange={(event) => setInstrument(event.target.value)}>
              <option value="NIFTY">NIFTY</option>
              <option value="SENSEX">SENSEX</option>
              <option value="BOTH">Both</option>
            </select>
          </label>
          <label>Timeframe
            <select value={timeframe} onChange={(event) => setTimeframe(event.target.value)}>
              <option value="1Minute">1 Minute</option>
              <option value="3Minute">3 Minute</option>
              <option value="5Minute">5 Minute</option>
              <option value="15Minute">15 Minute</option>
            </select>
          </label>
          <label>From Date
            <input type="date" value={fromDate || data.default_from} onChange={(event) => setFromDate(event.target.value)} />
          </label>
          <label>To Date
            <input type="date" value={toDate || data.default_to} onChange={(event) => setToDate(event.target.value)} />
          </label>
        </div>
        <div className="row" style={{ marginTop: 12 }}>
          <button className="primary" onClick={() => start("INITIAL")}>Import Last 3 Years</button>
          <button onClick={() => start("INCREMENTAL")}>Import Missing Data</button>
          {job?.status === "FAILED" || job?.status === "PAUSED" ? <button onClick={resume}>Resume Import</button> : null}
        </div>
        {message ? <p>{message}</p> : null}
      </section>

      {job ? (
        <section className="card">
          <h2>Historical Data Import</h2>
          <p>{(job.instruments || []).join(", ")}</p>
          <p>{job.from_date} → {job.to_date} · {job.timeframe} · {job.status}</p>
          {bar(job.progress_percent)}
          <p>{fmt(job.progress_percent)}%</p>
          <p>Records processed {fmt(job.records_received)}</p>
          <p>Inserted {fmt(job.records_inserted)}</p>
          <p>Skipped duplicates {fmt(job.records_skipped)}</p>
          <p>Invalid records {fmt(job.records_invalid)}</p>
          <p>Errors {fmt(job.records_failed)}</p>
          <div className="row">
            {(job.sides || []).map((side: any) => (
              <span key={side.instrument} className={side.state === "completed" ? "pass" : side.state === "importing" ? "warn" : "muted"}>
                {side.instrument} {side.state === "completed" ? "✓ Completed" : side.state === "importing" ? "⟳ Importing..." : "· pending"}
              </span>
            ))}
          </div>
          {job.log_message ? <p className="muted">{job.log_message}</p> : null}
          {job.last_error ? <p className="error">{job.last_error}</p> : null}
        </section>
      ) : null}

      <section className="card">
        <h2>Data Quality</h2>
        {(data.quality || []).map((row: any) => (
          <div key={row.instrument} className="quality-block">
            <h3>{row.instrument} · {row.timeframe}</h3>
            <p>Trading Days {fmt(row.trading_days)}</p>
            <p>Expected Candles {fmt(row.expected_candles)}</p>
            <p>Imported Candles {fmt(row.imported_candles)}</p>
            <p>Missing Candles {fmt(row.missing_candles)}</p>
            <p>Duplicate Candles {fmt(row.duplicate_candles)}</p>
            <p>Invalid Candles {fmt(row.invalid_candles)}</p>
            {row.incomplete ? <p className="warn">⚠ Historical data incomplete</p> : <p className="pass">Coverage looks complete for stored sessions.</p>}
            <p className="muted">Timezone {row.timezone || "Asia/Kolkata"} · {row.data_source || "market_historical_data"}</p>
            {row.proxy_note || row.option_history_available === false ? (
              <p className="warn">Option history unavailable for this period. Index close is being used as the futures proxy.</p>
            ) : null}
            <p className="muted">{row.first_timestamp || "—"} → {row.last_timestamp || "—"}</p>
          </div>
        ))}
      </section>
    </>
  );
}
