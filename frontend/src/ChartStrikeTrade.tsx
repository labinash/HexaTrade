import { useMemo, useState } from "react";
import { api } from "./api";
import { fmt, usePoll } from "./lib";
import { useTradeNoticePush } from "./PaperTradeNotifications";

type Contract = {
  available?: boolean;
  tradingsymbol?: string;
  instrument_token?: number;
  strike?: number;
  expiry?: string;
  ltp?: number | null;
  oi?: number | null;
  lot_size?: number | null;
  option_type?: string;
  live_position?: any;
  unavailable_reason?: string;
};

type Rung = { label: string; strike: number | null; ce?: Contract | null; pe?: Contract | null };

function newClientOrderId() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  return `chart-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function oiText(value: number | null | undefined) {
  if (value == null) return "—";
  if (Math.abs(value) >= 100000) return `${(value / 100000).toFixed(1)}L`;
  if (Math.abs(value) >= 1000) return `${(value / 1000).toFixed(1)}k`;
  return fmt(value);
}

export function ChartStrikeTrade({ instrument, enabled }: { instrument: string; enabled: boolean }) {
  const path = enabled ? `/api/trading/chart-strikes?instrument=${instrument}` : "";
  const { data, error } = usePoll<any>(path, 4000);
  const [pending, setPending] = useState<null | { kind: "CE" | "PE"; rung: Rung; contract: Contract }>(null);

  if (!enabled) return null;
  return (
    <section className="chart-strike-rail">
      <div className="chart-strike-head">
        <p>
          <strong>{data?.trend || "—"}</strong>
          {" · "}
          {data?.condition_count ?? "—"}/{data?.condition_total ?? "—"} CONDITIONS
          {" · "}
          Recommended: <strong>{data?.recommended_action || data?.signal || "NO TRADE"}</strong>
        </p>
        <p className="muted">
          ATM {fmt(data?.atm_strike)} · interval {fmt(data?.strike_interval)} · expiry {data?.expiry || "—"} · lot {fmt(data?.lot_size)}
          {" · "}
          <span className={data?.trading?.chart_live_enabled ? "live-badge" : "paper-badge"}>
            {data?.trading?.badge || "PAPER TRADING"}
          </span>
        </p>
        <p className="paper">HYPOTHETICAL / PAPER signals only. No broker orders unless Broker has armed live trading.</p>
        <p className="muted">{data?.note}</p>
        {error ? <p className="error">{error}</p> : null}
        {data?.blocked_reason ? <p className="warn">{data.blocked_reason}</p> : null}
      </div>
      <div className="chart-strike-grid">
        {(data?.rungs || []).map((rung: Rung) => (
          <StrikeColumn
            key={rung.label}
            rung={rung}
            recommended={data?.recommended_side}
            live={Boolean(data?.trading?.chart_live_enabled)}
            onBuy={(kind, contract) => setPending({ kind, rung, contract })}
          />
        ))}
      </div>
      {pending ? (
        <TradeConfirmDialog
          instrument={instrument}
          trend={data?.trend}
          signal={data?.recommended_action || data?.signal}
          live={Boolean(data?.trading?.chart_live_enabled)}
          ackRequired={Boolean(data?.trading?.chart_live_enabled) && !data?.trading?.live_session_ack}
          settings={data?.trading || {}}
          pending={pending}
          onClose={() => setPending(null)}
        />
      ) : null}
    </section>
  );
}

function StrikeColumn({
  rung, recommended, live, onBuy,
}: {
  rung: Rung;
  recommended?: string | null;
  live: boolean;
  onBuy: (kind: "CE" | "PE", contract: Contract) => void;
}) {
  return (
    <div className="strike-column">
      <p className="muted">{rung.label}</p>
      <p className="big">{fmt(rung.strike)}</p>
      <ContractCard
        kind="CE"
        contract={rung.ce}
        primary={recommended === "CE"}
        live={live}
        onBuy={() => rung.ce && onBuy("CE", rung.ce)}
      />
      <ContractCard
        kind="PE"
        contract={rung.pe}
        primary={recommended === "PE"}
        live={live}
        onBuy={() => rung.pe && onBuy("PE", rung.pe)}
      />
    </div>
  );
}

function ContractCard({
  kind, contract, primary, live, onBuy,
}: {
  kind: "CE" | "PE";
  contract?: Contract | null;
  primary: boolean;
  live: boolean;
  onBuy: () => void;
}) {
  if (!contract?.tradingsymbol) {
    return <div className="strike-mini muted">{kind} unavailable</div>;
  }
  return (
    <div className={`strike-mini${primary ? " preferred" : ""}`}>
      <p><strong>{fmt(contract.strike)} {kind}</strong></p>
      <p>LTP ₹{fmt(contract.ltp)}</p>
      <p className="muted">OI {oiText(contract.oi)}</p>
      {contract.live_position ? (
        <p className="live-badge">LIVE ZERODHA qty {contract.live_position.quantity}</p>
      ) : null}
      <button
        type="button"
        className={primary ? "primary" : ""}
        disabled={!contract.available && !contract.tradingsymbol}
        onClick={onBuy}
      >
        {live ? `LIVE BUY ${kind}` : `PAPER BUY ${kind}`}
      </button>
      <p className="muted">{live ? "LIVE ZERODHA if confirmed — armed on Broker" : "PAPER — nothing is sent to Zerodha"}</p>
    </div>
  );
}

function TradeConfirmDialog({
  instrument, trend, signal, live, ackRequired, settings, pending, onClose,
}: {
  instrument: string;
  trend?: string;
  signal?: string;
  live: boolean;
  ackRequired: boolean;
  settings: any;
  pending: { kind: "CE" | "PE"; rung: Rung; contract: Contract };
  onClose: () => void;
}) {
  const push = useTradeNoticePush();
  const lotSize = pending.contract.lot_size || settings.lots || 0;
  const maxLots = Number(settings.max_lots_per_trade || 2);
  const [lots, setLots] = useState(1);
  const [orderType, setOrderType] = useState("MARKET");
  const [product, setProduct] = useState(settings.product || "MIS");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [livePhrase, setLivePhrase] = useState("");
  const quantity = lots * Number(lotSize || 0);
  const estimated = (pending.contract.ltp || 0) * quantity;
  const clientOrderId = useMemo(() => newClientOrderId(), [pending.contract.instrument_token, pending.kind]);

  async function submit() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const result = await api<any>("/api/trading/zerodha/order", {
        method: "POST",
        body: JSON.stringify({
          underlying: instrument,
          optionType: pending.kind,
          moneyness: pending.rung.label,
          transactionType: "BUY",
          lots,
          orderType,
          product,
          clientOrderId,
          liveConfirmation: live ? livePhrase : "",
          strategy: "CHART_SIGNAL",
          signal: trend,
        }),
      });
      push({
        id: `live:${result.local_order_id || clientOrderId}:${result.display_status}`,
        tradeId: Number(result.local_order_id || 0),
        underlying: instrument,
        optionType: pending.kind,
        strike: Number(pending.contract.strike || pending.rung.strike || 0),
        quantity: Number(result.quantity || quantity),
        entryPrice: Number(result.ltp || pending.contract.ltp || 0),
        tradingsymbol: result.tradingsymbol || pending.contract.tradingsymbol,
        kind: live ? "live" : "fill",
        status: result.display_status,
        orderId: result.order_id || undefined,
        badge: result.badge,
      });
      if (live && result.order_id) {
        api("/api/trading/zerodha/sync", { method: "POST" }).catch(() => undefined);
        watchLiveOrder(result.order_id, push, {
          underlying: instrument,
          optionType: pending.kind,
          strike: Number(pending.contract.strike || pending.rung.strike || 0),
          tradingsymbol: result.tradingsymbol || pending.contract.tradingsymbol,
          quantity: Number(result.quantity || quantity),
          ltp: Number(result.ltp || pending.contract.ltp || 0),
        });
      }
      onClose();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const title = live ? `BUY ${instrument} ${fmt(pending.contract.strike)} ${pending.kind}` : `PAPER BUY ${instrument} ${fmt(pending.contract.strike)} ${pending.kind}`;
  return (
    <div className="modal" onClick={onClose}>
      <div className="card" onClick={(event) => event.stopPropagation()}>
        <h3>{title}</h3>
        {live ? (
          <p className="warn">LIVE ZERODHA ORDER. This places a real BUY in the connected Zerodha account.</p>
        ) : (
          <p className="paper">PAPER TRADE — nothing is sent to Zerodha.</p>
        )}
        <table>
          <tbody>
            <tr><td>Instrument</td><td>{pending.contract.tradingsymbol}</td></tr>
            <tr><td>Exchange</td><td>{instrument === "SENSEX" ? "BFO" : "NFO"} (resolved on server)</td></tr>
            <tr><td>LTP</td><td>₹{fmt(pending.contract.ltp)}</td></tr>
            <tr><td>Strike</td><td>{fmt(pending.contract.strike)}</td></tr>
            <tr><td>Option</td><td>{pending.kind}</td></tr>
            <tr><td>Signal</td><td>{trend} · {signal}</td></tr>
            <tr>
              <td>Lots</td>
              <td>
                <button type="button" disabled={lots <= 1 || busy} onClick={() => setLots((n) => Math.max(1, n - 1))}>−</button>
                {" "}{lots}{" "}
                <button type="button" disabled={lots >= maxLots || busy} onClick={() => setLots((n) => Math.min(maxLots, n + 1))}>+</button>
              </td>
            </tr>
            <tr><td>Quantity</td><td>{quantity} ({lotSize} per lot)</td></tr>
            <tr>
              <td>Order Type</td>
              <td>
                <select value={orderType} disabled={busy} onChange={(event) => setOrderType(event.target.value)}>
                  <option>MARKET</option>
                  <option>LIMIT</option>
                </select>
              </td>
            </tr>
            <tr>
              <td>Product</td>
              <td>
                <select value={product} disabled={busy} onChange={(event) => setProduct(event.target.value)}>
                  <option>MIS</option>
                  <option>NRML</option>
                </select>
              </td>
            </tr>
            <tr><td>Estimated Value</td><td>₹{fmt(estimated)}</td></tr>
          </tbody>
        </table>
        {live && ackRequired ? (
          <>
            <p className="warn">First live order this session. Type CONFIRM LIVE BUY.</p>
            <input value={livePhrase} placeholder="CONFIRM LIVE BUY" disabled={busy} onChange={(event) => setLivePhrase(event.target.value)} />
          </>
        ) : null}
        {live && !ackRequired ? <p className="muted">Market protection {settings.market_protection_percent ?? "—"}% is applied on MARKET orders by the backend.</p> : null}
        {error ? <p className="error">{error}</p> : null}
        <div className="row">
          <button type="button" onClick={onClose} disabled={busy}>Cancel</button>
          <button type="button" className="primary" disabled={busy || !lotSize} onClick={submit}>
            {busy ? `BUY ${pending.kind}...` : live ? `BUY ${pending.kind}` : `PAPER BUY ${pending.kind}`}
          </button>
        </div>
      </div>
    </div>
  );
}

function watchLiveOrder(
  orderId: string,
  push: (row: any) => void,
  meta: { underlying: string; optionType: string; strike: number; tradingsymbol?: string; quantity: number; ltp: number },
) {
  let n = 0;
  const tick = async () => {
    n += 1;
    try {
      const row = await api<any>(`/api/trading/zerodha/order/${orderId}`);
      push({
        id: `live:${orderId}:${row.display_status}:${n}`,
        tradeId: 0,
        underlying: meta.underlying,
        optionType: meta.optionType,
        strike: meta.strike,
        quantity: Number(row.quantity || meta.quantity),
        entryPrice: Number(row.average_price || meta.ltp),
        tradingsymbol: row.tradingsymbol || meta.tradingsymbol,
        kind: "live",
        status: row.display_status,
        orderId,
        badge: "LIVE ZERODHA",
      });
      const done = /COMPLETE|REJECTED|CANCELLED/.test(String(row.display_status || ""));
      if (done) {
        api("/api/trading/zerodha/sync", { method: "POST" }).catch(() => undefined);
        return;
      }
    } catch {
      /* keep polling until the timeout */
    }
    if (n < 8) window.setTimeout(tick, 2000);
  };
  window.setTimeout(tick, 2000);
}
