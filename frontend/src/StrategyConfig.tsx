import { useEffect, useState } from "react";
import { api } from "./api";
import { FieldGroup, FieldInput } from "./fields";

type Group = { title: string; note?: string; keys: string[] };

const UNDERLYINGS = ["NIFTY", "SENSEX"];

/* Grouped in the order the dashboard evaluates the conditions, so a row in the
   condition table maps to the block that controls it. Notes that describe a
   comparison are written against the configured direction. */
function groupsFor(bearish: boolean): Group[] {
  const side = bearish ? "below" : "above";
  return [
    {
      title: "Timeframe and confirmation",
      note: "Which candle the strategy reads, and how many conditions must agree. Live trading always requires every enabled condition regardless of this setting.",
      keys: ["timeframe_minutes", "evaluate_on", "confirmation_mode", "min_conditions"],
    },
    {
      title: "Data sources",
      note: "Where price and open interest are read from. Index spot has no volume or OI, so the front-month future is the default for both.",
      keys: ["price_source", "oi_source"],
    },
    {
      title: "EMA trend — directional",
      note: `Dashboard row “EMA Trend”. Passes when the candle close is ${side} the EMA.`,
      keys: ["ema_enabled", "ema_period"],
    },
    {
      title: "Momentum — directional",
      note: bearish
        ? "Dashboard row “Momentum”. The move from the reference close must be below the negated threshold, so a threshold of 5 requires a fall of more than 5. A small rise does not qualify."
        : "Dashboard row “Momentum”. The move from the reference close must be above the threshold. Points measures index points; percent measures a share of the reference close, and each unit keeps its own threshold.",
      keys: ["momentum_unit", "momentum_time", "momentum_min_points", "momentum_min_percent"],
    },
    {
      title: "Open interest — direction neutral",
      note: "Dashboard row “OI Change”. This test is not reversed for a bearish direction: rising OI is read the same way either way. An OI increase on its own is not proof of long buildup, so treat this as one input among several.",
      keys: ["oi_time", "oi_min_change", "oi_unit"],
    },
    {
      title: "Futures volume — direction neutral",
      note: "Dashboard row “Futures Volume”. Passes when the candle volume is above its own EMA. Participation is required in either direction, so this is not reversed.",
      keys: ["volume_enabled", "volume_ema_period"],
    },
    {
      title: "VWAP — directional",
      note: `Dashboard row “VWAP”. Passes when the close is ${side} the session VWAP, which resets each day.`,
      keys: ["vwap_enabled"],
    },
    {
      title: "RSI — directional through configuration",
      note: "Dashboard row “RSI”. The two bands below are separate values, not one range flipped. Only the band matching the configured direction is evaluated.",
      keys: ["rsi_enabled", "rsi_period"],
    },
    {
      title: "ATR — direction neutral",
      note: "Dashboard row “ATR”. Volatility has to be present in either direction, so this is not reversed. The period lives here, but the threshold is set per underlying on the Risk screen because NIFTY and SENSEX move on very different scales.",
      keys: ["atr_enabled", "atr_period"],
    },
    {
      title: "Candle — directional",
      note: bearish
        ? "Dashboard row “Candle”. Requires close below open. A candle that closes exactly at its open satisfies neither direction and fails."
        : "Dashboard row “Candle”. Requires close above open. A candle that closes exactly at its open satisfies neither direction and fails.",
      keys: ["candle_bullish_required"],
    },
    {
      title: "Trading window — direction neutral",
      note: "Dashboard row “Time”. The same window applies to both directions and limits when a fresh entry may be considered.",
      keys: ["time_enabled", "time_start", "time_end"],
    },
    {
      title: "CE / PE option-buy thresholds",
      note: "Independent CE BUY and PE BUY evaluators on the NIFTY and SENSEX cards. Both use the same 9 checks: premium > EMA20, premium momentum from the reference time, futures volume, option OI(0)>OI(-1), futures VWAP, futures RSI band, futures ATR, bullish premium candle, and trading window. PE mirrors VWAP (close below) and uses the bearish RSI band. Premium momentum is a percent of the selected contract's 09:20 premium. ATR still uses the per-underlying threshold on the Risk screen.",
      keys: ["option_momentum_percent"],
    },
    {
      title: "Best option candidate",
      note: "Ranks CE and PE around ATM using the existing strategy scores, normalised as passed/total. A score below the minimum is shown as watch-only, never as a BUY. This does not change the 9-condition CE or PE rules.",
      keys: ["candidate_strike_range", "min_candidate_score_percent"],
    },
    {
      title: "Option type mapping",
      note: "The strategy direction decides which option type is bought. Option selection ranks contracts within that type and cannot contradict the direction. Both types are never chosen at once.",
      keys: ["bullish_option_type", "bearish_option_type"],
    },
  ];
}

const RSI_BULLISH = ["rsi_min", "rsi_max"];
const RSI_BEARISH = ["rsi_min_bearish", "rsi_max_bearish"];

function summarise(fields: Record<string, any>, symbol: string, atrThreshold: number | null) {
  const bearish = fields.direction === "BEARISH";
  const comparison = bearish ? "below" : "above";
  const unit = fields.momentum_unit === "percent" ? "%" : " pts";
  const magnitude = fields.momentum_unit === "percent" ? fields.momentum_min_percent : fields.momentum_min_points;
  const signed = bearish ? `-${magnitude}` : `+${magnitude}`;
  const band = bearish
    ? `${fields.rsi_min_bearish}–${fields.rsi_max_bearish} (bearish band)`
    : `${fields.rsi_min}–${fields.rsi_max} (bullish band)`;
  const lines: { label: string; text: string; neutral?: boolean }[] = [
    { label: "Underlying", text: symbol },
    { label: "Direction", text: `${fields.direction}, buying ${bearish ? fields.bearish_option_type : fields.bullish_option_type}` },
    { label: "EMA trend", text: fields.ema_enabled ? `Close ${comparison} EMA${fields.ema_period}` : "disabled" },
    {
      label: "Momentum",
      text: `${bearish ? "<" : ">"} ${signed}${unit} from ${fields.momentum_time}`,
    },
    { label: "VWAP", text: fields.vwap_enabled ? `Close ${comparison} session VWAP` : "disabled" },
    { label: "Candle", text: fields.candle_bullish_required ? `${bearish ? "Bearish" : "Bullish"} candle required, neutral candle fails` : "not required" },
    { label: "RSI", text: fields.rsi_enabled ? `${band}, inclusive` : "disabled" },
    { label: "OI change", text: `> ${fields.oi_min_change} ${fields.oi_unit} from ${fields.oi_time}`, neutral: true },
    { label: "Futures volume", text: fields.volume_enabled ? `> EMA${fields.volume_ema_period}` : "disabled", neutral: true },
    {
      label: "ATR",
      text: !fields.atr_enabled
        ? "disabled"
        : atrThreshold == null
          ? `period ${fields.atr_period}, no threshold set for ${symbol} on the Risk screen`
          : `period ${fields.atr_period}, > ${atrThreshold}`,
      neutral: true,
    },
    { label: "Trading window", text: fields.time_enabled ? `${fields.time_start}–${fields.time_end}` : "disabled", neutral: true },
    {
      label: "CE buy",
      text: `premium > EMA20, > ${fields.option_momentum_percent ?? 3}% from ${fields.momentum_time}, futures volume, OI(0)>OI(-1), close > VWAP, RSI ${fields.rsi_min}–${fields.rsi_max}, ATR, bullish premium candle, ${fields.time_start}–${fields.time_end} (9 checks)`,
    },
    {
      label: "PE buy",
      text: `same 9 checks as CE: premium > EMA20, > ${fields.option_momentum_percent ?? 3}% from ${fields.momentum_time}, futures volume, OI(0)>OI(-1), close < VWAP, RSI ${fields.rsi_min_bearish}–${fields.rsi_max_bearish}, ATR, bullish premium candle, ${fields.time_start}–${fields.time_end}`,
    },
    {
      label: "Candidate rank",
      text: `ATM ± ${fields.candidate_strike_range ?? 5} strikes, highlight only if score ≥ ${fields.min_candidate_score_percent ?? 60}% (normalised passed/total)`,
    },
    {
      label: "Signal rule",
      text: `every enabled condition must pass, otherwise NO TRADE`,
    },
  ];
  return lines;
}

export function StrategyConfig() {
  const [form, setForm] = useState<any>(null);
  const [symbol, setSymbol] = useState(UNDERLYINGS[0]);
  const [message, setMessage] = useState("");
  const [note, setNote] = useState("Saved from the strategy screen");

  function load() {
    api<any>("/api/strategy").then(setForm).catch((err) => setMessage(err.message));
  }
  useEffect(load, []);
  if (!form) return <p>{message || "Loading…"}</p>;
  const fields = form.parameters[symbol];
  if (!fields) return <p>No strategy parameters stored for {symbol}.</p>;
  const bearish = fields.direction === "BEARISH";
  const groups = groupsFor(bearish);
  const grouped = new Set([...groups.flatMap((group) => group.keys), "direction", ...RSI_BULLISH, ...RSI_BEARISH, "pe_midday_time", "pe_oi_since_midday"]);
  const leftover = Object.keys(fields).filter((key) => !grouped.has(key) && key !== "underlying");
  const profile = (form.underlyings ?? []).find((row: any) => row.symbol === symbol);

  function update(key: string, value: any) {
    setForm({ ...form, parameters: { ...form.parameters, [symbol]: { ...fields, [key]: value } } });
  }

  async function save() {
    setMessage("");
    try {
      const result = await api<any>("/api/strategy", {
        method: "PUT",
        body: JSON.stringify({ parameters: fields, underlying: symbol, note }),
      });
      setMessage(`Saved ${symbol} as version ${result.version_number}. Earlier trades keep the version they were taken under.`);
      load();
    } catch (err: any) {
      setMessage(err.message);
    }
  }

  return (
    <>
      <h1>Strategy configuration</h1>
      <p className="muted">
        Version {form.version_number}. Saving creates a new version rather than overwriting this one. These values are
        settings you chose, not parameters shown to be profitable.
      </p>

      <section className="card">
        <h2>Underlying and direction</h2>
        <p className="muted">
          NIFTY and SENSEX are configured independently. Switching the underlying below loads that index's own direction
          and thresholds; nothing is copied between them.
        </p>
        <div className="form">
          <label>
            Underlying
            <select value={symbol} onChange={(event) => setSymbol(event.target.value)}>
              {UNDERLYINGS.filter((name) => form.parameters[name]).map((name) => <option key={name}>{name}</option>)}
            </select>
          </label>
          <label>
            Direction
            <FieldInput name="direction" value={fields.direction} onChange={(next) => update("direction", next)} />
          </label>
        </div>
        <p className={bearish ? "bearish" : "bullish"}>
          <strong>{symbol} is set to {fields.direction}.</strong>{" "}
          {bearish
            ? "EMA, momentum, VWAP and candle are evaluated downward, and a signal buys a put."
            : "EMA, momentum, VWAP and candle are evaluated upward, and a signal buys a call."}{" "}
          Open interest, futures volume, ATR and the trading window are unchanged by direction.
        </p>
      </section>

      {groups.slice(0, 8).map((group) => (
        <FieldGroup key={group.title} title={group.title} note={group.note} keys={group.keys} values={fields} onChange={update} />
      ))}

      <FieldGroup
        title={`RSI band — bullish${bearish ? " (not in use)" : " (in use)"}`}
        note="Applied only when the direction is BULLISH. Stored separately from the bearish band so switching direction cannot silently reuse the wrong range."
        keys={RSI_BULLISH}
        values={fields}
        onChange={update}
      />
      <FieldGroup
        title={`RSI band — bearish${bearish ? " (in use)" : " (not in use)"}`}
        note="Applied only when the direction is BEARISH. A downtrend usually sits lower than a bullish one, so this band is configured on its own rather than reusing the bullish range."
        keys={RSI_BEARISH}
        values={fields}
        onChange={update}
      />

      {groups.slice(8).map((group) => (
        <FieldGroup key={group.title} title={group.title} note={group.note} keys={group.keys} values={fields} onChange={update} />
      ))}

      <FieldGroup title="Other settings" note="Fields not yet placed in a group above." keys={leftover} values={fields} onChange={update} />

      <section className="card">
        <h2>Review before saving</h2>
        <p className="muted">
          What the saved configuration will require for {symbol}. Rows marked neutral read the same in either direction.
        </p>
        <table>
          <tbody>
            {summarise(fields, symbol, profile ? profile.atr_threshold : null).map((line) => (
              <tr key={line.label}>
                <td>{line.label}</td>
                <td>{line.text}</td>
                <td className="muted">{line.neutral ? "direction neutral" : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="form">
          <label>
            Version note
            <input value={note} onChange={(event) => setNote(event.target.value)} />
          </label>
        </div>
        <p>
          <button className="primary" onClick={save}>Save {symbol} as a new version</button>{" "}
          <span className="muted">{message}</span>
        </p>
      </section>
    </>
  );
}
