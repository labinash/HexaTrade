import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "./api";
import { DateField } from "./fields";
import { fmt, fmt4 } from "./lib";

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"];
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const HIGHLIGHT_KEY = "hextrade-calendar-highlight";

export function CalendarPatterns() {
  const [instrument, setInstrument] = useState("BOTH");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [timeframe, setTimeframe] = useState("1Minute");
  const [weekday, setWeekday] = useState("ALL");
  const [weekOfMonth, setWeekOfMonth] = useState("ALL");
  const [weekdayOrdinal, setWeekdayOrdinal] = useState("ALL");
  const [month, setMonth] = useState("ALL");
  const [quarter, setQuarter] = useState("ALL");
  const [expiryProximity, setExpiryProximity] = useState("ALL");
  const [regime, setRegime] = useState("ALL");
  const [minSample, setMinSample] = useState(20);
  const [heatMetric, setHeatMetric] = useState("avg_open_to_close_pct");
  const [focus, setFocus] = useState("NIFTY");
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const requestId = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

  const path = useMemo(() => {
    const query = new URLSearchParams({
      instrument,
      timeframe,
      min_sample: String(minSample || 20),
    });
    if (dateFrom) query.set("date_from", dateFrom);
    if (dateTo) query.set("date_to", dateTo);
    if (weekday !== "ALL") query.set("weekday", weekday);
    if (weekOfMonth !== "ALL") query.set("week_of_month", weekOfMonth);
    if (weekdayOrdinal !== "ALL") query.set("weekday_ordinal", weekdayOrdinal);
    if (month !== "ALL") query.set("month", month);
    if (quarter !== "ALL") query.set("quarter", quarter);
    if (expiryProximity !== "ALL") query.set("expiry_proximity", expiryProximity);
    if (regime !== "ALL") query.set("regime", regime);
    return `/api/calendar-patterns?${query.toString()}`;
  }, [instrument, timeframe, minSample, dateFrom, dateTo, weekday, weekOfMonth, weekdayOrdinal, month, quarter, expiryProximity, regime]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      const id = ++requestId.current;
      setLoading(true);
      setError("");
      const watchdog = window.setTimeout(() => controller.abort(), 180000);
      api<any>(path, { signal: controller.signal })
        .then((value) => {
          if (id !== requestId.current) return;
          setData(value);
          const names = Object.keys(value?.instruments || {});
          setFocus((current) => (names.length && !names.includes(current) ? names[0] : current));
        })
        .catch((err) => {
          if (id !== requestId.current) return;
          if (err?.name === "AbortError") {
            setError("Request timed out. Filters were applied again, or the archive query is still running.");
            return;
          }
          setError(err.message);
        })
        .finally(() => {
          window.clearTimeout(watchdog);
          if (id === requestId.current) setLoading(false);
        });
    }, 450);
    return () => {
      window.clearTimeout(timer);
      abortRef.current?.abort();
    };
  }, [path]);

  const report = data?.instruments?.[focus] || Object.values(data?.instruments || {})[0] || null;
  const names = Object.keys(data?.instruments || {});

  return (
    <>
      <h1>Calendar patterns</h1>
      <p className="paper">HYPOTHETICAL / PAPER analysis of stored INDEX sessions. A positive historical average is not a prediction. No broker orders. Patterns are not converted into strategies.</p>
      {data?.proxy_note ? <p className="warn">{data.proxy_note}</p> : null}
      {data?.option_note ? <p className="muted">{data.option_note}</p> : null}
      {data?.multiple_testing_warning ? <p className="warn">{data.multiple_testing_warning} Groups tested: {fmt(data.groups_tested)}.</p> : null}

      <section className="card">
        <h2>Filters</h2>
        <div className="form">
          <label>Instrument
            <select value={instrument} onChange={(event) => setInstrument(event.target.value)}>
              <option value="BOTH">Both</option>
              <option value="NIFTY">NIFTY</option>
              <option value="SENSEX">SENSEX</option>
            </select>
          </label>
          <label>From date
            <DateField value={dateFrom} onChange={setDateFrom} />
          </label>
          <label>To date
            <DateField value={dateTo} onChange={setDateTo} />
          </label>
          <label>Timeframe
            <select value={timeframe} onChange={(event) => setTimeframe(event.target.value)}>
              <option value="1Minute">1Minute (daily OHLC)</option>
              <option value="3Minute">3Minute source note</option>
              <option value="5Minute">5Minute source note</option>
              <option value="15Minute">15Minute source note</option>
            </select>
          </label>
          <label>Weekday
            <select value={weekday} onChange={(event) => setWeekday(event.target.value)}>
              <option value="ALL">All</option>
              {WEEKDAYS.map((name) => <option key={name}>{name}</option>)}
            </select>
          </label>
          <label>Week of month (calendar)
            <select value={weekOfMonth} onChange={(event) => setWeekOfMonth(event.target.value)}>
              <option value="ALL">All</option>
              <option>First</option>
              <option>Second</option>
              <option>Third</option>
              <option>Fourth</option>
              <option>Last</option>
            </select>
          </label>
          <label>Trading day of the week in month
            <select value={weekdayOrdinal} onChange={(event) => setWeekdayOrdinal(event.target.value)}>
              <option value="ALL">All</option>
              <option>First</option>
              <option>Second</option>
              <option>Third</option>
              <option>Fourth</option>
              <option>Last</option>
            </select>
          </label>
          <label>Month
            <select value={month} onChange={(event) => setMonth(event.target.value)}>
              <option value="ALL">All</option>
              {MONTHS.map((name) => <option key={name}>{name}</option>)}
            </select>
          </label>
          <label>Quarter
            <select value={quarter} onChange={(event) => setQuarter(event.target.value)}>
              <option value="ALL">All</option>
              <option>Q1</option>
              <option>Q2</option>
              <option>Q3</option>
              <option>Q4</option>
            </select>
          </label>
          <label>Expiry proximity
            <select value={expiryProximity} onChange={(event) => setExpiryProximity(event.target.value)}>
              <option value="ALL">All</option>
              <option value="EXPIRY_DAY">Expiry day</option>
              <option value="ONE_BEFORE_EXPIRY">One trading day before expiry</option>
              <option value="ONE_AFTER_EXPIRY">One trading day after expiry</option>
              <option value="NON_EXPIRY">Non-expiry day</option>
            </select>
          </label>
          <label>Market regime
            <select value={regime} onChange={(event) => setRegime(event.target.value)}>
              <option value="ALL">All</option>
              <option value="BULLISH">Bullish previous close</option>
              <option value="BEARISH">Bearish previous close</option>
              <option value="SIDEWAYS">Sideways previous close</option>
              <option value="GAP_UP">Gap-up open</option>
              <option value="GAP_DOWN">Gap-down open</option>
              <option value="HIGH_VOL">High-volatility previous session</option>
              <option value="LOW_VOL">Low-volatility previous session</option>
            </select>
          </label>
          <label>Minimum sample size
            <input type="number" min={1} value={minSample} onChange={(event) => setMinSample(Number(event.target.value) || 20)} />
          </label>
        </div>
        <p className="muted">{data?.timeframe_note || "Calendar sessions are daily OHLC from stored 1-minute INDEX bars."} Timezone {data?.timezone || "Asia/Kolkata"}.</p>
        <p className="muted">Returns: open-to-close percent on INDEX close (futures proxy). Close-to-close is labelled separately. Option premium is not mixed in.</p>
      </section>

      {loading ? <p>{data ? "Updating calendar analysis…" : "Loading stored calendar sessions…"}</p> : null}
      {error ? <p className="error">{error}</p> : null}
      {!loading && !error && data && !report ? <p className="warn">No stored INDEX sessions for the selected filters.</p> : null}

      {data && names.length > 1 ? (
        <div className="row">
          {names.map((name) => (
            <button key={name} type="button" className={focus === name ? "primary" : ""} onClick={() => setFocus(name)}>{name}</button>
          ))}
        </div>
      ) : null}

      {report ? (
        <>
          <p className="muted">
            {report.instrument} {data.date_from} → {data.date_to} · {fmt(report.session_count)} filtered / {fmt(report.available_sessions)} stored sessions
            · holidays or missing weekdays {fmt(report.holiday_count)}
            · missing 1m bars (est.) {fmt(report.missing_minute_bars_estimate)}
            · duplicate timestamps {fmt(report.duplicate_timestamps)}
            · out-of-order {fmt(report.out_of_order)}
          </p>
          {data.expiry_analysis === "unavailable" ? <p className="warn">{data.expiry_note}</p> : null}

          <WeekdayTable report={report} instrument={focus} />
          <Heatmap rows={report.weekday_by_month || []} metric={heatMetric} onMetric={setHeatMetric} instrument={focus} />
          <WeekOfMonth rows={report.week_of_month || []} instrument={focus} />
          <MarketConditions rows={report.market_conditions || []} instrument={focus} />
          {data.expiry_analysis === "available" ? <ExpiryTable rows={report.expiry || []} /> : null}
          <PaperWeekdays paper={report.paper_by_weekday || {}} />
          <YearTable rows={report.weekday_by_year || []} />
        </>
      ) : null}

      {data?.nifty_vs_sensex ? <NiftySensex comparison={data.nifty_vs_sensex} /> : null}
      {data?.pattern_ranking ? <Ranking rows={data.pattern_ranking} /> : null}
    </>
  );
}

function WeekdayTable({ report, instrument }: { report: any; instrument: string }) {
  return (
    <section className="card">
      <h2>Weekday comparison</h2>
      <p className="muted">Monday through Friday on {instrument}. Average/median are open-to-close percent. Out-of-sample is the last 30% of the selected range.</p>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th className="left">Weekday</th>
              <th>Sessions</th>
              <th>Positive %</th>
              <th>Average return</th>
              <th>Median return</th>
              <th>Volatility</th>
              <th>Net paper P&amp;L</th>
              <th>Win rate</th>
              <th>Maximum drawdown</th>
              <th>Out-of-sample</th>
              <th className="left">Reliability</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {(report.weekday_table || []).map((row: any) => (
              <tr key={row.weekday}>
                <td className="left">{row.weekday}</td>
                <td>{fmt(row.sessions)}</td>
                <td>{pct(row.positive_session_pct)}</td>
                <td>{num(row.avg_open_to_close_pct)}</td>
                <td>{num(row.median_open_to_close_pct)}</td>
                <td>{num(row.volatility)}</td>
                <td>{money(row.net_paper_pnl)}</td>
                <td>{pct(row.paper_win_rate ?? row.positive_session_pct)}</td>
                <td>{num(row.max_drawdown_pct)}</td>
                <td>{oosCell(row)}</td>
                <td className="left">{statusChip(row.reliability)}</td>
                <td><ChartLink instrument={instrument} row={row} label={row.weekday} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Heatmap({ rows, metric, onMetric, instrument }: { rows: any[]; metric: string; onMetric: (value: string) => void; instrument: string }) {
  const lookup = new Map(rows.map((row) => [`${row.weekday}|${row.month}`, row]));
  return (
    <section className="card">
      <h2>Weekday-by-month heatmap</h2>
      <div className="row">
        <label>Metric
          <select value={metric} onChange={(event) => onMetric(event.target.value)}>
            <option value="avg_open_to_close_pct">Average return</option>
            <option value="positive_session_pct">Positive-session percentage</option>
            <option value="net_paper_pnl">Net P&amp;L</option>
            <option value="paper_win_rate">Win rate</option>
            <option value="volatility">Volatility</option>
          </select>
        </label>
      </div>
      <div className="table-scroll">
        <table className="heatmap">
          <thead>
            <tr>
              <th className="left">Weekday</th>
              {MONTHS.map((name) => <th key={name}>{name.slice(0, 3)}</th>)}
            </tr>
          </thead>
          <tbody>
            {WEEKDAYS.map((day) => (
              <tr key={day}>
                <td className="left">{day}</td>
                {MONTHS.map((monthName) => {
                  const row = lookup.get(`${day}|${monthName}`);
                  const value = heatValue(row, metric);
                  return (
                    <td key={monthName} style={{ background: heatColor(value, metric) }} title={`${day} ${monthName}: ${num(value)} n=${row?.sessions || 0}`}>
                      {row?.sample_sufficient === false ? "n<" : num(value)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted">{instrument} cells with n below the minimum sample show n&lt;. Color is descriptive only.</p>
    </section>
  );
}

function WeekOfMonth({ rows, instrument }: { rows: any[]; instrument: string }) {
  return (
    <section className="card">
      <h2>Week-of-month analysis</h2>
      <p className="muted">First / Second / Third / Fourth / Last calendar week of the month, for each weekday on {instrument}.</p>
      <GroupedTable rows={rows} groupKey="week_of_month" instrument={instrument} />
    </section>
  );
}

function MarketConditions({ rows, instrument }: { rows: any[]; instrument: string }) {
  return (
    <section className="card">
      <h2>Market-condition analysis</h2>
      <p className="muted">Previous-day close, opening gap, and previous-session volatility. No lookahead: labels use only earlier sessions.</p>
      <GroupedTable rows={rows} groupKey="condition" instrument={instrument} />
    </section>
  );
}

function ExpiryTable({ rows }: { rows: any[] }) {
  return (
    <section className="card">
      <h2>Expiry proximity</h2>
      <p className="muted">Expiry comes from listed instrument.expiry rows. Weekday is not used as a proxy for expiry.</p>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th className="left">Proximity</th>
              <th>Sessions</th>
              <th>Positive %</th>
              <th>Average return</th>
              <th>Median</th>
              <th>Out-of-sample</th>
              <th className="left">Reliability</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row: any) => (
              <tr key={row.expiry_proximity}>
                <td className="left">{row.expiry_proximity}</td>
                <td>{fmt(row.sessions)}</td>
                <td>{pct(row.positive_session_pct)}</td>
                <td>{num(row.avg_open_to_close_pct)}</td>
                <td>{num(row.median_open_to_close_pct)}</td>
                <td>{oosCell(row)}</td>
                <td className="left">{statusChip(row.reliability)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function PaperWeekdays({ paper }: { paper: Record<string, any> }) {
  return (
    <section className="card">
      <h2>Paper strategy by weekday</h2>
      <p className="muted">Existing INDEX paper BUY/SELL only. PE premium is not counted as SELL. P&amp;L is recomputed with index_paper_net (no NFO STT on index levels).</p>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th className="left">Weekday</th>
              <th>BUY signals</th>
              <th>SELL signals</th>
              <th>HOLD / no-trade</th>
              <th>BUY win %</th>
              <th>SELL win %</th>
              <th>BUY net P&amp;L</th>
              <th>SELL net P&amp;L</th>
              <th>Combined net</th>
              <th>Avg trade</th>
              <th>Avg hold (min)</th>
              <th>Max DD</th>
              <th>Stop</th>
              <th>Target</th>
              <th>Time</th>
              <th>Force SQ</th>
            </tr>
          </thead>
          <tbody>
            {WEEKDAYS.map((name) => {
              const row = paper[name] || {};
              return (
                <tr key={name}>
                  <td className="left">{name}</td>
                  <td>{fmt(row.buy_signal_count)}</td>
                  <td>{fmt(row.sell_signal_count)}</td>
                  <td>{fmt(row.hold_count)}</td>
                  <td>{pct(row.buy_win_rate)}</td>
                  <td>{pct(row.sell_win_rate)}</td>
                  <td>{money(row.buy_net_pnl)}</td>
                  <td>{money(row.sell_net_pnl)}</td>
                  <td>{money(row.combined_net_pnl)}</td>
                  <td>{money(row.average_trade_result)}</td>
                  <td>{fmt(row.average_holding_minutes)}</td>
                  <td>{money(row.max_drawdown)}</td>
                  <td>{fmt(row.stop_loss_exits)}</td>
                  <td>{fmt(row.target_exits)}</td>
                  <td>{fmt(row.time_exits)}</td>
                  <td>{fmt(row.force_square_off_exits)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function YearTable({ rows }: { rows: any[] }) {
  return (
    <section className="card">
      <h2>Weekday by year</h2>
      <GroupedTable rows={rows} groupKey="year" instrument="" />
    </section>
  );
}

function GroupedTable({ rows, groupKey, instrument }: { rows: any[]; groupKey: string; instrument: string }) {
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th className="left">Weekday</th>
            <th className="left">{groupKey.replaceAll("_", " ")}</th>
            <th>Sessions</th>
            <th>Positive %</th>
            <th>Avg</th>
            <th>Median</th>
            <th>Vol</th>
            <th>OOS avg</th>
            <th className="left">Reliability</th>
            {instrument ? <th></th> : null}
          </tr>
        </thead>
        <tbody>
          {rows.map((row: any) => (
            <tr key={`${row.weekday}|${row[groupKey]}`}>
              <td className="left">{row.weekday}</td>
              <td className="left">{String(row[groupKey] ?? "")}</td>
              <td>{fmt(row.sessions)}</td>
              <td>{pct(row.positive_session_pct)}</td>
              <td>{num(row.avg_open_to_close_pct)}</td>
              <td>{num(row.median_open_to_close_pct)}</td>
              <td>{num(row.volatility)}</td>
              <td>{oosCell(row)}</td>
              <td className="left">{statusChip(row.reliability)}</td>
              {instrument ? <td><ChartLink instrument={instrument} row={row} label={`${row.weekday} ${row[groupKey]}`} /></td> : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function NiftySensex({ comparison }: { comparison: any }) {
  return (
    <section className="card">
      <h2>NIFTY versus SENSEX</h2>
      <p className="muted">{comparison.note} Aligned sessions: {fmt(comparison.aligned_session_count)}.</p>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th className="left">Weekday</th>
              <th>Same direction %</th>
              <th>Opposite %</th>
              <th>Pearson</th>
              <th>Spearman</th>
              <th>Avg NIFTY</th>
              <th>Avg SENSEX</th>
              <th>Vol difference</th>
              <th>Win-rate difference</th>
            </tr>
          </thead>
          <tbody>
            {(comparison.weekdays || []).map((row: any) => (
              <tr key={row.weekday}>
                <td className="left">{row.weekday}</td>
                <td>{pct(row.same_direction_pct)}</td>
                <td>{pct(row.opposite_direction_pct)}</td>
                <td>{num(row.pearson)}</td>
                <td>{num(row.spearman)}</td>
                <td>{num(row.avg_nifty_return)}</td>
                <td>{num(row.avg_sensex_return)}</td>
                <td>{num(row.volatility_diff)}</td>
                <td>{num(row.win_rate_diff)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Ranking({ rows }: { rows: any[] }) {
  return (
    <section className="card">
      <h2>Pattern ranking</h2>
      <p className="muted">Ranked by out-of-sample net result, then risk-adjusted return, positive-session %, drawdown, and sample size — not by gross profit alone. This is not a trading signal.</p>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th className="left">Pattern</th>
              <th>n</th>
              <th>Avg</th>
              <th>Median</th>
              <th>Win %</th>
              <th>OOS avg</th>
              <th>Net after costs</th>
              <th>Risk adj.</th>
              <th>Max DD</th>
              <th className="left">Reliability</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row: any, index: number) => (
              <tr key={`${row.instrument}|${row.condition}|${index}`}>
                <td>{index + 1}</td>
                <td className="left">
                  {row.pattern_definition}
                  {row.oos_warning ? <div className="fail">{row.oos_warning}</div> : null}
                  {row.outlier_dominated ? <div className="warn">Dominated by one or two large sessions.</div> : null}
                </td>
                <td>{fmt(row.observations)}</td>
                <td>{num(row.average_result)}</td>
                <td>{num(row.median_result)}</td>
                <td>{pct(row.win_rate)}</td>
                <td>{num(row.out_of_sample?.avg_return_pct)}</td>
                <td>{num(row.net_result_after_costs)}</td>
                <td>{num(row.rank_risk_adjusted)}</td>
                <td>{num(row.rank_drawdown)}</td>
                <td className="left">{statusChip(row.reliability)}</td>
                <td><ChartLink instrument={row.instrument} row={row} label={row.condition} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ChartLink({ instrument, row, label }: { instrument: string; row: any; label: string }) {
  const dates: string[] = row.dates || row.full_sample?.dates || [];
  if (!dates.length) return null;
  const sorted = [...dates].sort();
  const end = sorted[sorted.length - 1];
  const start = windowStart(end, 30);
  const href = `/chart?instrument=${encodeURIComponent(instrument)}&mode=HISTORICAL_ANALYSIS&start=${start}&end=${end}&pattern=${encodeURIComponent(String(label))}`;
  return (
    <Link
      to={href}
      onClick={() => {
        window.sessionStorage.setItem(HIGHLIGHT_KEY, JSON.stringify({
          instrument,
          pattern: label,
          dates,
          returns: Object.fromEntries(dates.map((day) => [day, row.avg_open_to_close_pct ?? row.average_result])),
        }));
      }}
    >
      Chart
    </Link>
  );
}

function windowStart(end: string, days: number) {
  const [year, month, day] = end.split("-").map(Number);
  const stamp = new Date(Date.UTC(year, month - 1, day - days));
  const y = stamp.getUTCFullYear();
  const m = String(stamp.getUTCMonth() + 1).padStart(2, "0");
  const d = String(stamp.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function oosCell(row: any) {
  if (row.oos_failed) return <span className="fail">Failed {num(row.out_sample_avg_return_pct)}</span>;
  return num(row.out_sample_avg_return_pct);
}

function statusChip(value: string | undefined) {
  const text = value || "Weak evidence";
  const fail = text.includes("Failed") || text.includes("Insufficient") || text.includes("warning") || text.includes("High");
  return <span className={fail ? "fail" : text === "Stronger evidence" ? "pass" : "warn"}>{text}</span>;
}

function pct(value: unknown) {
  if (value == null || value === "") return "—";
  return `${fmt(value)}%`;
}

function num(value: unknown) {
  if (value == null || value === "") return "—";
  return fmt4(value);
}

function money(value: unknown) {
  if (value == null || value === "") return "—";
  return fmt(value);
}

function heatValue(row: any, metric: string) {
  if (!row) return null;
  if (metric === "net_paper_pnl") return row.net_paper_pnl ?? row.net_after_costs_pct;
  if (metric === "paper_win_rate") return row.paper_win_rate ?? row.positive_session_pct;
  return row[metric];
}

function heatColor(value: unknown, metric: string) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "transparent";
  if (metric === "volatility") {
    const t = Math.max(0, Math.min(1, n / 1.5));
    return `rgba(224, 122, 95, ${0.15 + t * 0.45})`;
  }
  const t = Math.max(-1, Math.min(1, n / (metric.includes("pct") || metric.includes("return") || metric === "paper_win_rate" ? 1.2 : 200)));
  if (t >= 0) return `rgba(125, 206, 160, ${0.12 + t * 0.5})`;
  return `rgba(224, 122, 95, ${0.12 + Math.abs(t) * 0.5})`;
}
