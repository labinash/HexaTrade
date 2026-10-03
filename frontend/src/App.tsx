import { NavLink, Navigate, Route, Routes, useSearchParams } from "react-router-dom";
import { useEffect, useState } from "react";
import { api } from "./api";
import { fmt, usePoll } from "./lib";
import { FieldInput, labelFor } from "./fields";
import { DashboardCard } from "./DashboardCard";
import { OiScanner } from "./OiScanner";
import { OptionTradingPanel, useOptionChoice, useOptionPanels } from "./OptionTradingPanel";
import { StrategyConfig } from "./StrategyConfig";
import { PaperTrading } from "./PaperTrading";
import { HistoricalData } from "./HistoricalData";
import { BackdatedPaper } from "./BackdatedPaper";
import { MarketChart } from "./MarketChart";
import { PaperTradeNotificationProvider, useTicker } from "./PaperTradeNotifications";
import { AlertCenter } from "./AlertCenter";
import { CalendarPatterns } from "./CalendarPatterns";
import { ChartTradeBrief } from "./ChartTradeBrief";
import { LiveZerodhaPanel } from "./LiveZerodhaPanel";
import { StrikeSelection, StrikeSelectionConfig } from "./StrikeSelection";
import { PaperBanner } from "./PaperBanner";

const navGroups: { title: string; items: [string, string][] }[] = [
  { title: "Monitor", items: [["/", "Dashboard"], ["/chart", "Chart"], ["/alerts", "Alerts"]] },
  { title: "Trade", items: [["/paper", "Paper"], ["/chain", "Option chain"]] },
  { title: "Research", items: [["/calendar-patterns", "Calendar patterns"], ["/historical-data", "Historical Data"]] },
  { title: "Settings", items: [["/strategy", "Strategy"], ["/strike-selection", "Strike engine"], ["/broker", "Broker"], ["/audit", "Audit"]] },
];

export function App() {
  return (
    <PaperTradeNotificationProvider>
      <div className="app">
        <TickerBar />
        <div className="shell">
          <nav>
            <strong>HexTrade</strong>
            <p className="muted">Analysis first. Live orders stay disarmed after every restart.</p>
            {navGroups.map((group) => (
              <div key={group.title} className="nav-group">
                <p className="nav-group-title">{group.title}</p>
                {group.items.map(([to, label]) => (
                  <NavLink key={to} to={to} end={to === "/" || to === "/strategy" || to === "/strike-selection" || to === "/chain" || to === "/paper"}>
                    {label}
                  </NavLink>
                ))}
              </div>
            ))}
          </nav>
          <main>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/calendar-patterns" element={<CalendarPatterns />} />
              <Route path="/chart" element={<MarketChart />} />
              <Route path="/alerts" element={<AlertCenter />} />
              <Route path="/paper" element={<PaperHub />} />
              <Route path="/paper-trading" element={<Navigate to="/paper?tab=today" replace />} />
              <Route path="/historical-data" element={<HistoricalData />} />
              <Route path="/backdated-paper" element={<Navigate to="/paper?tab=backdated" replace />} />
              <Route path="/chain" element={<ChainHub />} />
              <Route path="/oi-scanner" element={<Navigate to="/chain?tab=oi" replace />} />
              <Route path="/strike-selection" element={<StrikeHub />} />
              <Route path="/strike-selection/config" element={<Navigate to="/strike-selection?tab=config" replace />} />
              <Route path="/strategy" element={<StrategyHub />} />
              <Route path="/selection" element={<Navigate to="/strategy?tab=option" replace />} />
              <Route path="/exits" element={<Navigate to="/strategy?tab=exits" replace />} />
              <Route path="/risk" element={<Navigate to="/strategy?tab=risk" replace />} />
              <Route path="/broker" element={<Broker />} />
              <Route path="/positions" element={<Navigate to="/paper?tab=positions" replace />} />
              <Route path="/audit" element={<Audit />} />
            </Routes>
          </main>
        </div>
      </div>
    </PaperTradeNotificationProvider>
  );
}

function TickerBar() {
  const { data, error } = useTicker();
  const items = data?.items?.length ? data.items : [{ symbol: "NIFTY" }, { symbol: "SENSEX" }];
  return (
    <header className="ticker">
      {items.map((item: any) => (
        <div className="quote" key={item.symbol}>
          <span className="name">{item.symbol}</span>
          <span className="price">{fmt(item.ltp)}</span>
          <span className={changeClass(item.change)}>
            {item.change === null || item.change === undefined ? "" : `${Number(item.change) >= 0 ? "+" : ""}${fmt(item.change)}`}
            {item.change_percent === null || item.change_percent === undefined ? "" : ` (${fmt(item.change_percent)}%)`}
          </span>
          <span className="muted">{item.futures_ltp === null || item.futures_ltp === undefined ? "" : `fut ${fmt(item.futures_ltp)}`}</span>
          {item.stale && item.ltp !== null && item.ltp !== undefined ? <span className="warn">stale</span> : null}
        </div>
      ))}
      <div className="state">
        {error && !data ? <span className="error">API unreachable</span> : null}
        {data && !data.database ? <span className="error">database down</span> : null}
        {data?.broker === "connected" ? <span className="pass">Zerodha connected</span> : null}
        {data?.broker && data.broker !== "connected" ? (
          <NavLink to="/broker" className="warn ticker-broker">
            Zerodha {data.broker} — reconnect
          </NavLink>
        ) : null}
        {data && !data.session_open ? <span className="muted">market closed</span> : null}
        {data?.kill_switch ? <span className="error">kill switch on</span> : null}
        <span className={data?.chart_live_enabled ? "live-badge" : "paper-badge"}>
          {data?.chart_live_enabled ? "🟢 LIVE TRADING ENABLED" : "⚪ PAPER TRADING"}
        </span>
        <span className={data?.mode === "LIVE" ? "live" : "muted"}>{data?.mode || ""}</span>
        <span className="muted">{data?.as_of ? `as of ${formatAsOfIst(data.as_of)}` : "no ticks yet"}</span>
      </div>
    </header>
  );
}

function formatAsOfIst(value: unknown) {
  const raw = String(value || "").trim();
  if (!raw) return "— IST";
  const parsed = new Date(raw.includes("T") || /[zZ]|[+-]\d{2}:?\d{2}/.test(raw) ? raw.replace(" ", "T") : `${raw.replace(" ", "T")}Z`);
  if (Number.isNaN(parsed.getTime())) return `${raw} IST`;
  const clock = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(parsed);
  return `${clock} IST`;
}

function changeClass(change: unknown) {
  if (change === null || change === undefined) return "muted";
  return Number(change) >= 0 ? "pass" : "fail";
}

function DataQualityBlock({ rows }: { rows: any[] }) {
  if (!rows.length) return null;
  return (
    <section className="card">
      <h2>Historical data quality</h2>
      <p className="muted">Timezone Asia/Kolkata. Source: market_historical_data INDEX.</p>
      {(rows || []).map((row: any) => (
        <div key={row.instrument}>
          <h3>{row.instrument}</h3>
          <p>
            {row.available_from || row.from_date || "—"} → {row.available_to || row.to_date || "—"}
            {" · "}candles {fmt(row.candle_count ?? row.imported_candles ?? row.candles)}
            {" · "}sessions {fmt(row.session_count ?? row.trading_days)}
            {" · "}missing {fmt(row.missing_candles)}
            {" · "}duplicate timestamps {fmt(row.duplicate_timestamp_count ?? row.duplicate_candles)}
          </p>
          {row.stale ? <p className="warn">Stale-data warning</p> : null}
          {row.proxy_note || row.option_history_available === false ? (
            <p className="warn">Option history unavailable for this period. Index close is being used as the futures proxy.</p>
          ) : null}
        </div>
      ))}
    </section>
  );
}

function PaperPerformanceBlock({ title, summary }: { title: string; summary: any }) {
  if (!summary) return null;
  return (
    <section className="card">
      <h2>{title}</h2>
      <p className="paper">{summary.label || summary.note || "HYPOTHETICAL / PAPER signals only. No broker orders."}</p>
      <p>
        Trades {fmt(summary.total_trades)} · BUY {fmt(summary.buy_trades)} · SELL {fmt(summary.sell_trades)} · Open {fmt(summary.open_trades)} · Closed {fmt(summary.closed_trades)}
      </p>
      <p>
        Wins {fmt(summary.winning_trades)} · Losses {fmt(summary.losing_trades)} · Win rate {summary.win_rate == null ? "—" : `${summary.win_rate}%`}
      </p>
      <p>
        Gross profit ₹{fmt(summary.gross_profit)} · Gross loss ₹{fmt(summary.gross_loss)} · Brokerage ₹{fmt(summary.brokerage)} · Slippage ₹{fmt(summary.slippage)} · Costs ₹{fmt(summary.total_costs)}
      </p>
      <p className={(summary.net_pnl ?? 0) >= 0 ? "pass" : "fail"}>
        Net P&L ₹{fmt(summary.net_pnl)} · Avg trade ₹{fmt(summary.average_trade_result)} · Max drawdown ₹{fmt(summary.max_drawdown)}
      </p>
      <p>NIFTY ₹{fmt(summary.nifty_pnl)} · SENSEX ₹{fmt(summary.sensex_pnl)} · BUY P&L ₹{fmt(summary.buy_pnl)} · SELL P&L ₹{fmt(summary.sell_pnl)} · PE P&L ₹{fmt(summary.pe_pnl)}</p>
      <p className="muted">{summary.pnl_formula}</p>
    </section>
  );
}

function PaperAutoStatus({ status }: { status: any }) {
  if (!status) return null;
  const on = Boolean(status.enabled);
  return (
    <section className="card">
      <h2>Paper Auto Trade</h2>
      <p className="paper">PAPER TRADING — NO REAL ORDERS</p>
      <p><strong>● {status.label || (on ? "ON" : "OFF")}</strong></p>
      {on ? (
        <>
          <p>Threshold {status.threshold}%</p>
          <p>Open Positions {status.open_positions}/{status.max_open_positions}</p>
          <p className={(status.today_net_pnl ?? 0) >= 0 ? "pass" : "fail"}>Today's Net P&amp;L {Number(status.today_net_pnl ?? 0) >= 0 ? "+" : ""}₹{fmt(status.today_net_pnl)}</p>
        </>
      ) : (
        <p className="muted">{status.message || "Signal monitoring active. No automatic paper entries."}</p>
      )}
    </section>
  );
}

function useScreenTab(defaultTab: string) {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || defaultTab;
  function setTab(next: string) {
    setParams({ tab: next }, { replace: true });
  }
  return [tab, setTab] as const;
}

function ScreenTabs({ tabs, value, onChange }: { tabs: [string, string][]; value: string; onChange: (id: string) => void }) {
  return (
    <div className="row tabs">
      {tabs.map(([id, label]) => (
        <button key={id} type="button" className={value === id ? "tab active" : "tab"} onClick={() => onChange(id)}>
          {label}
        </button>
      ))}
    </div>
  );
}

function PaperHub() {
  const [tab, setTab] = useScreenTab("today");
  return (
    <>
      <ScreenTabs
        tabs={[["today", "Today"], ["backdated", "Backdated"], ["positions", "Positions"]]}
        value={tab}
        onChange={setTab}
      />
      <p className="muted">Today uses paper_trade. Backdated uses historical_paper_*. Positions uses the original position table. These P&amp;Ls are not the same book.</p>
      {tab === "today" ? <PaperTrading /> : null}
      {tab === "backdated" ? <BackdatedPaper /> : null}
      {tab === "positions" ? <Positions /> : null}
    </>
  );
}

function StrategyHub() {
  const [tab, setTab] = useScreenTab("conditions");
  return (
    <>
      <ScreenTabs
        tabs={[["conditions", "Conditions"], ["option", "Option pick"], ["exits", "Exits"], ["risk", "Risk"]]}
        value={tab}
        onChange={setTab}
      />
      <p className="muted">One strategy version. Index paper exits use a different engine than the Exits tab. Saving still creates a new version.</p>
      {tab === "conditions" ? <Strategy /> : null}
      {tab === "option" ? <Selection /> : null}
      {tab === "exits" ? <Exits /> : null}
      {tab === "risk" ? <Risk /> : null}
    </>
  );
}

function StrikeHub() {
  const [tab, setTab] = useScreenTab("run");
  return (
    <>
      <ScreenTabs tabs={[["run", "Run"], ["config", "Config"]]} value={tab} onChange={setTab} />
      {tab === "run" ? <StrikeSelection /> : <StrikeSelectionConfig />}
    </>
  );
}

function ChainHub() {
  const [tab, setTab] = useScreenTab("ladder");
  return (
    <>
      <ScreenTabs tabs={[["ladder", "Ladder"], ["oi", "OI Scanner"]]} value={tab} onChange={setTab} />
      <p className="muted">ATM defaults to spot so both tabs agree. Futures ATM is still computed for reference on the scanner API.</p>
      {tab === "ladder" ? <Chain /> : <OiScanner />}
    </>
  );
}

function Dashboard() {
  const { data, error } = usePoll<any>("/api/dashboard", 8000);
  const [showLadder, setShowLadder] = useState(false);
  const { panels } = useOptionPanels(showLadder);
  const { selected, setChoice } = useOptionChoice({
    NIFTY: panels.NIFTY?.preferred_option,
    SENSEX: panels.SENSEX?.preferred_option,
  });
  return (
    <>
      <h1>Dashboard</h1>
      {error ? <p className="error">{error}</p> : null}
      {!data && !error ? <p>Loading market cards…</p> : null}
      {!data && error ? <p className="muted">Waiting for dashboard data. A slow request is not cleared while it retries.</p> : null}
      {data ? (
        <>
          <p className="paper">HYPOTHETICAL / PAPER signals only. No broker orders. Timezone Asia/Kolkata. Market Ready is not a BUY.</p>
          <div className="grid">
            {(data.markets || []).map((market: any) => (
              <DashboardCard key={market.symbol} market={market} />
            ))}
          </div>
          <RiskBlock risk={data.risk} paper={data.paper_auto} />
          <ChartTradeBrief />
          <LiveZerodhaPanel />
          <AlertHistory rows={data.alert_history || data.exit_alerts || []} />
          <details className="card why-block">
            <summary>Archive, paper P&amp;L, and data quality</summary>
            {data.proxy_note ? <p className="warn">{data.proxy_note}</p> : null}
            <PaperAutoStatus status={data.paper_auto} />
            <DataQualityBlock rows={data.data_quality || data.coverage || []} />
            <PaperPerformanceBlock title="Live paper P&L" summary={data.paper_performance} />
            <PaperPerformanceBlock title="Latest historical paper P&L" summary={data.historical_paper_performance} />
            <ActivePaperPositions positions={data.positions || []} />
          </details>
          <details className="card why-block" open={showLadder} onToggle={(event) => setShowLadder((event.target as HTMLDetailsElement).open)}>
            <summary>Option ladder (paper orders only)</summary>
            <p className="muted">Hidden by default so CE/PE watch cards are not repeated on the main dashboard. Recording a paper buy still never reaches the broker.</p>
            {showLadder ? (
              <OptionTradingPanel
                panels={panels}
                selected={selected}
                onSelect={(symbol, kind) => setChoice((prev) => ({ ...prev, [symbol]: kind }))}
              />
            ) : null}
          </details>
        </>
      ) : null}
    </>
  );
}

function RiskBlock({ risk, paper }: { risk: any; paper?: any }) {
  if (!risk) return null;
  return (
    <section className="card">
      <h2>Risk</h2>
      <p>Mode {risk.mode}. Redis {risk.redis ? "up" : "down"}. Live armed {risk.live_armed ? "yes" : "no"}.</p>
      <p>Daily P&amp;L {fmt(risk.daily_pnl)} · Loss room {fmt(risk.daily_loss_remaining)} · Trades today {risk.trades_today} · Exposure {fmt(risk.open_exposure)}</p>
      {paper ? <p className="paper">Paper auto {paper.label || (paper.enabled ? "ON" : "OFF")}. No broker orders.</p> : null}
    </section>
  );
}

function AlertHistory({ rows }: { rows: any[] }) {
  return (
    <section className="card">
      <h2>Alert History</h2>
      <p className="muted">Option CE/PE watches and exit notices. Alerts never place an order.</p>
      {!rows.length ? <p className="muted">No alert history yet.</p> : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th className="left">When</th>
                <th className="left">Instrument</th>
                <th>Side</th>
                <th>Signal</th>
                <th className="left">Status</th>
                <th className="left">Note</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id || `${row.symbol}-${row.signal}`}>
                  <td className="left">{row.at ? String(row.at).replace("T", " ").slice(0, 19) : "—"}</td>
                  <td className="left">{row.instrument || row.underlying || row.symbol || "—"}</td>
                  <td>{row.option_type || "—"}</td>
                  <td>{row.signal || row.reason || "—"}</td>
                  <td className="left">{row.status || "—"}</td>
                  <td className="left">{row.note || row.message || row.warning || ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function ActivePaperPositions({ positions }: { positions: any[] }) {
  const paper = positions.filter((row) => row.mode === "PAPER");
  if (!paper.length) return null;
  async function exit(id: number) {
    await api(`/api/positions/${id}/exit`, { method: "POST" });
  }
  return (
    <>
      <h2>Active paper positions</h2>
      <table>
        <thead>
          <tr>
            <th className="left">Instrument</th><th>Side</th><th>Qty</th><th>Entry</th><th>Current</th>
            <th>P&L</th><th>Stop loss</th><th>Target</th><th />
          </tr>
        </thead>
        <tbody>
          {paper.map((row) => (
            <tr key={row.id}>
              <td className="left">{row.symbol}</td>
              <td>BUY</td>
              <td>{row.quantity}</td>
              <td>₹{fmt(row.entry_price)}</td>
              <td>₹{fmt(row.ltp)}</td>
              <td className={(row.pnl ?? 0) >= 0 ? "pass" : "fail"}>₹{fmt(row.pnl)}</td>
              <td>₹{fmt(row.stop_loss)}</td>
              <td>₹{fmt(row.target)}</td>
              <td><button onClick={() => exit(row.id)}>Exit</button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function Chain() {
  const [underlying, setUnderlying] = useState("NIFTY");
  const [above, setAbove] = useState(10);
  const [below, setBelow] = useState(10);
  const { data, error } = usePoll<any>(`/api/chain?underlying=${underlying}&strikes_above=${above}&strikes_below=${below}`);
  return (
    <>
      <h1>Option chain</h1>
      <PaperBanner extra="Quotes are observational. An OI increase is not proof of long buildup." />
      <div className="row">
        <select value={underlying} onChange={(event) => setUnderlying(event.target.value)}>
          <option>NIFTY</option>
          <option>SENSEX</option>
        </select>
        <label>Above ATM <input type="number" value={above} onChange={(event) => setAbove(Number(event.target.value))} /></label>
        <label>Below ATM <input type="number" value={below} onChange={(event) => setBelow(Number(event.target.value))} /></label>
      </div>
      {error ? <p className="error">{error}</p> : null}
      {data ? (
        <>
          <p>Spot {fmt(data.spot)} · ATM {fmt(data.atm)} · Expiry {data.expiry || "—"} · PCR {fmt(data.pcr)} · Max pain {fmt(data.max_pain)} ({data.max_pain_scope})</p>
          <p>CE OI {data.total_ce_oi} · PE OI {data.total_pe_oi} · Highest OI {fmt(data.highest_oi_strike)} · Highest OI change {fmt(data.highest_oi_change_strike)}</p>
          <p className="muted">{data.note}</p>
          <table>
            <thead>
              <tr>
                <th>CE LTP</th><th>CE OI</th><th>CE Vol</th><th>CE IV</th><th>CE Chg</th><th>Bid</th><th>Ask</th>
                <th>Strike</th>
                <th>PE Bid</th><th>PE Ask</th><th>PE LTP</th><th>PE OI</th><th>PE Vol</th><th>PE IV</th><th>PE Chg</th>
              </tr>
            </thead>
            <tbody>
              {(data.rows || []).map((row: any) => (
                <tr key={row.strike}>
                  <td>{fmt(row.ce?.ltp)}</td><td>{fmt(row.ce?.oi)}</td><td>{fmt(row.ce?.volume)}</td><td>{fmt(row.ce?.iv)}</td><td>{fmt(row.ce?.change)}</td><td>{fmt(row.ce?.bid)}</td><td>{fmt(row.ce?.ask)}</td>
                  <td>{row.strike} <span className="muted">{row.moneyness}</span></td>
                  <td>{fmt(row.pe?.bid)}</td><td>{fmt(row.pe?.ask)}</td><td>{fmt(row.pe?.ltp)}</td><td>{fmt(row.pe?.oi)}</td><td>{fmt(row.pe?.volume)}</td><td>{fmt(row.pe?.iv)}</td><td>{fmt(row.pe?.change)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!data.rows?.length ? <p className="muted">Connect Zerodha and refresh the instrument master to fill this chain.</p> : null}
        </>
      ) : <p>Loading…</p>}
    </>
  );
}

function Strategy() {
  return <StrategyConfig />;
}
function Selection() {
  return <ConfigScreen title="Option selection" section="selection" savePath="/api/option-selection" />;
}
function Exits() {
  return (
    <>
      <p className="muted">These fields feed choose_exit for option / original-engine positions. Index paper and Backdated index proxy use the structural index exit engine, not this list.</p>
      <ConfigScreen title="Exit strategy" section="exits" savePath="/api/exits" />
    </>
  );
}
function Risk() {
  return <RiskScreen />;
}

function ConfigScreen({ title, section, savePath }: { title: string; section: string; savePath: string }) {
  const [form, setForm] = useState<any>(null);
  const [message, setMessage] = useState("");
  useEffect(() => { api<any>("/api/strategy").then(setForm).catch((err) => setMessage(err.message)); }, []);
  if (!form) return <p>{message || "Loading…"}</p>;
  const fields = form[section];
  async function save() {
    try {
      const result = await api<any>(savePath, { method: "PUT", body: JSON.stringify(fields) });
      setMessage(`Saved as version ${result.version_id}`);
    } catch (err: any) {
      setMessage(err.message);
    }
  }
  return (
    <>
      <h1>{title}</h1>
      <PaperBanner extra="These values are settings, not a claim of profitability." />
      <p className="muted">Version {form.version_number}. Saving creates a new version.</p>
      <div className="form">
        {Object.entries(fields).map(([key, value]) => (
          <label key={key}>
            {labelFor(key)}
            <FieldInput name={key} value={value} onChange={(next) => setForm({ ...form, [section]: { ...fields, [key]: next } })} />
          </label>
        ))}
      </div>
      <p><button className="primary" onClick={save}>Save</button> <span className="muted">{message}</span></p>
    </>
  );
}

function RiskScreen() {
  const [form, setForm] = useState<any>(null);
  const [message, setMessage] = useState("");
  useEffect(() => { api<any>("/api/strategy").then(setForm).catch((err) => setMessage(err.message)); }, []);
  if (!form) return <p>{message || "Loading…"}</p>;
  async function save() {
    const atr_thresholds: Record<string, number | null> = {};
    for (const row of form.underlyings) atr_thresholds[row.symbol] = row.atr_threshold;
    const result = await api<any>("/api/risk", { method: "PUT", body: JSON.stringify({ ...form.risk, atr_thresholds }) });
    setMessage(`Saved as version ${result.version_id}`);
  }
  return (
    <>
      <h1>Risk management</h1>
      <PaperBanner extra="These limits apply to the original engine and live-order checks. Paper Trading has its own daily-loss settings." />
      <p className="warn">SENSEX does not reuse the NIFTY ATR threshold. Leave it empty until you set one.</p>
      <div className="form">
        {form.underlyings.map((row: any, index: number) => (
          <label key={row.symbol}>{row.symbol} ATR threshold
            <input value={row.atr_threshold ?? ""} onChange={(event) => {
              const underlyings = [...form.underlyings];
              underlyings[index] = { ...row, atr_threshold: event.target.value === "" ? null : Number(event.target.value) };
              setForm({ ...form, underlyings });
            }} />
          </label>
        ))}
        {Object.entries(form.risk).map(([key, value]) => (
          <label key={key}>
            {labelFor(key)}
            <FieldInput name={key} value={value} onChange={(next) => setForm({ ...form, risk: { ...form.risk, [key]: next } })} />
          </label>
        ))}
      </div>
      <p><button className="primary" onClick={save}>Save</button> <span>{message}</span></p>
    </>
  );
}

function Broker() {
  const { data, error } = usePoll<any>("/api/broker/status", 8000);
  const { data: live } = usePoll<any>("/api/trading/live-settings", 8000);
  const [phrase, setPhrase] = useState("");
  const [requestToken, setRequestToken] = useState("");
  const [message, setMessage] = useState("");
  async function connect() {
    try {
      const result = await api<{ url: string }>("/api/broker/login-url");
      const tab = window.open(result.url, "_blank", "noopener,noreferrer");
      if (!tab) {
        setMessage("Popup blocked. Allow this site to open a new tab, then click Connect Zerodha again.");
        return;
      }
      setMessage("Zerodha login opened in a new tab. This Broker screen stays here and will refresh when the session is stored.");
    } catch (err: any) {
      setMessage(err.message);
    }
  }
  async function call(path: string, body?: object) {
    try {
      const result = await api<any>(path, { method: "POST", body: JSON.stringify(body || {}) });
      setMessage(result.warning || result.mode || result.status || "done");
    } catch (err: any) {
      setMessage(err.message);
    }
  }
  return (
    <>
      <h1>Broker</h1>
      <PaperBanner extra="This is the only screen that can arm live trading. Chart and Dashboard cannot enable it." />
      {error ? <p className="error">{error}</p> : null}
      {data?.error || data?.database === "down" ? (
        <p className="warn">SQL Server is not accepting connections right now. Connect Zerodha still works after the database comes back. Tokens stay on the backend.</p>
      ) : null}
      {data ? (
        <>
          <p>Status {data.status} · User {data.user_id || "—"} · Key {data.api_key_masked || "not set"} · Expires {data.expires_at || "—"}</p>
          <p>Mode {data.trading_mode} · Kill switch {data.kill_switch ? "ON" : "off"} · Auto entry {data.auto_entry_armed ? "armed" : "disarmed"}</p>
          <p className="warn">This mode can place real orders in the connected Zerodha account only after you arm live trading.</p>
          <div className="row">
            <button className="primary" onClick={connect}>Connect Zerodha</button>
            <button onClick={() => call("/api/broker/logout")}>Log out</button>
            <button onClick={() => api("/api/instruments/refresh", { method: "POST" }).then(() => setMessage("Instruments refreshed")).catch((err) => setMessage(err.message))}>Refresh instruments</button>
          </div>
          <p className="muted">
            If your Kite console redirect URL does not point at {data.redirect_url}, Zerodha sends you elsewhere after
            login. Copy the request_token from that address bar and paste it here. It expires within minutes.
          </p>
          <div className="row">
            <input
              value={requestToken}
              placeholder="request_token or the full redirect URL"
              size={44}
              onChange={(event) => setRequestToken(event.target.value)}
            />
            <button onClick={() => call("/api/broker/session", { request_token: requestToken })}>Complete login</button>
          </div>
          <div className="row">
            <button onClick={() => call("/api/trading/mode", { mode: "ANALYSIS" })}>Analysis only</button>
            <button onClick={() => call("/api/trading/mode", { mode: "PAPER" })}>Paper trading</button>
            <button onClick={() => call("/api/trading/resume", { mode: "PAPER", confirmation: "" })}>Resume paper protection</button>
          </div>
          <div className="row">
            <input value={phrase} placeholder="PLACE REAL ORDERS" onChange={(event) => setPhrase(event.target.value)} />
            <button onClick={() => call("/api/trading/arm", { confirmation: phrase })}>Enable live trading</button>
            <button
              className={live?.chart_live_enabled ? "primary" : ""}
              onClick={() => api("/api/trading/live-settings", {
                method: "PUT",
                body: JSON.stringify({ enabled: !live?.chart_live_enabled, confirmation: live?.chart_live_enabled ? "" : phrase }),
              }).then((result: any) => setMessage(result.badge || result.note || "chart live updated")).catch((err) => setMessage(err.message))}
            >
              {live?.chart_live_enabled ? "Chart live orders ON" : "Enable chart live orders"}
            </button>
            <button onClick={() => call("/api/trading/kill-switch")}>Kill switch</button>
          </div>
          <p className="muted">
            Chart live orders ({live?.badge || "PAPER TRADING"}) is the latch Chart BUY uses. Dashboard cannot turn it on.
            Type PLACE REAL ORDERS, then Enable chart live orders.
          </p>
          <ul>{(data.notes || []).map((note: string) => <li key={note}>{note}</li>)}</ul>
          <p>{message}</p>
        </>
      ) : <p>Loading…</p>}
    </>
  );
}

function Positions() {
  const { data, error } = usePoll<any[]>("/api/positions");
  const [orders, setOrders] = useState<any[]>([]);
  useEffect(() => { api<any[]>("/api/orders").then(setOrders).catch(() => setOrders([])); }, [data]);
  return (
    <>
      <h1>Positions and orders</h1>
      <PaperBanner extra="This table is the original position book, not Paper Trading today and not Backdated Paper." />
      {error ? <p className="error">{error}</p> : null}
      <table>
        <thead><tr><th className="left">Mode</th><th className="left">Symbol</th><th>Entry</th><th>LTP / exit</th><th>P&L</th><th>Stop</th><th>Target</th><th>Reason</th><th></th></tr></thead>
        <tbody>
          {(data || []).map((row) => (
            <tr key={row.id}>
              <td className={row.mode === "LIVE" ? "live" : "paper"}>{row.mode}</td>
              <td className="left">{row.symbol}</td>
              <td>{fmt(row.entry_price)}</td>
              <td>{fmt(row.ltp ?? row.exit_price)}</td>
              <td>{fmt(row.pnl)}</td>
              <td>{fmt(row.stop_loss)}</td>
              <td>{fmt(row.target)}</td>
              <td>{row.exit_reason || row.status}</td>
              <td>{row.status === "OPEN" ? <button onClick={() => api(`/api/positions/${row.id}/exit`, { method: "POST" })}>Close</button> : null}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h2>Orders</h2>
      <table>
        <thead><tr><th className="left">Mode</th><th>Side</th><th className="left">Symbol</th><th>Qty</th><th>Status</th><th>Reason</th></tr></thead>
        <tbody>
          {orders.map((row) => (
            <tr key={row.id}><td>{row.mode}</td><td>{row.side}</td><td className="left">{row.symbol}</td><td>{row.quantity}</td><td>{row.status}</td><td>{row.reason}</td></tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function Audit() {
  const { data, error } = usePoll<any[]>("/api/audit", 3000);
  return (
    <>
      <h1>Audit log</h1>
      <PaperBanner extra="Event log only. Nothing here places an order." />
      {error ? <p className="error">{error}</p> : null}
      <table>
        <thead><tr><th className="left">Time</th><th className="left">Event</th><th className="left">Underlying</th><th>Result</th><th className="left">Reason</th></tr></thead>
        <tbody>
          {(data || []).map((row) => (
            <tr key={row.id}><td className="left">{row.created_at}</td><td className="left">{row.event_type}</td><td>{row.underlying}</td><td>{row.result}</td><td className="left">{row.reason}</td></tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

