import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ColorType, CrosshairMode, LineStyle, TickMarkType, createChart } from "lightweight-charts";
import { DateField } from "./fields";
import { api } from "./api";
import { clearChartCache, fmt, useChartQuery } from "./lib";
import { useChartSignalNotices } from "./PaperTradeNotifications";
import { requestSignalNotifyPermission } from "./notifyPermission";
import { ChartStrikeTrade } from "./ChartStrikeTrade";

type Filters = { buy: boolean; sell: boolean; ce: boolean; pe: boolean };
type Indicators = { ema20: boolean; vwap: boolean; volume: boolean; rsi: boolean; atr: boolean; oi: boolean };
type Levels = { entry: boolean; target: boolean; stop: boolean; exit: boolean };

const BUY_CE = "#3dd68c";
const BUY_PE = "#4ecdc4";
const SELL_CE = "#e07a5f";
const SELL_PE = "#e8a33d";
const EMA = "#e8a33d";
const VWAP = "#8ab4f8";
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
/** Lightweight Charts labels Unix as UTC. Shift by IST (+5:30, no DST) so the axis shows Kolkata wall clock. */
const IST_OFFSET_SEC = 5.5 * 60 * 60;
const IST_ZONE = "Asia/Kolkata";
const HIGHLIGHT_KEY = "hextrade-calendar-highlight";

export function MarketChart() {
  const [searchParams] = useSearchParams();
  const urlInstrument = searchParams.get("instrument") === "SENSEX" ? "SENSEX" : searchParams.get("instrument") === "NIFTY" ? "NIFTY" : "";
  const urlStart = searchParams.get("start") || "";
  const urlEnd = searchParams.get("end") || "";
  const urlMode = searchParams.get("mode") || "";
  const [instrument, setInstrument] = useState(urlInstrument || "NIFTY");
  const [timeframe, setTimeframe] = useState("3m");
  const [mode, setMode] = useState(urlStart ? (urlMode || "HISTORICAL_ANALYSIS") : (urlMode || "LIVE_PAPER"));
  const [rangeKey, setRangeKey] = useState("1D");
  const [start, setStart] = useState(urlStart);
  const [end, setEnd] = useState(urlEnd);
  const [preset, setPreset] = useState("custom");
  const [indicators, setIndicators] = useState<Indicators>({
    ema20: true, vwap: true, volume: true, rsi: false, atr: false, oi: false,
  });
  const [filters, setFilters] = useState<Filters>({ buy: true, sell: true, ce: true, pe: true });
  const [levels, setLevels] = useState<Levels>({ entry: true, target: true, stop: true, exit: true });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [refresh, setRefresh] = useState(0);
  const [appliedStart, setAppliedStart] = useState(urlStart);
  const [appliedEnd, setAppliedEnd] = useState(urlEnd);
  const [patternLabel, setPatternLabel] = useState(searchParams.get("pattern") || "");
  const [highlightDates, setHighlightDates] = useState<string[]>(() => {
    const csv = searchParams.get("highlight");
    if (csv) return csv.split(",").filter(Boolean);
    try {
      const stored = JSON.parse(window.sessionStorage.getItem(HIGHLIGHT_KEY) || "null");
      return Array.isArray(stored?.dates) ? stored.dates.map(String) : [];
    } catch {
      return [];
    }
  });
  const historical = mode !== "LIVE_PAPER";
  const replay = mode === "HISTORICAL_REPLAY";
  const dateQuery = historical && appliedStart && appliedEnd
    ? `&start=${appliedStart}&end=${appliedEnd}`
    : historical && appliedStart
      ? `&start=${appliedStart}&end=${appliedStart}`
      : "";
  const path = `/api/chart?instrument=${instrument}&timeframe=${timeframe}&mode=${mode}&range=${rangeKey}${dateQuery}${historical ? `&r=${refresh}` : ""}`;
  const { data, error, loading } = useChartQuery<any>(path, !historical);
  const selectedFrom = start;
  const selectedTo = end || start;
  const loadedFrom = data?.date_from || "";
  const loadedTo = data?.date_to || data?.date_from || "";
  const appliedTo = appliedEnd || appliedStart;
  const dataForRange = Boolean(
    data
    && data.instrument === instrument
    && loadedFrom
    && appliedStart
    && loadedFrom === appliedStart
    && loadedTo === appliedTo,
  );
  const chart = historical
    ? (dataForRange ? data : null)
    : (data && data.instrument === instrument ? data : null);
  const pendingDates = Boolean(
    historical
    && (start || end)
    && (start !== appliedStart || (end || start) !== (appliedEnd || appliedStart)),
  );

  useEffect(() => {
    if (!historical || !data?.date_from) return;
    if (!start) setStart(data.date_from);
    if (!end) setEnd(data.date_to || data.date_from);
    if (!appliedStart) setAppliedStart(data.date_from);
    if (!appliedEnd) setAppliedEnd(data.date_to || data.date_from);
  }, [data?.date_from, data?.date_to, historical, start, end, appliedStart, appliedEnd]);

  useEffect(() => {
    setCursor(0);
    setPlaying(false);
    setSelectedId(null);
  }, [mode, instrument, timeframe, appliedStart, appliedEnd]);

  const strategy = data?.strategy || {};
  const allCandles = chart?.candles || [];
  const cutoffIndex = replay ? Math.min(cursor, Math.max(0, allCandles.length - 1)) : allCandles.length - 1;
  const candles = replay && allCandles.length ? allCandles.slice(0, Math.max(1, cutoffIndex + 1)) : allCandles;
  const cutoffTime = candles.length ? Number(candles[candles.length - 1].time) : 0;
  const strategySignals = visibleSignals(chart?.signals || [], filters).filter((row: any) => !replay || Number(row.time_unix) <= cutoffTime);
  const calendarSignals = calendarSessionSignals(candles, highlightDates, patternLabel).filter((row: any) => !replay || Number(row.time_unix) <= cutoffTime);
  const signals = [...strategySignals, ...calendarSignals];
  const selected = (chart?.signals || []).find((row: any) => row.signal_id === selectedId) || signals[signals.length - 1] || null;
  const minDate = data?.available_from || "";
  const maxDate = data?.available_to || "";
  const today = data?.today_ist || "";
  const ceOn = Boolean(strategy.ce_enabled);
  const peOn = Boolean(strategy.pe_enabled);
  const buyOn = strategy.buy_enabled !== false;
  const sellOn = strategy.sell_enabled !== false;
  const noStrategy = !buyOn && !sellOn && !ceOn && !peOn;

  useEffect(() => {
    requestSignalNotifyPermission();
  }, []);
  useChartSignalNotices(signals, mode === "LIVE_PAPER" && !replay && Boolean(chart), `${mode}|${instrument}|${timeframe}|${appliedStart}|${appliedEnd}`);

  useEffect(() => {
    if (!playing || !replay) return;
    const timer = window.setInterval(() => {
      setCursor((current) => {
        if (current >= allCandles.length - 1) {
          setPlaying(false);
          return current;
        }
        return current + 1;
      });
    }, Math.max(20, Math.round(400 / speed)));
    return () => window.clearInterval(timer);
  }, [playing, replay, speed, allCandles.length]);

  async function saveStrategy(patch: Record<string, unknown>) {
    setMessage("");
    try {
      await api("/api/paper-trading/settings", { method: "PUT", body: JSON.stringify(patch) });
      setMessage("Saved. Click OK to refresh the chart. PAPER TRADING — NO REAL ORDERS.");
    } catch (err: any) {
      setMessage(err.message);
    }
  }

  function saveIndex(partial: Record<string, unknown>) {
    saveStrategy({ index_strategy: { ...(strategy.index_strategy || {}), ...partial, preset: "CUSTOM" } });
  }

  function applyPreset(key: string) {
    setPreset(key);
    if (!today && !maxDate) return;
    const latest = maxDate && today && today > maxDate ? maxDate : (today || maxDate);
    if (key === "today") {
      const day = clampDate(latest, minDate, maxDate);
      setStart(day);
      setEnd(day);
      return;
    }
    if (key === "yesterday") {
      const day = clampDate(shiftIso(latest, -1), minDate, maxDate);
      setStart(day);
      setEnd(day);
      return;
    }
    if (key === "7d") {
      setEnd(clampDate(latest, minDate, maxDate));
      setStart(clampDate(shiftIso(latest, -6), minDate, maxDate));
      return;
    }
    if (key === "30d") {
      setEnd(clampDate(latest, minDate, maxDate));
      setStart(clampDate(shiftIso(latest, -29), minDate, maxDate));
    }
  }

  function applyMonth(ym: string) {
    if (!ym) return;
    const [year, month] = ym.split("-").map(Number);
    const lastDay = new Date(Date.UTC(year, month, 0)).getUTCDate();
    const first = `${ym}-01`;
    const last = `${ym}-${pad(lastDay)}`;
    setPreset("custom");
    setMode((current) => (current === "LIVE_PAPER" ? "HISTORICAL_ANALYSIS" : current));
    setStart(clampDate(first, minDate, maxDate));
    setEnd(clampDate(last, minDate, maxDate));
  }

  function changeRange(nextStart: string, nextEnd: string) {
    setPreset("custom");
    setStart(clampDate(nextStart, minDate, maxDate));
    setEnd(clampDate(nextEnd || nextStart, minDate, maxDate));
  }

  function applyChart() {
    const from = clampDate(start || appliedStart || minDate, minDate, maxDate);
    const to = clampDate(end || start || appliedEnd || from, minDate, maxDate);
    if (!from || !to) {
      setMessage("Choose a start and end date first.");
      return;
    }
    setStart(from);
    setEnd(to);
    setAppliedStart(from);
    setAppliedEnd(to);
    clearChartCache();
    setRefresh((value) => value + 1);
    setCursor(0);
    setPlaying(false);
    setSelectedId(null);
    setMessage("");
  }

  return (
    <>
      <h1>Market Chart</h1>
      <p className="paper">HYPOTHETICAL / PAPER signals only. Historical results are not a forecast. No broker orders.</p>
      {error ? <p className="error">{error}</p> : null}

      <section className="card chart-toolbar">
        <label>Instrument
          <select value={instrument} onChange={(event) => setInstrument(event.target.value)}>
            <option>NIFTY</option>
            <option>SENSEX</option>
          </select>
        </label>
        <label>Timeframe
          <select value={timeframe} onChange={(event) => setTimeframe(event.target.value)}>
            <option value="3m">3m</option>
            <option value="5m">5m</option>
            <option value="15m">15m</option>
            <option value="1m">1m</option>
          </select>
        </label>
        <label>Mode
          <select value={mode} onChange={(event) => { setMode(event.target.value); setStart(""); setEnd(""); setAppliedStart(""); setAppliedEnd(""); setPreset("custom"); }}>
            <option value="LIVE_PAPER">LIVE PAPER</option>
            <option value="HISTORICAL_ANALYSIS">HISTORICAL ANALYSIS</option>
            <option value="HISTORICAL_REPLAY">HISTORICAL REPLAY</option>
          </select>
        </label>
        {historical ? (
          <>
            <label>Quick range
              <select value={preset} onChange={(event) => applyPreset(event.target.value)}>
                <option value="today">Today</option>
                <option value="yesterday">Yesterday</option>
                <option value="7d">Last 7 days</option>
                <option value="30d">Last 30 days</option>
                <option value="custom">Custom date</option>
              </select>
            </label>
            <label>Month
              <select
                value={start ? start.slice(0, 7) : ""}
                onChange={(event) => applyMonth(event.target.value)}
              >
                <option value="">Choose month</option>
                {monthOptions(minDate, maxDate).map((row) => (
                  <option key={row.value} value={row.value}>{row.label}</option>
                ))}
              </select>
            </label>
            <label>Start date
              <DateField value={start} min={minDate} max={maxDate || end} onChange={(value) => changeRange(value, end || value)} />
            </label>
            <label>End date
              <DateField value={end} min={start || minDate} max={maxDate} onChange={(value) => changeRange(start || value, value)} />
            </label>
            <div className="chart-ok">
              <button type="button" className="primary" onClick={applyChart} disabled={!start && !end}>
                {loading ? "Loading…" : "OK"}
              </button>
            </div>
          </>
        ) : (
          <>
            <label>Date
              <select value="today" disabled>
                <option value="today">Today (IST)</option>
              </select>
            </label>
            <label>Range
              <select value={rangeKey} onChange={(event) => setRangeKey(event.target.value)}>
                <option value="1D">1D</option>
                <option value="5D">5D</option>
                <option value="1M">1M</option>
              </select>
            </label>
          </>
        )}
      </section>

      <p className="chart-status">
        <strong>{mode.replaceAll("_", " ")}</strong>
        {" · "}{instrument} {timeframe}
        {" · "}{data?.timezone || IST_ZONE} (IST)
        {" · "}{formatIstDate((pendingDates ? selectedFrom : appliedStart) || data?.date_from)}{(pendingDates ? selectedTo : (appliedEnd || appliedStart)) && (pendingDates ? selectedTo : (appliedEnd || appliedStart)) !== (pendingDates ? selectedFrom : appliedStart) ? ` → ${formatIstDate(pendingDates ? selectedTo : (appliedEnd || appliedStart))}` : ""}
        {" · "}Index BUY {buyOn ? "ON" : "OFF"} / Index SELL {sellOn ? "ON" : "OFF"}
        {" · "}{fmt(signals.length)} signals
        {patternLabel ? ` · Calendar pattern ${patternLabel} (${highlightDates.length} sessions)` : null}
        {loading ? ` · Loading${appliedStart ? ` ${formatIstDate(appliedStart)}${appliedEnd && appliedEnd !== appliedStart ? ` → ${formatIstDate(appliedEnd)}` : ""}` : ""}…` : null}
        {chart?.data_status && chart.data_status !== "OK" ? ` · ${chart.data_status.replaceAll("_", " ")}` : null}
      </p>
      {minDate && maxDate ? (
        <p className="muted">Stored {instrument} archive: {formatIstDate(minDate)} → {formatIstDate(maxDate)} (IST). Chart times are Asia/Kolkata, not UTC. Dates outside this range are blocked.{data?.timeframe && data.timeframe !== "1Minute" && timeframe !== "1m" ? ` ${data.timeframe} bars are built from 1-minute index candles.` : ""}{timeframe === "3m" ? " 3m entries also require a closed 5m same-side score, 10:00–14:00 IST, and no re-entry after a stop." : ""}</p>
      ) : loading ? (
        <p className="muted">Loading stored NIFTY/SENSEX archive coverage…</p>
      ) : historical && data ? (
        <div className="warn">
          <p>The 3-year historical archive is empty. It was not imported into <code>market_historical_data</code>.</p>
          {data?.live_candles ? <p>LIVE PAPER has {fmt(data.live_candles)} candles in the live feed. Historical Replay will not mix those in.</p> : null}
          <p>Go to <a href="/historical-data">Historical Data</a>, connect Zerodha on Broker, start an INITIAL import, and wait until the job status is COMPLETED. Then return here.</p>
        </div>
      ) : null}
      {pendingDates ? (
        <p className="warn">Dates are set to {formatIstDate(start)} → {formatIstDate(end || start)}. Click OK to refresh the chart.</p>
      ) : null}
      {(chart?.warnings || []).map((row: string) => <p key={row} className="warn">{row}</p>)}
      {message ? <p className="muted">{message}</p> : null}

      {replay ? (
        <section className="card chart-replay">
          <p><strong>Replay is sequential.</strong> Signals appear only when the playhead reaches that candle. No future bars are used.</p>
          <div className="row">
            <button type="button" className="primary" onClick={() => setPlaying((value) => !value)}>{playing ? "Pause" : "Play"}</button>
            <button type="button" onClick={() => { setPlaying(false); setCursor(0); }}>Restart</button>
            <button type="button" onClick={() => { setPlaying(false); setCursor((value) => Math.min(value + 1, Math.max(0, allCandles.length - 1))); }}>Step forward</button>
            <label>Speed
              <select value={speed} onChange={(event) => setSpeed(Number(event.target.value))}>
                {[1, 2, 5, 10, 20].map((value) => <option key={value} value={value}>{value}x</option>)}
              </select>
            </label>
            <span className="muted">{Math.min(cursor + 1, allCandles.length)} / {allCandles.length} candles</span>
          </div>
        </section>
      ) : null}

      <div className="chart-layout">
        <aside className="chart-side">
          <section className="card">
            <h2>STRATEGIES</h2>
            <p className="muted">
              Index BUY/SELL markers are walked on closed candles with the same 3m validation as Paper Trading. Re-entry, max trades/day, and max daily loss apply to those markers. HYPOTHETICAL / PAPER only.
            </p>
            <StrategySwitch
              label="Index BUY"
              on={buyOn}
              score={strategy.ce}
              onToggle={(value) => saveStrategy({ ce_strategy_enabled: value, index_strategy: { ...(strategy.index_strategy || {}), buy_enabled: value, preset: "CUSTOM" } })}
            />
            <StrategySwitch
              label="Index SELL"
              on={sellOn}
              score={strategy.pe}
              onToggle={(value) => saveStrategy({ pe_strategy_enabled: value, index_strategy: { ...(strategy.index_strategy || {}), sell_enabled: value, preset: "CUSTOM" } })}
            />
            <label className="inline-check">
              <input type="checkbox" checked={indicators.ema20} onChange={(event) => setIndicators((prev) => ({ ...prev, ema20: event.target.checked }))} />
              EMA20
            </label>
            <label className="inline-check">
              <input type="checkbox" checked={indicators.vwap} onChange={(event) => setIndicators((prev) => ({ ...prev, vwap: event.target.checked }))} />
              VWAP
            </label>
            <label>Entry Threshold
              <input
                type="number"
                defaultValue={strategy.threshold ?? 75}
                key={strategy.threshold}
                onBlur={(event) => saveStrategy({ min_signal_score: Number(event.target.value) })}
              />
            </label>
            <label className="inline-check">
              <input
                type="checkbox"
                checked={Boolean(strategy.allow_reentry)}
                onChange={(event) => saveIndex({ allow_reentry: event.target.checked, no_reentry_after_stop: !event.target.checked })}
              />
              Allow re-entry after stop
            </label>
            <label>Max trades / day
              <input
                type="number"
                defaultValue={strategy.max_trades_per_day ?? 2}
                key={`max-trades-${strategy.max_trades_per_day}`}
                onBlur={(event) => saveIndex({ max_trades_per_day: Number(event.target.value) })}
              />
            </label>
            <label>Max daily loss
              <input
                type="number"
                defaultValue={strategy.max_daily_loss ?? 5000}
                key={`max-loss-${strategy.max_daily_loss}`}
                onBlur={(event) => saveIndex({ max_daily_loss: Number(event.target.value) })}
              />
            </label>
            <label className="inline-check">
              <input
                type="checkbox"
                checked={strategy.index_strategy?.confirm_higher_tf !== false}
                onChange={(event) => saveIndex({ confirm_higher_tf: event.target.checked })}
              />
              Confirm 3m with 5m
            </label>
            <label>Auto Paper Trade
              <button type="button" className={strategy.auto_paper ? "primary" : ""} onClick={() => saveStrategy({ enabled: !strategy.auto_paper })}>
                {strategy.auto_paper ? "ON" : "OFF"}
              </button>
            </label>
            <p className="muted">{strategy.best_setup?.label === "NO TRADE" ? strategy.best_setup?.reason : `Best eligible setup: ${strategy.best_setup?.label || "—"}`}</p>
          </section>

          <section className="card">
            <h2>Indicators</h2>
            {(["ema20", "vwap", "volume", "rsi", "atr", "oi"] as const).map((key) => (
              <label key={key} className="inline-check">
                <input type="checkbox" checked={indicators[key]} onChange={(event) => setIndicators((prev) => ({ ...prev, [key]: event.target.checked }))} />
                {key.toUpperCase()}
              </label>
            ))}
          </section>

          <section className="card">
            <h2>SIGNALS</h2>
            <p className="muted">Visibility only. Does not change strategy ON/OFF.</p>
            {(["buy", "sell", "ce", "pe"] as const).map((key) => (
              <label key={key} className="inline-check">
                <input type="checkbox" checked={filters[key]} onChange={(event) => setFilters((prev) => ({ ...prev, [key]: event.target.checked }))} />
                {key.toUpperCase()}
              </label>
            ))}
          </section>

          <section className="card">
            <h2>Levels</h2>
            {(["entry", "target", "stop", "exit"] as const).map((key) => (
              <label key={key} className="inline-check">
                <input type="checkbox" checked={levels[key]} onChange={(event) => setLevels((prev) => ({ ...prev, [key]: event.target.checked }))} />
                {key === "stop" ? "Stop Loss" : key[0].toUpperCase() + key.slice(1)}
              </label>
            ))}
          </section>
        </aside>

        <section className="card chart-stage">
          <div className="chart-legend">
            <span style={{ color: BUY_CE }}>⇈ BUY</span>
            <span style={{ color: SELL_CE }}>⇊ SELL</span>
            <span style={{ color: "#5aa8ff" }}>○ Target</span>
            <span style={{ color: "#e08a3c" }}>○ Stop</span>
            <span style={{ color: "#9b7dff" }}>○ Time/forced</span>
            <span style={{ color: BUY_CE }}>⇈ BUY CE</span>
            <span style={{ color: BUY_PE }}>⇈ BUY PE</span>
            <span style={{ color: SELL_CE }}>⇊ SELL CE</span>
            <span style={{ color: SELL_PE }}>⇊ SELL PE</span>
            <span style={{ color: "#8ab4f8" }}>■ Matching weekday session</span>
            <span style={{ color: "#3dd68c" }}>● LIVE BUY CE</span>
            <span style={{ color: "#4ecdc4" }}>● LIVE BUY PE</span>
            <span style={{ color: "#e07a5f" }}>● LIVE SELL</span>
            {indicators.ema20 ? <span style={{ color: EMA }}>○ EMA20</span> : null}
            {indicators.vwap ? <span style={{ color: VWAP }}>○ VWAP</span> : null}
          </div>
          {loading ? (
            <p>
              Loading {selectedFrom && selectedTo && selectedFrom !== selectedTo
                ? `${formatIstDate(selectedFrom)} → ${formatIstDate(selectedTo)}`
                : "historical candles and signals"}
              … 1-minute month ranges can take up to a minute.
            </p>
          ) : null}
          {noStrategy ? <p className="warn">No strategy selected. Turn on Index BUY and/or Index SELL to generate signals.</p> : null}
          {!loading && chart?.option_data_available === false ? (
            <p className="warn">Option history unavailable. Chart BUY/SELL is on NIFTY/SENSEX itself. Green ⇈ is long the index, red ⇊ is short the index. HYPOTHETICAL / PAPER only.</p>
          ) : null}
          {!historical ? <ChartStrikeTrade instrument={instrument} enabled={!historical} /> : null}
          {!loading && historical && chart?.data_status === "NO_DATA" ? <p className="warn">No historical data found for the selected period.</p> : null}
          {!loading && chart?.data_status === "NO_SESSION" ? <p className="warn">No session. The selected date is a weekend, holiday, or has no stored candles. Nothing is invented.</p> : null}
          {!loading && !noStrategy && candles.length > 0 && signals.length === 0 ? <p className="muted">No signals generated for the selected period.</p> : null}
          {!loading && start && end && start > end ? <p className="error">Invalid date range.</p> : null}
          <ChartCanvas
            candles={candles}
            indicatorsData={slicedIndicators(chart?.indicators || {}, cutoffTime, replay)}
            indicators={indicators}
            signals={signals}
            selected={selected}
            levels={levels}
            lockScale={replay && playing}
            onSelect={setSelectedId}
          />
          {selected ? <SignalDetails signal={selected} /> : <p className="muted">Click a double arrow for hypothetical signal details.</p>}
          {calendarSignals.length ? (
            <div className="chart-results">
              <h3>Matching calendar sessions</h3>
              <p className="muted">{patternLabel || "Selected pattern"} · markers use the first candle timestamp of each Asia/Kolkata session. Open-to-close return is INDEX close, not option premium.</p>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th className="left">Session</th>
                      <th>Open</th>
                      <th>Close</th>
                      <th>Return %</th>
                      <th>Candle unix</th>
                    </tr>
                  </thead>
                  <tbody>
                    {calendarSignals.map((row: any) => (
                      <tr key={row.signal_id}>
                        <td className="left">{row.session_date}</td>
                        <td>{fmt(row.entry_price)}</td>
                        <td>{fmt(row.exit_price)}</td>
                        <td>{row.net_pnl == null ? "—" : Number(row.net_pnl).toFixed(4)}</td>
                        <td>{row.time_unix}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}
          <ResultsPanel summary={chart?.summary} rejected={chart?.rejected || []} />
        </section>
      </div>
    </>
  );
}

function ResultsPanel({ summary, rejected }: { summary: any; rejected: any[] }) {
  if (!summary) return null;
  return (
    <div className="chart-results">
      <h3>Historical / paper results</h3>
      <p className="muted">{summary.note}</p>
      <p>Candles {fmt(summary.candles)} · CE BUY {fmt(summary.ce_buy)} · PE BUY {fmt(summary.pe_buy)} · CE SELL {fmt(summary.ce_sell)} · PE SELL {fmt(summary.pe_sell)}</p>
      <p>Wins {fmt(summary.winning)} · Losses {fmt(summary.losing)} · Open {fmt(summary.open)} · Win rate {summary.win_rate == null ? "—" : `${fmt(summary.win_rate)}%`}</p>
      <p>Avg return {fmt(summary.average_return)} · Paper P&amp;L {fmt(summary.net_pnl)} · Max drawdown {fmt(summary.max_drawdown)}</p>
      <p>Rejected candidates {fmt(summary.rejected)}</p>
      {(summary.rejection_reasons || []).length ? (
        <p className="muted">Strategy validation: {(summary.rejection_reasons || []).map((row: any) => `${row.reason} (${row.count})`).join(" · ")}</p>
      ) : null}
      {rejected.length ? <p className="muted">Latest WAIT sample uses only the candle available at that timestamp.</p> : null}
    </div>
  );
}

function StrategySwitch({
  label, on, score, onToggle,
}: {
  label: string;
  on: boolean;
  score: any;
  onToggle: (value: boolean) => void;
}) {
  const passed = score?.passed ?? "—";
  const total = score?.total ?? "—";
  const pct = score?.score_percent == null ? "—" : `${Number(score.score_percent).toFixed(2)}%`;
  return (
    <div className="strategy-switch">
      <p><strong>{label}</strong> <span className={on ? "pass" : "fail"}>● {on ? "ON" : "OFF"}</span></p>
      <p className="muted">{passed}/{total} · {pct}</p>
      <button type="button" onClick={() => onToggle(!on)}>{on ? "Turn OFF" : "Turn ON"}</button>
    </div>
  );
}

function ChartCanvas({
  candles, indicatorsData, indicators, signals, selected, levels, lockScale, onSelect,
}: {
  candles: any[];
  indicatorsData: any;
  indicators: Indicators;
  signals: any[];
  selected: any;
  levels: Levels;
  lockScale?: boolean;
  onSelect: (id: string) => void;
}) {
  const host = useRef<HTMLDivElement | null>(null);
  const rsiHost = useRef<HTMLDivElement | null>(null);
  const atrHost = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<any>(null);
  const candleRef = useRef<any>(null);
  const lines = useRef<any[]>([]);
  const [hover, setHover] = useState<any>(null);
  const candleTimes = useMemo(() => indexCandleTimes(candles), [candles]);

  const candleData = useMemo(
    () => candles.map((row) => ({
      time: toChartTime(Number(row.time)),
      open: row.open, high: row.high, low: row.low, close: row.close,
    })),
    [candles],
  );

  useEffect(() => {
    if (!host.current) return;
    const chart = createChart(host.current, {
      layout: { background: { type: ColorType.Solid, color: "#141816" }, textColor: "#c8bfb2" },
      grid: { vertLines: { color: "#24302a" }, horzLines: { color: "#24302a" } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "#3c463f" },
      localization: { locale: "en-IN", timeFormatter: formatChartTime },
      timeScale: {
        borderColor: "#3c463f",
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: formatChartTick,
      },
      autoSize: true,
    });
    const series = chart.addCandlestickSeries({
      upColor: "#2f7d4a", downColor: "#8a4b32", borderVisible: false, wickUpColor: "#2f7d4a", wickDownColor: "#8a4b32",
    });
    chartRef.current = chart;
    candleRef.current = series;
    const resize = () => chart.applyOptions({ width: host.current?.clientWidth, height: host.current?.clientHeight });
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(host.current);
    return () => {
      observer.disconnect();
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!candleRef.current) return;
    candleRef.current.setData(candleData);
    if (!lockScale) chartRef.current?.timeScale().fitContent();
  }, [candleData, lockScale]);

  useEffect(() => {
    const chart = chartRef.current;
    const series = candleRef.current;
    if (!chart || !series) return;
    const extra: any[] = [];
    if (indicators.ema20) {
      const line = chart.addLineSeries({ color: EMA, lineWidth: 2, title: "EMA20" });
      line.setData((indicatorsData.ema20 || []).map((row: any) => ({ time: toChartTime(Number(row.time)), value: row.value })));
      extra.push(line);
    }
    if (indicators.vwap) {
      const line = chart.addLineSeries({ color: VWAP, lineWidth: 2, title: "VWAP" });
      line.setData((indicatorsData.vwap || []).map((row: any) => ({ time: toChartTime(Number(row.time)), value: row.value })));
      extra.push(line);
    }
    if (indicators.volume) {
      const hist = chart.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" } });
      hist.priceScale().applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } });
      hist.setData((indicatorsData.volume || []).map((row: any) => ({ time: toChartTime(Number(row.time)), value: row.value, color: row.color })));
      extra.push(hist);
    }
    return () => {
      extra.forEach((item) => {
        try { chart.removeSeries(item); } catch { /* already removed */ }
      });
    };
  }, [indicators.ema20, indicators.vwap, indicators.volume, indicatorsData]);

  useEffect(() => {
    const pane = rsiHost.current;
    if (!pane || !indicators.rsi) return;
    const chart = createChart(pane, {
      height: 90,
      layout: { background: { type: ColorType.Solid, color: "#141816" }, textColor: "#c8bfb2" },
      grid: { vertLines: { color: "#24302a" }, horzLines: { color: "#24302a" } },
      timeScale: { visible: false },
      rightPriceScale: { borderColor: "#3c463f" },
      autoSize: true,
    });
    const line = chart.addLineSeries({ color: "#d6c49a", lineWidth: 2 });
    line.setData((indicatorsData.rsi || []).map((row: any) => ({ time: toChartTime(Number(row.time)), value: row.value })));
    return () => chart.remove();
  }, [indicators.rsi, indicatorsData.rsi]);

  useEffect(() => {
    const pane = atrHost.current;
    if (!pane || !indicators.atr) return;
    const chart = createChart(pane, {
      height: 90,
      layout: { background: { type: ColorType.Solid, color: "#141816" }, textColor: "#c8bfb2" },
      grid: { vertLines: { color: "#24302a" }, horzLines: { color: "#24302a" } },
      timeScale: { visible: false },
      rightPriceScale: { borderColor: "#3c463f" },
      autoSize: true,
    });
    const line = chart.addLineSeries({ color: "#e6b17a", lineWidth: 2 });
    line.setData((indicatorsData.atr || []).map((row: any) => ({ time: toChartTime(Number(row.time)), value: row.value })));
    return () => chart.remove();
  }, [indicators.atr, indicatorsData.atr]);

  useEffect(() => {
    const series = candleRef.current;
    if (!series) return;
    lines.current.forEach((line) => series.removePriceLine(line));
    lines.current = [];
    const add = (price: number | null | undefined, title: string, color: string, show: boolean) => {
      if (!show || price == null || Number.isNaN(Number(price))) return;
      lines.current.push(series.createPriceLine({ price: Number(price), title, color, lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: true }));
    };
    if (selected) {
      add(selected.entry_price, "ENTRY", "#c8bfb2", levels.entry);
      add(selected.target_price, "TARGET", "#5aa8ff", levels.target);
      add(selected.stop_loss_price, "STOP LOSS", "#e08a3c", levels.stop);
      add(selected.trail_price, "TRAIL", "#9b7dff", levels.stop && selected.trail_price != null);
      add(selected.exit_price, "EXIT", SELL_CE, levels.exit);
    }
    for (const row of signals.filter((item: any) => item.source === "LIVE_ZERODHA" && item.strike != null)) {
      add(Number(row.strike), row.live_marker || "LIVE", markerColor(row), true);
    }
  }, [selected, levels, signals]);

  useEffect(() => {
    const chart = chartRef.current;
    const series = candleRef.current;
    if (!chart || !series) return;
    const markers = signals
      .map((signal) => toSeriesMarker(signal, candleTimes))
      .filter((row): row is NonNullable<typeof row> => Boolean(row))
      .sort((left, right) => left.time - right.time);
    series.setMarkers(markers);
    const pick = (param: any) => {
      const id = param?.hoveredObjectId;
      if (id) return dataSignals(signals, String(id));
      const time = fromChartTime(Number(param?.time));
      if (!Number.isFinite(time)) return null;
      return signals.find((row) => snapUnix(Number(row.time_unix), candleTimes) === time) || null;
    };
    const onClick = (param: any) => {
      const match = pick(param);
      if (!match) return;
      onSelect(match.signal_id);
      setHover(match);
    };
    const onMove = (param: any) => {
      const match = pick(param);
      setHover((current: any) => (current?.signal_id === match?.signal_id ? current : match));
    };
    chart.subscribeClick(onClick);
    chart.subscribeCrosshairMove(onMove);
    return () => {
      try { series.setMarkers([]); } catch { /* series already removed */ }
      chart.unsubscribeClick(onClick);
      chart.unsubscribeCrosshairMove(onMove);
    };
  }, [signals, candleTimes, onSelect]);

  return (
    <>
      <div className="chart-actions">
        <button type="button" onClick={() => chartRef.current?.timeScale().fitContent()}>Fit content</button>
        <button type="button" onClick={() => chartRef.current?.timeScale().resetTimeScale()}>Reset zoom</button>
      </div>
      <div className="tv-wrap">
        <div ref={host} className="tv-chart" />
        {hover ? <HoverCard signal={hover} /> : null}
      </div>
      {indicators.oi ? <OiStrip points={indicatorsData.oi || []} /> : null}
      {indicators.rsi ? <div ref={rsiHost} className="tv-pane"><span className="pane-label">RSI</span></div> : null}
      {indicators.atr ? <div ref={atrHost} className="tv-pane"><span className="pane-label">ATR</span></div> : null}
    </>
  );
}

function OiStrip({ points }: { points: { time: number; value: number }[] }) {
  if (!points.length) return <p className="muted">OI is unavailable for this series.</p>;
  const last = points[points.length - 1];
  return <p className="muted">OI last {fmt(last.value)}</p>;
}

function HoverCard({ signal }: { signal: any }) {
  return (
    <div className="chart-tip">
      <strong>HYPOTHETICAL {signal.signal_type} {signal.option_type}</strong>
      <p>{signal.instrument} · {signal.strategy_name || `${signal.option_type} Strategy`}</p>
      <p>{signal.tradingsymbol || `${signal.instrument} ${fmt(signal.strike)} ${signal.option_type}`}</p>
      {signal.expiry ? <p>Expiry: {formatIstDate(String(signal.expiry))}</p> : null}
      <p>Strike: {fmt(signal.strike)}</p>
      <p>Signal: {formatIstDateTime(signal.timestamp)}</p>
      <p>Candle: {formatIstDateTime(signal.candle_timestamp)}</p>
      <p>Entry: ₹{fmt(signal.entry_price)} · Exit: ₹{fmt(signal.exit_price)}</p>
      <p>SL: ₹{fmt(signal.stop_loss_price)} · Target: ₹{fmt(signal.target_price)}</p>
      <p>Score: {signal.strategy_score == null ? "—" : `${Number(signal.strategy_score).toFixed(2)}%`} · {signal.passed_conditions}/{signal.total_conditions}</p>
      <p>EMA {fmt(signal.ema)} · VWAP {fmt(signal.vwap)} · RSI {fmt(signal.rsi)}</p>
      <p>Result: {signal.result || "NOT EVALUATED"}</p>
      <p>{signal.signal_reason || signal.rejection_reason || signal.exit_reason || "—"}</p>
    </div>
  );
}

function SignalDetails({ signal }: { signal: any }) {
  return (
    <div className="chart-details">
      <h3>HYPOTHETICAL {signal.signal_type} {signal.option_type}</h3>
      <p className="paper">HYPOTHETICAL / PAPER signals only. No broker orders.</p>
      <p>{signal.instrument} · {signal.strategy_name || `${signal.option_type} Strategy`} · {signal.result || signal.exit_reason || "OPEN"}</p>
      <p>{signal.tradingsymbol} · Strike {fmt(signal.strike)} · Expiry {signal.expiry ? formatIstDate(String(signal.expiry)) : "—"}</p>
      <p>Score {signal.strategy_score == null ? "—" : `${Number(signal.strategy_score).toFixed(2)}%`} · BUY {fmt(signal.ce_score ?? signal.buy_score)} · SELL {fmt(signal.pe_score ?? signal.sell_score)} · {signal.passed_conditions}/{signal.total_conditions} · threshold {fmt(signal.threshold)}</p>
      <p>Entry ₹{fmt(signal.entry_price)} · Exit ₹{fmt(signal.exit_price)} · SL ₹{fmt(signal.stop_loss_price)} · Target ₹{fmt(signal.target_price)}</p>
      <p>Gross {fmt(signal.gross_pnl)} · Charges {fmt(signal.total_charges)} · NET {fmt(signal.net_pnl)}</p>
      <p>Signal {formatIstDateTime(signal.timestamp)} · Candle {formatIstDateTime(signal.candle_timestamp)}</p>
      <p>EMA {fmt(signal.ema)} · VWAP {fmt(signal.vwap)} · RSI {fmt(signal.rsi)} · ATR {fmt(signal.atr)}</p>
      <p>{signal.signal_reason || signal.exit_reason || signal.rejection_reason || "—"}</p>
      {signal.sequential ? <p className="muted">Generated sequentially at this candle. No look-ahead.</p> : null}
    </div>
  );
}

function slicedIndicators(series: Record<string, any[]>, cutoff: number, replay: boolean) {
  if (!replay || !cutoff) return series;
  const out: Record<string, any[]> = {};
  for (const [key, rows] of Object.entries(series)) {
    out[key] = (rows || []).filter((row) => Number(row.time) <= cutoff);
  }
  return out;
}

function visibleSignals(rows: any[], filters: Filters) {
  return rows.filter((row) => {
    if (row.signal_type === "CALENDAR") return true;
    if (row.signal_type === "BUY" && !filters.buy) return false;
    if (row.signal_type === "SELL" && !filters.sell) return false;
    if (row.option_type === "CE" && !filters.ce) return false;
    if (row.option_type === "PE" && !filters.pe) return false;
    if (row.option_type === "BUY" && !filters.buy) return false;
    if (row.option_type === "SELL" && !filters.sell) return false;
    return true;
  });
}

function dataSignals(rows: any[], id: string) {
  return rows.find((row) => row.signal_id === id) || null;
}

function markerColor(signal: any) {
  if (signal.source === "LIVE_ZERODHA" || String(signal.marker_role || "").startsWith("live_")) {
    if (signal.live_marker === "LIVE BUY PE" || signal.marker_role === "live_buy_pe") return BUY_PE;
    if (signal.live_marker === "LIVE SELL" || signal.marker_role === "live_sell") return SELL_CE;
    if (signal.live_marker === "POSITION CLOSED" || signal.marker_role === "live_closed") return "#9b7dff";
    return BUY_CE;
  }
  if (signal.signal_type === "CALENDAR") return "#8ab4f8";
  const isExit = signal.marker_role === "exit" || /\|EXIT$/.test(String(signal.signal_id || ""));
  const reason = String(signal.exit_reason || "");
  if (isExit) {
    if (/TARGET/.test(reason)) return "#5aa8ff";
    if (/TRAIL/.test(reason)) return "#9b7dff";
    if (/STOP/.test(reason)) return "#e08a3c";
    if (/TIME|FORCE|END_OF|SQUARE/.test(reason)) return "#9b7dff";
    return "#9b7dff";
  }
  if (signal.signal_type === "BUY" || signal.option_type === "BUY") return BUY_CE;
  if (signal.option_type === "SELL") return SELL_CE;
  if (signal.signal_type === "BUY") return signal.option_type === "PE" ? BUY_PE : BUY_CE;
  return signal.option_type === "PE" ? SELL_PE : SELL_CE;
}

function isExitMarker(signal: any) {
  return signal.marker_role === "exit" || /\|EXIT$/.test(String(signal.signal_id || ""));
}

function isBuyMarker(signal: any) {
  if (signal.signal_type === "CALENDAR") return true;
  return signal.signal_type === "BUY" || signal.option_type === "BUY" || signal.option_type === "CE";
}

function markerText(signal: any) {
  if (signal.live_marker) return signal.live_marker.replace("LIVE ", "");
  if (signal.signal_type === "CALENDAR") return "DAY";
  if (isExitMarker(signal)) {
    const reason = String(signal.exit_reason || "");
    if (/TARGET/.test(reason)) return "T";
    if (/STOP/.test(reason)) return "SL";
    if (/TIME|FORCE|END_OF|SQUARE/.test(reason)) return "SQ";
    if (/INVALID/.test(reason)) return "X";
    return "EX";
  }
  const side = isBuyMarker(signal) ? "BUY" : "SELL";
  if (signal.option_type === "CE" || signal.option_type === "PE") return `${side} ${signal.option_type}`;
  return side;
}

function indexCandleTimes(candles: any[]) {
  const set = new Set<number>();
  const list: number[] = [];
  for (const row of candles) {
    const time = Number(row.time);
    if (!Number.isFinite(time) || set.has(time)) continue;
    set.add(time);
    list.push(time);
  }
  return { set, list };
}

function snapUnix(unix: number, candleTimes: { set: Set<number>; list: number[] }) {
  if (!Number.isFinite(unix)) return null;
  if (candleTimes.set.has(unix)) return unix;
  let best: number | null = null;
  let dist = 90;
  for (const time of candleTimes.list) {
    const delta = Math.abs(time - unix);
    if (delta < dist) {
      dist = delta;
      best = time;
    }
  }
  return best;
}

function toSeriesMarker(signal: any, candleTimes: { set: Set<number>; list: number[] }) {
  const time = snapUnix(Number(signal.time_unix), candleTimes);
  if (time == null) return null;
  if (signal.signal_type === "CALENDAR") {
    return {
      time: toChartTime(time),
      id: signal.signal_id,
      position: "belowBar",
      color: "#8ab4f8",
      shape: "square",
      text: "DAY",
      size: 1,
    };
  }
  const exit = isExitMarker(signal);
  const buy = !exit && isBuyMarker(signal);
  return {
    time: toChartTime(time),
    id: signal.signal_id,
    position: exit ? "inBar" : buy ? "belowBar" : "aboveBar",
    color: markerColor(signal),
    shape: exit ? "circle" : buy ? "arrowUp" : "arrowDown",
    text: markerText(signal),
    size: exit ? 1 : 2,
  };
}

function toChartTime(unix: number) {
  return Math.floor(unix) + IST_OFFSET_SEC;
}

function fromChartTime(chartTime: number) {
  return Number(chartTime) - IST_OFFSET_SEC;
}

function utcParts(chartTime: number) {
  const stamp = new Date(Math.floor(chartTime) * 1000);
  return {
    year: stamp.getUTCFullYear(),
    month: stamp.getUTCMonth(),
    day: stamp.getUTCDate(),
    hour: stamp.getUTCHours(),
    minute: stamp.getUTCMinutes(),
    second: stamp.getUTCSeconds(),
  };
}

function formatChartTime(time: number) {
  const { day, month, year, hour, minute } = utcParts(Number(time));
  return `${pad(day)} ${MONTHS[month]} ${year} ${pad(hour)}:${pad(minute)} IST`;
}

function formatChartTick(time: number, tickMarkType: TickMarkType) {
  const { day, month, year, hour, minute } = utcParts(Number(time));
  if (tickMarkType === TickMarkType.Year) return String(year);
  if (tickMarkType === TickMarkType.Month) return MONTHS[month];
  if (tickMarkType === TickMarkType.DayOfMonth) return `${pad(day)} ${MONTHS[month]}`;
  return `${pad(hour)}:${pad(minute)}`;
}

function formatIstDateTime(value: unknown) {
  if (value == null || value === "") return "—";
  const raw = String(value).trim();
  if (/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}/.test(raw) && !/[zZ]|[+-]\d{2}:?\d{2}$/.test(raw)) {
    const [day, clock] = raw.replace("T", " ").split(" ");
    return `${formatIstDate(day)} ${String(clock).slice(0, 8)} IST`;
  }
  const date = new Date(raw.includes("T") || raw.includes("Z") || /[+-]\d{2}:?\d{2}$/.test(raw) ? raw : raw.replace(" ", "T") + "Z");
  if (Number.isNaN(date.getTime())) return raw;
  const text = new Intl.DateTimeFormat("en-GB", {
    timeZone: IST_ZONE,
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
  return `${text} IST`;
}

function formatIstDate(value: string) {
  const match = String(value || "").match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!match) return value || "—";
  return `${match[3]} ${MONTHS[Number(match[2]) - 1]} ${match[1]}`;
}

function pad(value: number) {
  return String(value).padStart(2, "0");
}

function monthOptions(minDate: string, maxDate: string) {
  if (!minDate || !maxDate) return [];
  const out: { value: string; label: string }[] = [];
  let year = Number(minDate.slice(0, 4));
  let month = Number(minDate.slice(5, 7));
  const endYear = Number(maxDate.slice(0, 4));
  const endMonth = Number(maxDate.slice(5, 7));
  while (year < endYear || (year === endYear && month <= endMonth)) {
    const value = `${year}-${pad(month)}`;
    out.push({ value, label: `${MONTHS[month - 1]} ${year}` });
    month += 1;
    if (month > 12) {
      month = 1;
      year += 1;
    }
  }
  return out;
}

function shiftIso(iso: string, days: number) {
  const [year, month, day] = iso.split("-").map(Number);
  const stamp = new Date(Date.UTC(year, month - 1, day + days));
  return `${stamp.getUTCFullYear()}-${pad(stamp.getUTCMonth() + 1)}-${pad(stamp.getUTCDate())}`;
}

function clampDate(value: string, min?: string, max?: string) {
  if (!value) return value;
  if (min && value < min) return min;
  if (max && value > max) return max;
  return value;
}

function istDateFromUnix(unix: number) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: IST_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(Math.floor(unix) * 1000));
}

function calendarSessionSignals(candles: any[], dates: string[], pattern: string) {
  const wanted = new Set(dates);
  if (!wanted.size) return [];
  const seen = new Set<string>();
  const out: any[] = [];
  for (const row of candles) {
    const unix = Number(row.time);
    if (!Number.isFinite(unix)) continue;
    const day = istDateFromUnix(unix);
    if (!wanted.has(day) || seen.has(day)) continue;
    seen.add(day);
    const openPx = Number(row.open);
    const closePx = Number(row.close);
    const oc = openPx ? ((closePx - openPx) / openPx) * 100 : null;
    out.push({
      signal_id: `calendar-${day}`,
      signal_type: "CALENDAR",
      option_type: "",
      time_unix: unix,
      session_date: day,
      timestamp: day,
      entry_price: openPx,
      exit_price: closePx,
      net_pnl: oc,
      note: `${pattern || "Calendar session"} ${day} open-to-close ${oc == null ? "—" : oc.toFixed(4)}% INDEX. Exact candle unix ${unix}. Asia/Kolkata.`,
      is_hypothetical: true,
    });
  }
  return out;
}
