import { useEffect, useState } from "react";
import { api } from "./api";
import { FieldGroup } from "./fields";
import { fmt, fmt4, usePoll } from "./lib";

const UNDERLYINGS = ["NIFTY", "SENSEX"];

export function StrikeSelection() {
  const [underlying, setUnderlying] = useState("NIFTY");
  const [message, setMessage] = useState("");
  const [acknowledge, setAcknowledge] = useState(false);
  const { data, error } = usePoll<any>(`/api/strike-selection?underlying=${underlying}`, 3000);

  async function paperTrade(tradingsymbol: string) {
    setMessage("");
    try {
      const result = await api<any>("/api/strike-selection/paper-trade", {
        method: "POST",
        body: JSON.stringify({ underlying, tradingsymbol, acknowledge }),
      });
      setMessage(
        `Simulated ${result.tradingsymbol}: qty ${result.quantity} at ${fmt(result.entry)}, ` +
          `stop ${fmt(result.stop_loss)}, target ${fmt(result.target)}. Paper position ${result.position_id}.`,
      );
    } catch (err: any) {
      setMessage(err.message);
    }
  }

  return (
    <>
      <h1>Strike selection</h1>
      <div className="row">
        <select value={underlying} onChange={(event) => setUnderlying(event.target.value)}>
          {UNDERLYINGS.map((symbol) => <option key={symbol}>{symbol}</option>)}
        </select>
        <span className="muted">Each underlying uses its own configuration.</span>
      </div>
      {error ? <p className="error">{error}</p> : null}
      {!data ? <p>Loading…</p> : (
        <>
          <SignalCard data={data} />
          {!data.available ? (
            <section className="card">
              <h2 className="fail">NO SUITABLE OPTION</h2>
              <p>The engine did not evaluate any strike. Reasons:</p>
              <ul>{(data.no_trade_reasons || []).map((reason: string) => <li key={reason}>{reason}</li>)}</ul>
            </section>
          ) : data.selected ? (
            <SelectedCard
              candidate={data.selected}
              acknowledge={acknowledge}
              setAcknowledge={setAcknowledge}
              onPaperTrade={paperTrade}
            />
          ) : (
            <section className="card">
              <h2 className="fail">NO SUITABLE OPTION</h2>
              <p>Nothing passed the hard filters, so no strike is selected. Reasons:</p>
              <ul>{(data.no_trade_reasons || []).map((reason: string) => <li key={reason}>{reason}</li>)}</ul>
              <p className="muted">The engine does not lower its criteria to produce a trade.</p>
            </section>
          )}
          {data.top?.length > 1 ? <Comparison rows={data.top} /> : null}
          <CandidateTable rows={data.candidates || []} />
          <p className="muted">{data.disclaimer}</p>
          {message ? <p className="warn">{message}</p> : null}
        </>
      )}
    </>
  );
}

function SignalCard({ data }: { data: any }) {
  return (
    <section className="card">
      <h2>{data.underlying} · {data.direction} · {data.option_type || "no option type"}</h2>
      <p>
        Signal <span className={data.passed ? "pass" : "fail"}>{data.signal}</span> · Spot {fmt(data.spot)} ·
        Futures {fmt(data.futures_ltp)} · ATM {fmt(data.atm)} · Strike interval {fmt(data.strike_interval)} ·
        Expiry {data.expiry || "—"}
      </p>
      <p>
        ATR {fmt(data.atr)} · Expected move {fmt(data.expected_move)} points from the configured ATR multiple ·
        VWAP {fmt(data.vwap)}
      </p>
      {data.stale ? <p className="error">Market data is stale, so selection is blocked.</p> : null}
      {data.failed?.length ? <p className="muted">Failed strategy conditions: {data.failed.join(", ")}</p> : null}
      <p className="muted">
        A bullish signal maps to {data.option_type === "PE" ? "puts" : "calls"} only. The engine never picks a CE and a
        PE at the same time.
      </p>
    </section>
  );
}

function SelectedCard({
  candidate,
  acknowledge,
  setAcknowledge,
  onPaperTrade,
}: {
  candidate: any;
  acknowledge: boolean;
  setAcknowledge: (value: boolean) => void;
  onPaperTrade: (symbol: string) => void;
}) {
  return (
    <section className="card">
      <h2 className="pass">Selected: {candidate.tradingsymbol}</h2>
      <p>
        Strike {fmt(candidate.strike)} {candidate.option_type} · {candidate.moneyness} ·
        {" "}{candidate.strikes_from_atm === 0 ? "at ATM" : `${Math.abs(candidate.strikes_from_atm)} strike(s) from ATM`} ·
        Expiry {candidate.expiry || "—"} ({candidate.days_to_expiry ?? "—"} days, {candidate.expiry_band})
      </p>
      <p>
        LTP {fmt(candidate.ltp)} · Bid {fmt(candidate.bid)} ({fmt(candidate.bid_quantity)}) ·
        Ask {fmt(candidate.ask)} ({fmt(candidate.ask_quantity)}) · Spread {fmt(candidate.spread_percent)}%
      </p>
      <p>
        Volume {fmt(candidate.volume)} · OI {fmt(candidate.oi)} · OI change {fmt(candidate.oi_change)}
        {candidate.oi_change_percent === null || candidate.oi_change_percent === undefined ? "" : ` (${fmt(candidate.oi_change_percent)}%)`}
      </p>
      <p>
        IV {fmt(candidate.iv)} ({candidate.iv_band.toLowerCase()} versus nearby strikes) ·
        Delta {fmt4(candidate.delta)} · Gamma {fmt4(candidate.gamma)} · Theta/day {fmt(candidate.theta_per_day)} ·
        Vega per 1% {fmt(candidate.vega_per_point)}
      </p>
      <p>
        Entry {fmt(candidate.entry)} · Stop {fmt(candidate.stop_loss)} · Target {fmt(candidate.target)} ·
        Reward/risk {fmt(candidate.reward_risk)} · Lots {candidate.allowed_lots} · Quantity {candidate.quantity} ·
        Risk {fmt(candidate.risk_amount)} · Capital {fmt(candidate.capital_required)}
      </p>
      <p className="muted">
        If the underlying moves the expected {fmt(candidate.expected_move)} points to {fmt(candidate.expected_underlying_target)},
        the modelled premium response is about {fmt(candidate.expected_option_response)} points. This is a model
        calculation from delta, not a forecast.
      </p>
      <p><strong>Score {fmt(candidate.score)} / 100.</strong> This is a suitability score under your configured criteria, not a probability of profit.</p>
      <Subscores values={candidate.subscores || {}} />
      <h3>Why this strike</h3>
      <ul>{(candidate.reasons || []).map((reason: string) => <li key={reason}>{reason}</li>)}</ul>
      <h3>Interpretation</h3>
      <p className="muted">{candidate.oi_interpretation}</p>
      {candidate.warnings?.length ? (
        <>
          <h3 className="warn">Warnings</h3>
          <ul>{candidate.warnings.map((warning: string) => <li className="warn" key={warning}>{warning}</li>)}</ul>
        </>
      ) : null}
      <div className="row">
        <label>
          <input type="checkbox" checked={acknowledge} onChange={(event) => setAcknowledge(event.target.checked)} />
          I understand this records a simulated paper trade only
        </label>
        <button className="primary" disabled={!acknowledge} onClick={() => onPaperTrade(candidate.tradingsymbol)}>
          Record paper trade
        </button>
      </div>
      <p className="muted">This screen never places a live order.</p>
    </section>
  );
}

function Subscores({ values }: { values: Record<string, number> }) {
  const entries = Object.entries(values);
  if (!entries.length) return null;
  return (
    <table>
      <thead><tr><th className="left">Score component</th>{entries.map(([key]) => <th key={key}>{key.replace(/_/g, " ")}</th>)}</tr></thead>
      <tbody>
        <tr><td className="left">Out of 100</td>{entries.map(([key, value]) => <td key={key}>{fmt(value)}</td>)}</tr>
      </tbody>
    </table>
  );
}

function Comparison({ rows }: { rows: any[] }) {
  return (
    <>
      <h2>Top {rows.length} candidates</h2>
      <p className="muted">Alternatives are shown so you can compare rather than trust one line.</p>
      <table>
        <thead>
          <tr>
            <th className="left">Rank</th><th className="left">Symbol</th><th>Strike</th><th>Moneyness</th>
            <th>LTP</th><th>Spread %</th><th>Volume</th><th>OI</th><th>Delta</th><th>Theta/day</th>
            <th>Entry</th><th>Stop</th><th>Target</th><th>R:R</th><th>Lots</th><th>Score</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={row.tradingsymbol}>
              <td className="left">{index + 1}</td>
              <td className="left">{row.tradingsymbol}</td>
              <td>{fmt(row.strike)}</td>
              <td>{row.moneyness}</td>
              <td>{fmt(row.ltp)}</td>
              <td>{fmt(row.spread_percent)}</td>
              <td>{fmt(row.volume)}</td>
              <td>{fmt(row.oi)}</td>
              <td>{fmt4(row.delta)}</td>
              <td>{fmt(row.theta_per_day)}</td>
              <td>{fmt(row.entry)}</td>
              <td>{fmt(row.stop_loss)}</td>
              <td>{fmt(row.target)}</td>
              <td>{fmt(row.reward_risk)}</td>
              <td>{row.allowed_lots}</td>
              <td>{fmt(row.score)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function CandidateTable({ rows }: { rows: any[] }) {
  if (!rows.length) return <p className="muted">No strikes were available around ATM for this expiry.</p>;
  return (
    <>
      <h2>Every strike examined</h2>
      <table>
        <thead>
          <tr>
            <th>Strike</th><th>Type</th><th>Moneyness</th><th>LTP</th><th>Bid</th><th>Ask</th><th>Spread %</th>
            <th>Volume</th><th>OI</th><th>OI chg</th><th>IV</th><th>Delta</th><th>R:R</th><th>Score</th>
            <th>Status</th><th className="left">Reason</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={`${row.tradingsymbol}-${row.strike}`}>
              <td>{fmt(row.strike)}</td>
              <td>{row.option_type}</td>
              <td>{row.moneyness}</td>
              <td>{fmt(row.ltp)}</td>
              <td>{fmt(row.bid)}</td>
              <td>{fmt(row.ask)}</td>
              <td>{fmt(row.spread_percent)}</td>
              <td>{fmt(row.volume)}</td>
              <td>{fmt(row.oi)}</td>
              <td>{fmt(row.oi_change)}</td>
              <td>{fmt(row.iv)}</td>
              <td>{fmt4(row.delta)}</td>
              <td>{fmt(row.reward_risk)}</td>
              <td>{row.status === "PASS" ? fmt(row.score) : "—"}</td>
              <td className={row.status === "PASS" ? "pass" : "fail"}>{row.status}</td>
              <td className="left">{row.status === "PASS" ? (row.warnings || []).join("; ") : (row.reject_reasons || []).join("; ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

const GROUPS: [string, string[]][] = [
  ["Strike window", ["strikes_each_side"]],
  ["Hard liquidity filters", ["min_volume", "min_oi", "max_spread_percent", "max_quote_age_seconds"]],
  ["Premium filter", ["min_premium", "max_premium"]],
  ["Delta bands", ["preferred_delta_min", "preferred_delta_max", "acceptable_delta_min", "acceptable_delta_max", "delta_missing_action"]],
  ["Implied volatility", ["iv_high_ratio", "iv_high_action"]],
  ["Expected move and levels", ["atr_multiplier", "stop_method", "stop_option_percent", "stop_atr_multiplier", "target_reward_risk", "min_reward_risk"]],
  ["Time and expiry", ["max_holding_minutes", "near_expiry_days", "far_expiry_days"]],
  ["Position sizing", ["account_capital", "max_risk_percent", "max_lots", "max_capital_allocation", "max_daily_loss"]],
  ["Score weights and gate", ["weight_liquidity", "weight_spread", "weight_delta", "weight_reward_risk", "weight_volume_oi", "weight_iv", "weight_premium", "weight_expiry", "weight_atm_distance", "min_score"]],
];

export function StrikeSelectionConfig() {
  const [all, setAll] = useState<any>(null);
  const [underlying, setUnderlying] = useState("NIFTY");
  const [message, setMessage] = useState("");
  useEffect(() => {
    api<any>("/api/strike-selection/config").then(setAll).catch((err) => setMessage(err.message));
  }, []);
  if (!all) return <p>{message || "Loading…"}</p>;
  const form = all[underlying];
  if (!form) return <p className="error">{underlying} is not configured.</p>;
  const weightTotal = Object.keys(form)
    .filter((key) => key.startsWith("weight_"))
    .reduce((sum, key) => sum + Number(form[key] || 0), 0);

  function update(key: string, value: any) {
    setAll({ ...all, [underlying]: { ...form, [key]: value } });
  }

  async function save() {
    setMessage("");
    try {
      const result = await api<any>("/api/strike-selection/config", {
        method: "PUT",
        body: JSON.stringify({ ...form, underlying }),
      });
      setAll({ ...all, [underlying]: result });
      setMessage(`Saved ${underlying}.`);
    } catch (err: any) {
      setMessage(err.message);
    }
  }

  return (
    <>
      <h1>Strike selection configuration</h1>
      <div className="row">
        <select value={underlying} onChange={(event) => setUnderlying(event.target.value)}>
          {Object.keys(all).map((symbol) => <option key={symbol}>{symbol}</option>)}
        </select>
        <span className="warn">NIFTY and SENSEX are stored separately. Editing one never changes the other.</span>
      </div>
      {GROUPS.map(([title, keys]) => (
        <FieldGroup key={title} title={title} keys={keys} values={form} onChange={update}>
          {title.startsWith("Score weights") ? (
            <p className="muted">
              Weights total {fmt(weightTotal)} and are normalised to a 0-100 score. A candidate below
              {" "}{fmt(form.min_score)} is not selected. The score ranks suitability, it is not a probability of profit.
            </p>
          ) : null}
          {title.startsWith("Hard liquidity") ? (
            <p className="muted">These are hard filters. A strike that fails any of them is rejected and cannot be rescued by a high score.</p>
          ) : null}
        </FieldGroup>
      ))}
      <p><button className="primary" onClick={save}>Save {underlying}</button> <span className="muted">{message}</span></p>
    </>
  );
}
