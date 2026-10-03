import { useState } from "react";
import { api } from "./api";
import { FieldInput } from "./fields";
import { fmt, usePoll } from "./lib";

const TOGGLES: { key: string; label: string }[] = [
  { key: "notifications_enabled", label: "In-app popup when a CE/PE BUY appears" },
  { key: "sound_enabled", label: "Beep on a new BUY" },
  { key: "browser_notify", label: "Browser notification (needs permission)" },
  { key: "nifty_enabled", label: "Watch NIFTY options" },
  { key: "sensex_enabled", label: "Watch SENSEX options" },
  { key: "ce_alerts", label: "CE BUY alerts" },
  { key: "pe_alerts", label: "PE BUY alerts" },
];

const NUMBERS: { key: string; label: string }[] = [
  { key: "min_confidence", label: "Minimum score to alert" },
  { key: "min_score_separation", label: "CE vs PE score gap" },
  { key: "cooldown_seconds", label: "Seconds between alerts" },
  { key: "max_spread_percent", label: "Max option spread %" },
  { key: "min_option_volume", label: "Min option volume" },
  { key: "min_option_oi", label: "Min option OI" },
  { key: "min_rr", label: "Min reward:risk" },
  { key: "sl_percent", label: "Stop-loss %" },
  { key: "target1_rr", label: "Target 1 R:R" },
  { key: "target2_rr", label: "Target 2 R:R" },
  { key: "supertrend_atr_mult", label: "Supertrend ATR multiple" },
];

const TIMES = ["time_start", "time_end"] as const;

export function AlertCenter() {
  const { data, error } = usePoll<any>("/api/alerts", 4000);
  const [message, setMessage] = useState("");

  async function save(patch: Record<string, unknown>) {
    setMessage("");
    const next = { ...(data?.config || {}), ...patch };
    if (patch.browser_notify === true && "Notification" in window && Notification.permission === "default") {
      try {
        await Notification.requestPermission();
      } catch {
        /* browser blocked the prompt */
      }
    }
    try {
      await api("/api/alerts/settings", { method: "PUT", body: JSON.stringify(next) });
      setMessage("Saved. Alerts stay analytical — no broker order is placed.");
    } catch (err: any) {
      setMessage(err.message);
    }
  }

  async function act(id: number, action: "acknowledge" | "dismiss") {
    setMessage("");
    try {
      await api(`/api/alerts/${id}/${action}`, { method: "POST" });
    } catch (err: any) {
      setMessage(err.message);
    }
  }

  const config = data?.config || {};

  return (
    <>
      <h1>Alerts</h1>
      <p className="paper">
        <strong>What this page does:</strong> it watches NIFTY and SENSEX <strong>option CE/PE</strong> scores.
        When a BUY setup confirms, HexTrade shows a popup. It never places an order. It is not a prediction.
      </p>
      <p className="paper">HYPOTHETICAL / PAPER signals only. No broker orders.</p>
      <p className="muted">
        Chart and Paper Trading popups are for <strong>index</strong> BUY/SELL. This menu is only for option CE/PE.
        WAIT means hours, score, liquidity, or trend did not confirm. Timezone Asia/Kolkata.
      </p>
      {error ? <p className="error">{error}</p> : null}
      {message ? <p className="muted">{message}</p> : null}
      {!data && !error ? <p className="muted">Loading saved alerts…</p> : null}
      {!data ? null : (
      <>
      <section className="card">
        <h2>Notifications</h2>
        <p className="muted">Turn CE/PE watch on or off. Browser popups need permission. Sound is a short beep on a new BUY.</p>
        <div className="form">
          {TOGGLES.map(({ key, label }) => (
            <label key={key}>
              {label}
              <input
                type="checkbox"
                checked={Boolean(config[key])}
                onChange={(event) => save({ [key]: event.target.checked })}
              />
            </label>
          ))}
        </div>
      </section>

      <section className="card">
        <h2>When to alert</h2>
        <div className="form">
          {NUMBERS.map(({ key, label }) => (
            <label key={key}>
              {label}
              <input
                type="number"
                defaultValue={config[key]}
                onBlur={(event) => save({ [key]: Number(event.target.value) })}
              />
            </label>
          ))}
          {TIMES.map((key) => (
            <label key={key}>
              {key === "time_start" ? "Alert from" : "Alert until"}
              <FieldInput name={key} value={config[key] ?? ""} onChange={(value) => save({ [key]: value })} />
            </label>
          ))}
        </div>
      </section>

      <section className="card">
        <h2>Latest CE / PE scores</h2>
        {(data.snapshots || []).length ? (
          <div className="grid">
            {(data.snapshots || []).map((row: any) => (
              <div key={row.instrument} className="card">
                <h2>{row.instrument}</h2>
                {row.error ? <p className="error">{row.error}</p> : null}
                <p className={row.trend === "BEARISH" ? "bearish" : row.trend === "BULLISH" ? "bullish" : "muted"}>
                  {row.trend || "—"} {row.stale ? <span className="warn">delayed data</span> : null}
                </p>
                <p>Close {fmt(row.close)} · VWAP {fmt(row.vwap)} · EMA20 {fmt(row.ema20)} · EMA9 {fmt(row.ema9)} · EMA21 {fmt(row.ema21)}</p>
                <p>RSI {fmt(row.rsi)} · ATR {fmt(row.atr)} · Vol {fmt(row.volume)}</p>
                <p>CE {row.ce ? `${fmt(row.ce.strike)} ${fmt(row.ce.score_percent)}%` : "WAIT"} · PE {row.pe ? `${fmt(row.pe.strike)} ${fmt(row.pe.score_percent)}%` : "WAIT"}</p>
              </div>
            ))}
          </div>
        ) : null}
        <table>
          <thead>
            <tr>
              <th className="left">Instrument</th>
              <th>Side</th>
              <th>Signal</th>
              <th>Score</th>
              <th className="left">Reason</th>
            </tr>
          </thead>
          <tbody>
            {(data.latest_scores || []).length ? (
              (data.latest_scores || []).map((row: any) => (
                <tr key={`${row.instrument}-${row.option_type}`}>
                  <td className="left">{row.instrument}</td>
                  <td>{row.option_type}</td>
                  <td>{row.signal || "WAIT"}</td>
                  <td>{fmt(row.score)}</td>
                  <td className="left">{row.reason || "—"}</td>
                </tr>
              ))
            ) : (
              <tr>
                <td className="left muted" colSpan={5}>
                  No CE/PE scores yet. Scores appear after the market loop runs.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>

      <section className="card">
        <h2>Open signals</h2>
        <p className="muted">Strategy {data.strategy_name || "CE/PE option buy"} {data.strategy_version ? `v${data.strategy_version}` : ""}</p>
        {(data.open || []).length ? (
          (data.open || []).map((row: any) => <SignalCard key={row.id} row={row} onAct={act} />)
        ) : (
          <p className="muted">No open BUY alerts.</p>
        )}
      </section>

      <section className="card">
        <h2>Historical paper alerts</h2>
        <p className="paper">HISTORICAL PAPER ALERT — NOT LIVE</p>
        <p className="muted">
          BUY and SELL scores come from the same index engine: EMA20 trend, session VWAP, close-to-close momentum,
          volume vs volume EMA, RSI band, 20-bar breakout/breakdown, and implied CE/PE tape. Option history is not
          stored, so fills are an index-futures proxy at the index close — not CE/PE premium. Option NFO charges are
          not applied to these notionals. Same-candle stop/target is not checked. Score going cold is not an exit.
        </p>
        {(data.historical_alerts || []).length ? (
          <table>
            <thead>
              <tr>
                <th className="left">Candle time</th>
                <th className="left">Instrument</th>
                <th>Side</th>
                <th>Status</th>
                <th>BUY score</th>
                <th>SELL score</th>
                <th>Entry</th>
                <th>Gross</th>
                <th>Charges</th>
                <th>Net P&amp;L</th>
              </tr>
            </thead>
            <tbody>
              {(data.historical_alerts || []).map((row: any) => (
                <tr key={row.id}>
                  <td className="left">{stamp(row.candle_timestamp || row.signal_time)}</td>
                  <td className="left">{row.instrument}</td>
                  <td>{row.signal}</td>
                  <td>{row.status_code || row.status}</td>
                  <td>{fmt(row.buy_score)}</td>
                  <td>{fmt(row.sell_score)}</td>
                  <td>{fmt(row.entry_price)}</td>
                  <td className={(row.gross_pnl ?? 0) >= 0 ? "pass" : "fail"}>{fmt(row.gross_pnl)}</td>
                  <td>{fmt(row.charges)}</td>
                  <td className={(row.net_pnl ?? 0) >= 0 ? "pass" : "fail"}>{fmt(row.net_pnl)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">No historical paper alerts stored yet.</p>
        )}
      </section>

      <section className="card">
        <h2>History</h2>
        <table>
          <thead>
            <tr>
              <th className="left">Time</th>
              <th className="left">Contract</th>
              <th>Signal</th>
              <th>Status</th>
              <th>Score</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {(data.history || []).map((row: any) => (
              <tr key={row.id}>
                <td className="left">{stamp(row.signal_time)}</td>
                <td className="left">{row.instrument} {fmt(row.strike)} {row.option_type} {row.expiry || ""}</td>
                <td>{row.signal}</td>
                <td>{row.status_code || row.status}</td>
                <td>{fmt(row.confidence)}</td>
                <td>
                  <button type="button" onClick={() => act(row.id, "acknowledge")}>Acknowledge</button>{" "}
                  <button type="button" onClick={() => act(row.id, "dismiss")}>Dismiss</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="card">
        <h2>Audit log</h2>
        <p className="muted">Every score, rejection, and notification is recorded here.</p>
        <table>
          <thead>
            <tr>
              <th className="left">Time</th>
              <th>Event</th>
              <th>Result</th>
              <th>Score</th>
              <th className="left">Detail</th>
            </tr>
          </thead>
          <tbody>
            {(data.logs || []).map((row: any) => (
              <tr key={row.id}>
                <td className="left">{stamp(row.time)}</td>
                <td>{row.instrument} {row.option_type} {row.event_type}</td>
                <td>{row.result}</td>
                <td>{fmt(row.score)}</td>
                <td className="left">{row.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      </>
      )}
    </>
  );
}

function SignalCard({
  row,
  onAct,
}: {
  row: any;
  onAct: (id: number, action: "acknowledge" | "dismiss") => void;
}) {
  const reasons = Array.isArray(row.reasons) ? row.reasons : [];
  return (
    <article className="alert-signal">
      <div className="row">
        <strong>{row.instrument} {row.option_type} {fmt(row.strike)}</strong>
        <span className={row.signal === "BUY" ? "pass" : "muted"}>{row.signal}</span>
        <span>{row.status_code || row.status}</span>
        <span>CE {fmt(row.ce_score)} · PE {fmt(row.pe_score)}</span>
        <span>conf {fmt(row.confidence)}</span>
      </div>
      <p className="muted">{row.tradingsymbol} · expiry {row.expiry || "—"} · {row.label || "alert-only"} · {row.strategy_name || "CE/PE option buy"}</p>
      <p className="muted">Data {stamp(row.data_time)} · Signal {stamp(row.signal_time)}</p>
      {row.warning ? <p className="warn">{row.warning}</p> : null}
      <p>
        Entry {fmt(row.entry_low)}–{fmt(row.entry_high)} (LTP {fmt(row.entry_price)}) · SL {fmt(row.stop_loss)} · T1 {fmt(row.target1)} · T2 {fmt(row.target2)} · R:R {fmt(row.risk_reward)}
      </p>
      <ul className="alert-reasons">
        {reasons.map((item: any, index: number) => (
          <li key={`${item.name}-${index}`} className={item.passed ? "pass" : "fail"}>
            {item.passed ? "+" : "−"}{fmt(item.delta)} {item.name}: {item.detail}
          </li>
        ))}
      </ul>
      {(row.blocked || []).length ? <p className="warn">{(row.blocked || []).join(" ")}</p> : null}
      <div className="row">
        <button type="button" className="primary" onClick={() => onAct(row.id, "acknowledge")}>Acknowledge</button>
        <button type="button" onClick={() => onAct(row.id, "dismiss")}>Dismiss</button>
      </div>
    </article>
  );
}

function stamp(value: unknown) {
  if (!value) return "—";
  return String(value).replace("T", " ").slice(0, 19);
}
