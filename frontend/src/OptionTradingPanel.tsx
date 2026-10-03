import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { fmt, fmt4, usePoll } from "./lib";

const UNDERLYINGS = ["NIFTY", "SENSEX"];
const RUNG_ORDER = ["ITM", "ATM", "OTM"] as const;

export type OptionKind = "CE" | "PE";

export type Card = {
  moneyness_label: string;
  rank?: number;
  strike: number | null;
  option_type?: string;
  tradingsymbol?: string;
  instrument_token?: number;
  expiry?: string | null;
  ltp?: number | null;
  bid?: number | null;
  ask?: number | null;
  spread_percent?: number | null;
  oi?: number | null;
  oi_change?: number | null;
  volume?: number | null;
  iv?: number | null;
  delta?: number | null;
  gamma?: number | null;
  theta_per_day?: number | null;
  vega_per_point?: number | null;
  score?: number | null;
  entry?: number | null;
  stop_loss?: number | null;
  target?: number | null;
  reward_risk?: number | null;
  lot_size?: number | null;
  max_lots?: number;
  available: boolean;
  unavailable_reason?: string;
  oi_interpretation?: string;
  warnings?: string[];
};

export type Panel = {
  underlying: string;
  underlying_ltp: number | null;
  futures_ltp: number | null;
  atm_strike: number | null;
  strike_interval: number | null;
  expiry: string | null;
  lot_size: number | null;
  direction: string;
  graded_direction?: string;
  configured_direction: string;
  direction_score: number;
  condition_count: number;
  condition_total: number;
  signal_confirmed: boolean;
  signal: string;
  buy_signal: string;
  preferred_option: OptionKind;
  option_side: OptionKind;
  market_closed: boolean;
  stale: boolean;
  can_trade: boolean;
  blocked_reason: string;
  ladder: Record<string, Record<string, number | null>>;
  options: Record<OptionKind, Record<string, Card>>;
  chain: any[];
  no_trade_reasons: string[];
  timestamp: string;
  score_note: string;
};

function bearish(direction: string) {
  return (direction || "").includes("BEARISH");
}

function directionTone(direction: string) {
  if ((direction || "").includes("BEARISH")) return "bearish";
  if ((direction || "").includes("NEUTRAL")) return "muted";
  return "bullish";
}

function cardsFor(panel: Panel, kind: OptionKind): Card[] {
  const side = panel.options?.[kind] || {};
  return RUNG_ORDER.map((label) => side[label]).filter(Boolean);
}

export function useOptionChoice(preferredBySymbol: Record<string, OptionKind | undefined>) {
  const [choice, setChoice] = useState<Record<string, OptionKind>>({});
  const lastPreferred = useRef<Record<string, OptionKind>>({});
  const nifty = preferredBySymbol.NIFTY;
  const sensex = preferredBySymbol.SENSEX;

  useEffect(() => {
    setChoice((prev) => {
      const next = { ...prev };
      for (const [symbol, preferred] of Object.entries({ NIFTY: nifty, SENSEX: sensex })) {
        if (!preferred) continue;
        if (lastPreferred.current[symbol] !== preferred) {
          next[symbol] = preferred;
          lastPreferred.current[symbol] = preferred;
        } else if (!next[symbol]) {
          next[symbol] = preferred;
        }
      }
      return next;
    });
  }, [nifty, sensex]);

  function selected(symbol: string, fallback: OptionKind = "CE"): OptionKind {
    return choice[symbol] ?? fallback;
  }

  return { selected, setChoice };
}

export function OptionTypeToggle({
  value,
  preferred,
  onChange,
}: {
  value: OptionKind;
  preferred: OptionKind;
  onChange: (kind: OptionKind) => void;
}) {
  return (
    <div className="row tabs">
      {(["CE", "PE"] as OptionKind[]).map((kind) => (
        <button
          key={kind}
          className={kind === value ? "tab active" : "tab"}
          onClick={() => onChange(kind)}
        >
          {kind}{kind === preferred ? " ✓" : ""}
        </button>
      ))}
    </div>
  );
}

export function OptionTradingPanel({
  panels,
  selected,
  onSelect,
}: {
  panels: Record<string, Panel | null>;
  selected: (symbol: string, fallback?: OptionKind) => OptionKind;
  onSelect: (symbol: string, kind: OptionKind) => void;
}) {
  const [symbol, setSymbol] = useState(UNDERLYINGS[0]);
  const panel = panels[symbol];
  return (
    <section className="card">
      <h2>Option trading</h2>
      <div className="row tabs">
        {UNDERLYINGS.map((name) => (
          <button
            key={name}
            className={name === symbol ? "tab active" : "tab"}
            onClick={() => setSymbol(name)}
          >
            {name}
          </button>
        ))}
      </div>
      {panel ? (
        <PanelBody
          panel={panel}
          kind={selected(symbol, panel.preferred_option)}
          onSelect={(kind) => onSelect(symbol, kind)}
        />
      ) : (
        <p>Loading…</p>
      )}
    </section>
  );
}

function PanelBody({
  panel,
  kind,
  onSelect,
}: {
  panel: Panel;
  kind: OptionKind;
  onSelect: (kind: OptionKind) => void;
}) {
  const [pending, setPending] = useState<Card | null>(null);
  const [lots, setLots] = useState<Record<string, number>>({});
  const [message, setMessage] = useState("");
  const cards = cardsFor(panel, kind);

  const lotsFor = (card: Card) => lots[card.tradingsymbol ?? ""] ?? 1;
  const setLotsFor = (card: Card, value: number) =>
    setLots({ ...lots, [card.tradingsymbol ?? ""]: value });

  return (
    <>
      <Underlying panel={panel} />
      <Direction panel={panel} kind={kind} />
      <OptionTypeToggle value={kind} preferred={panel.preferred_option} onChange={onSelect} />
      {panel.blocked_reason ? <p className="fail"><strong>{panel.blocked_reason}</strong></p> : null}

      <h3>Option quick trade · {kind}</h3>
      <div className="grid">
        {cards.map((card) => (
          <OptionStrikeCard
            key={`${kind}-${card.moneyness_label}`}
            card={card}
            panel={panel}
            kind={kind}
            lots={lotsFor(card)}
            onLots={(value) => setLotsFor(card, value)}
            onBuy={() => { setMessage(""); setPending(card); }}
          />
        ))}
      </div>
      {message ? <p className="pass">{message}</p> : null}

      <h3>Mini option chain</h3>
      <OptionChainMiniTable panel={panel} />

      <p className="muted">{panel.score_note}</p>
      <p className="muted">As of {panel.timestamp} IST. Expiry {panel.expiry || "—"}.</p>

      {pending ? (
        <PaperTradeConfirmation
          card={pending}
          panel={panel}
          lots={lotsFor(pending)}
          onCancel={() => setPending(null)}
          onDone={(text) => { setPending(null); setMessage(text); }}
        />
      ) : null}
    </>
  );
}

function Underlying({ panel }: { panel: Panel }) {
  return (
    <div className="form">
      <div>
        <span className="muted">Spot / LTP</span>
        <p className="big">{fmt(panel.underlying_ltp)}</p>
      </div>
      <div>
        <span className="muted">Futures LTP</span>
        <p className="big">{fmt(panel.futures_ltp)}</p>
      </div>
      <div>
        <span className="muted">ATM strike</span>
        <p className="big">{fmt(panel.atm_strike)}</p>
      </div>
      <div>
        <span className="muted">Strike interval</span>
        <p className="big">{fmt(panel.strike_interval)}</p>
      </div>
      <div>
        <span className="muted">Lot size</span>
        <p className="big">{panel.lot_size ?? "—"}</p>
      </div>
    </div>
  );
}

function Direction({ panel, kind }: { panel: Panel; kind: OptionKind }) {
  const down = bearish(panel.direction);
  const fired = panel.signal !== "NO TRADE";
  return (
    <>
      <p className={directionTone(panel.direction)}>
        <strong>{down ? "▼" : panel.direction === "NEUTRAL" ? "●" : "▲"} Direction {panel.direction}</strong>
        <span className="muted"> · preferred {panel.preferred_option} · viewing {kind}</span>
      </p>
      {panel.configured_direction && panel.configured_direction !== panel.direction ? (
        <p className="muted">Configured bias {panel.configured_direction}. Live tape is {panel.direction}.</p>
      ) : null}
      <p className={fired ? "pass" : "fail"}>
        <strong>Signal {panel.signal}</strong>
        {!fired ? <span className="muted"> · Preferred: {panel.preferred_option}</span> : null}
        <span className="muted">
          {" "}· {panel.condition_count} of {panel.condition_total} conditions pass
        </span>
      </p>
    </>
  );
}

function QuantitySelector({
  lots,
  lotSize,
  max,
  onChange,
}: {
  lots: number;
  lotSize: number | null;
  max: number;
  onChange: (value: number) => void;
}) {
  const capped = max > 0 ? Math.min(lots, max) : lots;
  return (
    <div className="qty">
      <div className="row">
        <button onClick={() => onChange(Math.max(1, capped - 1))} disabled={capped <= 1}>−</button>
        <strong>{capped}</strong>
        <button onClick={() => onChange(capped + 1)} disabled={max > 0 && capped >= max}>+</button>
        <span className="muted">lots</span>
      </div>
      <p className="muted">
        1 lot = {lotSize ?? "—"} · Qty {lotSize ? capped * lotSize : "—"}
        {max > 0 ? ` · risk limits allow up to ${max} lots` : ""}
      </p>
    </div>
  );
}

function OptionStrikeCard({
  card,
  panel,
  kind,
  lots,
  onLots,
  onBuy,
}: {
  card: Card;
  panel: Panel;
  kind: OptionKind;
  lots: number;
  onLots: (value: number) => void;
  onBuy: () => void;
}) {
  const lotSize = card.lot_size ?? panel.lot_size;
  const tradeable = card.available && panel.can_trade && (card.max_lots ?? 0) > 0;
  const qty = lotSize ? lots * lotSize : null;
  const premium = card.ltp != null && qty != null ? card.ltp * qty : null;
  return (
    <div className="card inner">
      <h4>
        {card.rank ? `#${card.rank} ` : ""}
        {card.moneyness_label} {kind}
        {card.strike !== null ? ` ${fmt(card.strike)}` : ""}
      </h4>
      {card.tradingsymbol ? <p className="muted">{card.tradingsymbol} · expiry {card.expiry || panel.expiry || "—"}</p> : null}
      {card.strike === null ? (
        <p className="fail">{card.unavailable_reason}</p>
      ) : (
        <>
          <table>
            <tbody>
              <tr><td>LTP</td><td>₹{fmt(card.ltp)}</td></tr>
              <tr><td>Bid / Ask</td><td>₹{fmt(card.bid)} / ₹{fmt(card.ask)}</td></tr>
              <tr><td>Spread</td><td>{card.spread_percent == null ? "—" : `${fmt(card.spread_percent)}%`}</td></tr>
              <tr><td>OI</td><td>{fmt(card.oi)}</td></tr>
              <tr><td>OI change</td><td>{fmt(card.oi_change)}</td></tr>
              <tr><td>Volume</td><td>{fmt(card.volume)}</td></tr>
              <tr><td>IV</td><td>{card.iv == null ? "—" : fmt4(card.iv)}</td></tr>
              <tr><td>Delta</td><td>{card.delta == null ? "—" : fmt4(card.delta)}</td></tr>
              <tr><td>Theta</td><td>{card.theta_per_day == null ? "—" : fmt4(card.theta_per_day)}</td></tr>
              <tr><td>Gamma</td><td>{card.gamma == null ? "—" : fmt4(card.gamma)}</td></tr>
              <tr><td>Vega</td><td>{card.vega_per_point == null ? "—" : fmt4(card.vega_per_point)}</td></tr>
              {card.score != null ? <tr><td>Candidate score</td><td>{fmt(card.score)}/100</td></tr> : null}
              {card.entry != null ? <tr><td>Entry</td><td>₹{fmt(card.entry)}</td></tr> : null}
              {card.stop_loss != null ? <tr><td>Stop loss</td><td>₹{fmt(card.stop_loss)}</td></tr> : null}
              {card.target != null ? <tr><td>Target</td><td>₹{fmt(card.target)}</td></tr> : null}
              {card.reward_risk != null ? <tr><td>R/R</td><td>{fmt(card.reward_risk)}</td></tr> : null}
              {premium != null ? <tr><td>Required premium</td><td>₹{fmt(premium)}</td></tr> : null}
            </tbody>
          </table>
          {card.oi_interpretation ? <p className="muted">{card.oi_interpretation}</p> : null}
          <QuantitySelector lots={lots} lotSize={lotSize} max={card.max_lots ?? 0} onChange={onLots} />
          <button className="primary" disabled={!tradeable} onClick={onBuy}>
            BUY {kind}
          </button>
          {!card.available ? <p className="fail">{card.unavailable_reason}</p> : null}
          {card.available && !panel.can_trade ? <p className="fail">{panel.blocked_reason}</p> : null}
        </>
      )}
    </div>
  );
}

function OptionChainMiniTable({ panel }: { panel: Panel }) {
  if (!panel.chain.length) return <p className="muted">No contracts available for this expiry.</p>;
  const side = panel.preferred_option;
  return (
    <table>
      <thead>
        <tr>
          <th>CE label</th><th>CE LTP</th><th>CE OI</th><th>CE Vol</th>
          <th>Strike</th>
          <th>PE LTP</th><th>PE OI</th><th>PE Vol</th><th>PE label</th>
        </tr>
      </thead>
      <tbody>
        {panel.chain.map((row: any) => (
          <tr key={row.strike}>
            <td className={side === "CE" ? "strategy-side" : ""}>{row.ce?.moneyness_label ?? "—"}</td>
            <td className={side === "CE" ? "strategy-side" : ""}>{fmt(row.ce?.ltp)}</td>
            <td className={side === "CE" ? "strategy-side" : ""}>{fmt(row.ce?.oi)}</td>
            <td className={side === "CE" ? "strategy-side" : ""}>{fmt(row.ce?.volume)}</td>
            <td><strong>{fmt(row.strike)}</strong>{row.is_atm ? <span className="muted"> ATM</span> : null}</td>
            <td className={side === "PE" ? "strategy-side" : ""}>{fmt(row.pe?.ltp)}</td>
            <td className={side === "PE" ? "strategy-side" : ""}>{fmt(row.pe?.oi)}</td>
            <td className={side === "PE" ? "strategy-side" : ""}>{fmt(row.pe?.volume)}</td>
            <td className={side === "PE" ? "strategy-side" : ""}>{row.pe?.moneyness_label ?? "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function PaperTradeConfirmation({
  card,
  panel,
  lots,
  onCancel,
  onDone,
}: {
  card: Card;
  panel: Panel;
  lots: number;
  onCancel: () => void;
  onDone: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [override, setOverride] = useState(false);
  const lotSize = card.lot_size ?? panel.lot_size ?? 0;
  const quantity = lots * lotSize;
  const estimated = (card.ltp ?? 0) * quantity;
  const kind = (card.option_type || "CE") as OptionKind;

  async function confirm() {
    setBusy(true);
    setError("");
    try {
      const result = await api<any>("/api/paper-orders", {
        method: "POST",
        body: JSON.stringify({
          underlying: panel.underlying,
          instrument_token: card.instrument_token,
          side: "BUY",
          lots,
          order_type: "MARKET",
          requested_price: card.ltp,
          expiry: panel.expiry,
          acknowledge: true,
          override_duplicate: override,
        }),
      });
      onDone(
        `Paper position ${result.position_id} recorded: ${result.tradingsymbol}, ${result.lots} lot(s), ` +
        `quantity ${result.quantity}, simulated fill ₹${result.executed_paper_price}. No order was sent to the broker.`,
      );
    } catch (err: any) {
      setError(err.message);
      setOverride(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal">
      <div className="card">
        <h3>Confirm paper trade</h3>
        <p className="muted">PAPER TRADE — nothing is sent to Zerodha.</p>
        <table>
          <tbody>
            <tr><td>Instrument</td><td>{card.tradingsymbol}</td></tr>
            <tr><td>Side</td><td>BUY {kind}</td></tr>
            <tr><td>Order type</td><td>MARKET</td></tr>
            <tr><td>LTP</td><td>₹{fmt(card.ltp)}</td></tr>
            <tr><td>Lots</td><td>{lots}</td></tr>
            <tr><td>Quantity</td><td>{quantity} ({lotSize} per lot)</td></tr>
            <tr><td>Estimated value</td><td>₹{fmt(estimated)}</td></tr>
            <tr><td>Direction</td><td>{panel.direction} · preferred {panel.preferred_option}</td></tr>
            <tr><td>Signal</td><td>{panel.signal}</td></tr>
            <tr><td>Candidate score</td><td>{card.score == null ? "—" : `${fmt(card.score)}/100`}</td></tr>
            <tr><td>Stop loss / target</td><td>₹{fmt(card.stop_loss)} / ₹{fmt(card.target)}</td></tr>
          </tbody>
        </table>
        {!panel.signal_confirmed ? (
          <p className="fail">
            The strategy has not confirmed this signal ({panel.condition_count} of {panel.condition_total}{" "}
            conditions pass). This would be a manual paper trade against an unconfirmed setup.
          </p>
        ) : null}
        <p className="muted">
          Quantity is recalculated on the server from the instrument master lot size. The candidate score
          ranks suitability and is not a probability of profit.
        </p>
        {error ? <p className="error">{error}</p> : null}
        {override ? <p className="muted">Pressing confirm again will submit anyway.</p> : null}
        <p>
          <button onClick={onCancel} disabled={busy}>Cancel</button>{" "}
          <button className="primary" onClick={confirm} disabled={busy}>
            {busy ? "Recording…" : `Confirm BUY ${kind}`}
          </button>
        </p>
      </div>
    </div>
  );
}

export function SignalOptionPreview({
  panel,
  kind,
}: {
  panel: Panel | null;
  kind: OptionKind;
}) {
  if (!panel) return <p className="muted">Loading option data…</p>;
  const cards = cardsFor(panel, kind);
  if (!cards.length) return <p className="muted">No {kind} contracts around ATM for this expiry.</p>;
  return (
    <table>
      <thead>
        <tr>
          <th className="left"> </th>
          {cards.map((card) => (
            <th key={card.moneyness_label}>{card.moneyness_label}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        <tr>
          <td className="left">Strike</td>
          {cards.map((card) => <td key={card.moneyness_label}>{fmt(card.strike)}</td>)}
        </tr>
        <tr>
          <td className="left">Option</td>
          {cards.map((card) => <td key={card.moneyness_label}>{kind}</td>)}
        </tr>
        <tr>
          <td className="left">LTP</td>
          {cards.map((card) => <td key={card.moneyness_label}>₹{fmt(card.ltp)}</td>)}
        </tr>
        <tr>
          <td className="left">OI</td>
          {cards.map((card) => <td key={card.moneyness_label}>{fmt(card.oi)}</td>)}
        </tr>
        <tr>
          <td className="left">OI change</td>
          {cards.map((card) => <td key={card.moneyness_label}>{fmt(card.oi_change)}</td>)}
        </tr>
        <tr>
          <td className="left">Volume</td>
          {cards.map((card) => <td key={card.moneyness_label}>{fmt(card.volume)}</td>)}
        </tr>
        <tr>
          <td className="left">IV</td>
          {cards.map((card) => <td key={card.moneyness_label}>{card.iv == null ? "—" : fmt4(card.iv)}</td>)}
        </tr>
        <tr>
          <td className="left">Delta</td>
          {cards.map((card) => <td key={card.moneyness_label}>{card.delta == null ? "—" : fmt4(card.delta)}</td>)}
        </tr>
      </tbody>
    </table>
  );
}

export function useOptionPanels(enabled = true) {
  const nifty = usePoll<Panel>(enabled ? "/api/dashboard/options/NIFTY" : "", 4000);
  const sensex = usePoll<Panel>(enabled ? "/api/dashboard/options/SENSEX" : "", 4000);
  const panels = { NIFTY: nifty.data, SENSEX: sensex.data };
  return { panels, error: nifty.error || sensex.error };
}
