import { fmt, usePoll } from "./lib";
import { PaperBanner } from "./PaperBanner";

export function ChartTradeBrief() {
  const { data, error } = usePoll<any>("/api/trading/dashboard-brief", 20000);
  if (!data && !error) return <p className="muted">Loading strategy vs live book…</p>;
  return (
    <section className="card trade-brief">
      <h2>Strategy vs live book</h2>
      <PaperBanner extra="This brief does not place an order. Arm chart live orders on Broker if you intend a real BUY." />
      <p className="muted">Signal, what you can buy, and what Zerodha actually holds stay separate. Market Ready is not a BUY.</p>
      {error ? <p className="error">{error}</p> : null}
      <div className="grid">
        {(data?.markets || []).map((market: any) => (
          <div key={market.symbol} className="card inner">
            <h3>{market.symbol}</h3>
            <p>
              Strategy: <strong>{market.trend || "—"}</strong>
              {market.recommended_side ? ` → ${market.recommended_action}` : " → NO TRADE"}
            </p>
            <p className="muted">{market.condition_count ?? "—"}/{market.condition_total ?? "—"} CONDITIONS</p>
            <p>What I can buy</p>
            <ul className="brief-rungs">
              {(market.rungs || []).map((row: any) => (
                <li key={row.label}>
                  {row.label} {fmt(row.strike)} · CE ₹{fmt(row.ce_ltp)} · PE ₹{fmt(row.pe_ltp)}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      {data?.last_order ? (
        <p>
          Last chart order: <strong>{data.last_order.display_status}</strong> {data.last_order.tradingsymbol}
          {" · "}qty {data.last_order.quantity}
          {" · "}
          <span className={data.last_order.execution_mode === "LIVE" ? "live-badge" : "paper-badge"}>{data.last_order.badge}</span>
          {data.last_order.broker_order_id ? ` · Order ID ${data.last_order.broker_order_id}` : ""}
        </p>
      ) : (
        <p className="muted">No chart order has been recorded yet.</p>
      )}
    </section>
  );
}
