import { useState } from "react";
import { api } from "./api";
import { fmt, usePoll } from "./lib";

export function LiveZerodhaPanel() {
  const { data, error } = usePoll<any>("/api/trading/zerodha/snapshot", 12000);
  const settings = data?.settings || {};
  const net = data?.positions?.net || [];
  const open = net.filter((row: any) => Number(row.quantity) !== 0);
  const [phrase, setPhrase] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function toggle(enabled: boolean) {
    setBusy(true);
    setMessage("");
    try {
      await api("/api/trading/live-settings", {
        method: "PUT",
        body: JSON.stringify({ enabled, confirmation: enabled ? phrase : "" }),
      });
      setPhrase("");
      setMessage(enabled ? "Live trading enabled for this process." : "Live trading off. BUY CE/PE is paper.");
    } catch (err: any) {
      setMessage(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function sync() {
    setBusy(true);
    try {
      await api("/api/trading/zerodha/sync", { method: "POST" });
      setMessage("Zerodha snapshot refreshed.");
    } catch (err: any) {
      setMessage(err.message);
    } finally {
      setBusy(false);
    }
  }

  const live = Boolean(settings.chart_live_enabled);
  const loaded = Boolean(data?.ok);
  const broker = String(settings.broker || "");
  return (
    <section className="card live-zerodha-panel">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2>My Live Zerodha Positions</h2>
        <button type="button" onClick={sync} disabled={busy}>↻ Sync Zerodha</button>
      </div>
      <p>
        <span className={live ? "live-badge" : "paper-badge"}>{live ? "🟢 LIVE TRADING ENABLED" : "⚪ PAPER TRADING"}</span>
        {" · "}
        Position book: <strong>net</strong> (Zerodha <code>positions().net</code> only)
        {broker ? <> · Broker session: <strong>{broker}</strong></> : null}
      </p>
      <p className="muted">These rows come from Zerodha kite.positions() net. Paper / strategy P&amp;L is listed under Archive. A paper fill is never shown here as a broker position.</p>
      <div className="row">
        <label>
          Enable Live Trading
          <button type="button" className={live ? "primary" : ""} disabled={busy} onClick={() => toggle(!live)}>
            {live ? "ON" : "OFF"}
          </button>
        </label>
        {!live ? (
          <input
            value={phrase}
            placeholder="PLACE REAL ORDERS"
            onChange={(event) => setPhrase(event.target.value)}
            disabled={busy}
          />
        ) : null}
      </div>
      {error ? <p className="error">{error}</p> : null}
      {data?.ok === false || data?.error ? (
        <p className="warn">Live Zerodha book was not loaded. {data?.error || error || "This table is empty until kite.positions() net returns rows. It is not paper P&L."}</p>
      ) : null}
      {message ? <p className="muted">{message}</p> : null}
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th className="left">Instrument</th>
              <th>Qty</th>
              <th>Avg ₹</th>
              <th>LTP ₹</th>
              <th>Invested</th>
              <th>Current</th>
              <th>LIVE UNREALIZED P&amp;L</th>
              <th>Realized</th>
              <th>Net</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {!loaded ? (
              <tr><td className="left" colSpan={10}>Live book not loaded. This is not a flat Zerodha position. Reconnect on Broker, then Sync Zerodha.</td></tr>
            ) : !open.length ? (
              <tr><td className="left" colSpan={10}>Zerodha net book is loaded and has no open rows (qty ≠ 0).</td></tr>
            ) : open.map((row: any) => (
              <tr key={`${row.tradingsymbol}-${row.product}`}>
                <td className="left">
                  {row.tradingsymbol}
                  <div className="live-badge">LIVE ZERODHA</div>
                </td>
                <td>{row.quantity}</td>
                <td>{fmt(row.average_price)}</td>
                <td>{fmt(row.ltp)}</td>
                <td>{fmt(row.invested_value)}</td>
                <td>{fmt(row.current_value)}</td>
                <td className={(row.unrealized_pnl ?? 0) >= 0 ? "pass" : "fail"}>{fmt(row.unrealized_pnl)}</td>
                <td>{fmt(row.realized_pnl)}</td>
                <td className={(row.net_pnl ?? 0) >= 0 ? "pass" : "fail"}>{fmt(row.net_pnl)}</td>
                <td>{row.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p>
        <strong>Total Open P&amp;L</strong> (LIVE UNREALIZED P&amp;L, net book){" "}
        {loaded ? `${Number(data?.open_pnl || 0) >= 0 ? "+" : ""}₹${fmt(data?.open_pnl)}` : "— (not loaded)"}
      </p>

      <TodaysOrders rows={data?.orders || []} />
      <TodaysTrades rows={data?.trades || []} />
    </section>
  );
}

function TodaysOrders({ rows }: { rows: any[] }) {
  return (
    <>
      <h3>Today&apos;s Zerodha Orders</h3>
      <p className="muted">Orders are not positions and not trades. Status is the broker status, not a local BUY SUCCESS.</p>
      <OrderTable rows={rows} kind="order" />
    </>
  );
}

function TodaysTrades({ rows }: { rows: any[] }) {
  const today = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
  const todays = rows.filter((row) => String(row.fill_timestamp || "").startsWith(today) || !row.fill_timestamp);
  return (
    <>
      <h3>Today&apos;s Zerodha Trades</h3>
      <p className="muted">Fills from the Zerodha trade book. Distinct from open positions and from paper P&amp;L.</p>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th className="left">Time</th>
              <th className="left">Instrument</th>
              <th>CE/PE</th>
              <th>BUY/SELL</th>
              <th>Quantity</th>
              <th>Price</th>
              <th className="left">Order ID</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {!todays.length ? (
              <tr><td className="left" colSpan={8}>No Zerodha trades loaded.</td></tr>
            ) : todays.map((row) => (
              <tr key={`${row.trade_id}-${row.order_id}`}>
                <td className="left">{String(row.fill_timestamp || "").replace("T", " ").slice(0, 19)}</td>
                <td className="left">{row.tradingsymbol}<span className="live-badge"> LIVE ZERODHA</span></td>
                <td>{row.option_type || "—"}</td>
                <td>{row.transaction_type}</td>
                <td>{row.quantity}</td>
                <td>₹{fmt(row.price)}</td>
                <td className="left">{row.order_id || "—"}</td>
                <td>{row.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function OrderTable({ rows }: { rows: any[]; kind: string }) {
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th className="left">Time</th>
            <th className="left">Instrument</th>
            <th>CE/PE</th>
            <th>BUY/SELL</th>
            <th>Quantity</th>
            <th>Price</th>
            <th className="left">Order ID</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {!rows.length ? (
            <tr><td className="left" colSpan={8}>No Zerodha orders loaded.</td></tr>
          ) : rows.map((row) => (
            <tr key={row.order_id || `${row.tradingsymbol}-${row.order_timestamp}`}>
              <td className="left">{String(row.order_timestamp || row.exchange_timestamp || "").replace("T", " ").slice(0, 19)}</td>
              <td className="left">{row.tradingsymbol}</td>
              <td>{row.option_type || "—"}</td>
              <td>{row.transaction_type}</td>
              <td>{row.quantity}</td>
              <td>₹{fmt(row.average_price ?? row.price)}</td>
              <td className="left">{row.order_id || "—"}</td>
              <td title={row.status_message || ""}>{row.display_status || row.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
