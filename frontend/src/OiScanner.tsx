import { useEffect, useMemo, useState } from "react";
import { fmt, usePoll } from "./lib";
import { PaperBanner } from "./PaperBanner";
import { OiControlPanel } from "./OiControl";
import { ControlContract, fmtLakhs, fmtLakhsSigned } from "./oiControlMath";

const UNDERLYINGS = ["NIFTY", "SENSEX"];
const EXPIRY_MODES: [string, string][] = [
  ["nearest", "Nearest expiry"],
  ["next", "Next expiry"],
  ["all", "All expiries"],
  ["specific", "Specific expiry"],
];
const STRIKE_WINDOWS = [3, 5, 8, 10, 15];

type ChangePeriod = "5" | "10" | "15" | "1030" | "open";
type SortKey = "strike" | "callOi" | "callChg" | "putOi" | "putChg";

const PERIODS: { id: ChangePeriod; label: string }[] = [
  { id: "5", label: "5 Min" },
  { id: "10", label: "10 Min" },
  { id: "15", label: "15 Min" },
  { id: "1030", label: "Since 10:30" },
  { id: "open", label: "Since Market Open" },
];

type Pair = {
  key: string;
  strike: number;
  expiry: string | null;
  isAtm: boolean;
  call: any | null;
  put: any | null;
  callOi: number | null;
  callChg: number | null;
  putOi: number | null;
  putChg: number | null;
};

export function OiScanner() {
  const [underlying, setUnderlying] = useState("NIFTY");
  const [expiryMode, setExpiryMode] = useState("nearest");
  const [expiry, setExpiry] = useState("");
  const [period, setPeriod] = useState<ChangePeriod>("5");
  const [window, setWindow] = useState(5);
  const [showAll, setShowAll] = useState(false);
  const [openTime, setOpenTime] = useState("09:15");
  const [sort, setSort] = useState<SortKey>("strike");
  const [descending, setDescending] = useState(false);

  const usesReference = period === "1030" || period === "open";
  const interval = period === "10" ? 10 : period === "15" ? 15 : 5;
  const referenceTime = period === "open" ? openTime : period === "1030" ? "10:30" : "10:00";
  const query = new URLSearchParams({
    underlying,
    option_type: "ALL",
    expiry_mode: expiryMode,
    reference_time: referenceTime,
    interval_minutes: String(interval),
    strikes_above: String(window),
    strikes_below: String(window),
    show_all: String(showAll),
    min_oi_change_interval: "0",
    min_oi_change_reference: "0",
    direction: "ALL",
  });
  if (expiryMode === "specific" && expiry) query.set("expiry", expiry);
  const { data, error } = usePoll<any>(`/api/oi-scanner?${query.toString()}`, 5000);
  useEffect(() => {
    const start = data?.context?.session_start;
    if (start && start !== openTime) setOpenTime(start);
  }, [data?.context?.session_start, openTime]);

  const closed = data?.state && data.state !== "LIVE";
  const intervalUnavailable = Boolean(closed && !usesReference);

  const pairs = useMemo(
    () => pairStrikes(data?.rows || [], data?.atm, usesReference, intervalUnavailable),
    [data, usesReference, intervalUnavailable],
  );

  const rows = useMemo(() => {
    const list = [...pairs];
    list.sort((a, b) => comparePair(a, b, sort, descending));
    return list;
  }, [pairs, sort, descending]);

  function sortBy(key: SortKey) {
    if (key === sort) setDescending(!descending);
    else {
      setSort(key);
      setDescending(key !== "strike");
    }
  }

  const spot = data?.context?.spot;
  const periodLabel = PERIODS.find((item) => item.id === period)?.label || period;
  const selectedExpiry = controlExpiry(pairs, data?.expiries_shown);
  const controlCalls = useMemo(
    () => toContracts(pairs, "call", selectedExpiry ? [selectedExpiry] : undefined),
    [pairs, selectedExpiry],
  );
  const controlPuts = useMemo(
    () => toContracts(pairs, "put", selectedExpiry ? [selectedExpiry] : undefined),
    [pairs, selectedExpiry],
  );

  return (
    <>
      <h1>OI Scanner</h1>
      <PaperBanner extra="Open interest is shown in lakhs. This screen does not place orders." />
      <p className="muted">
        Call OI vs Put OI around each strike.
      </p>

      <div className="row tabs">
        {UNDERLYINGS.map((symbol) => (
          <button
            key={symbol}
            className={symbol === underlying ? "tab active" : "tab"}
            onClick={() => setUnderlying(symbol)}
          >
            {symbol}
          </button>
        ))}
        <span className={data?.state === "LIVE" ? "pass" : "warn"}>{data?.state_label || ""}</span>
        <span className="muted">Server {data?.server_time || "—"} IST</span>
      </div>

      {error ? <p className="error">{error}</p> : null}
      {!data ? <p>Loading…</p> : (
        <>
          <section className="card">
            <h2>{data.underlying}</h2>
            <p className="big">LTP {fmtLtp(spot)}</p>
            <p className="muted">
              Futures {fmtLtp(data.context?.futures_ltp)} · ATM {fmt(data.atm)} ·
              Strike interval {fmt(data.strike_interval)} ·
              Expiry {data.expiries_shown?.join(", ") || "—"}
            </p>
            <div className="row">
              <label>
                OI Change Since
                <select value={period} onChange={(event) => setPeriod(event.target.value as ChangePeriod)}>
                  {PERIODS.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
                </select>
              </label>
              <label>
                Strikes
                <select
                  value={showAll ? "all" : String(window)}
                  onChange={(event) => {
                    if (event.target.value === "all") setShowAll(true);
                    else {
                      setShowAll(false);
                      setWindow(Number(event.target.value));
                    }
                  }}
                >
                  {STRIKE_WINDOWS.map((size) => <option key={size} value={size}>±{size}</option>)}
                  <option value="all">All</option>
                </select>
              </label>
              <label>
                Expiry
                <select value={expiryMode} onChange={(event) => setExpiryMode(event.target.value)}>
                  {EXPIRY_MODES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
              </label>
              {expiryMode === "specific" ? (
                <label>
                  Specific expiry
                  <select value={expiry} onChange={(event) => setExpiry(event.target.value)}>
                    <option value="">Select…</option>
                    {(data.expiries || []).map((item: string) => <option key={item}>{item}</option>)}
                  </select>
                </label>
              ) : null}
            </div>
            <p className="muted">Call OI vs Put OI · change shown {periodLabel.toLowerCase()}</p>
          </section>

          {intervalUnavailable ? (
            <p className="warn">Market Closed — Intraday OI change is unavailable.</p>
          ) : data.reference_pending && usesReference ? (
            <p className="warn">The {data.reference_time} reference time has not been reached yet today.</p>
          ) : usesReference && !data.reference_available ? (
            <p className="warn">No {data.reference_time} reference snapshot available for these contracts yet.</p>
          ) : null}

          <h2>Call OI vs Put OI ({rows.length} strikes)</h2>
          <ScanTable
            rows={rows}
            sort={sort}
            descending={descending}
            sortBy={sortBy}
            changeLabel={usesReference ? `Since ${data.reference_time}` : `${interval}M`}
          />
          <p className="muted">{data.note}</p>
          <OiControlPanel
            underlying={data.underlying || underlying}
            expiry={selectedExpiry}
            atm={data.atm ?? null}
            calls={controlCalls}
            puts={controlPuts}
          />
        </>
      )}
    </>
  );
}

function pairStrikes(rows: any[], atm: number | null, usesReference: boolean, hideInterval: boolean): Pair[] {
  const grouped = new Map<string, Pair>();
  for (const row of rows) {
    const key = `${row.strike}|${row.expiry || ""}`;
    let pair = grouped.get(key);
    if (!pair) {
      pair = {
        key,
        strike: row.strike,
        expiry: row.expiry || null,
        isAtm: atm != null && Number(row.strike) === Number(atm),
        call: null,
        put: null,
        callOi: null,
        callChg: null,
        putOi: null,
        putChg: null,
      };
      grouped.set(key, pair);
    }
    const change = hideInterval && !usesReference
      ? null
      : (usesReference ? row.oi_change_reference : row.oi_change_interval);
    if (row.option_type === "CE") {
      pair.call = row;
      pair.callOi = row.oi ?? null;
      pair.callChg = change ?? null;
    } else if (row.option_type === "PE") {
      pair.put = row;
      pair.putOi = row.oi ?? null;
      pair.putChg = change ?? null;
    }
  }
  return [...grouped.values()];
}

function comparePair(a: Pair, b: Pair, sort: SortKey, descending: boolean) {
  const left = sortValue(a, sort);
  const right = sortValue(b, sort);
  if (left === null) return 1;
  if (right === null) return -1;
  const delta = left - right;
  if (delta === 0) return a.strike - b.strike;
  return descending ? -delta : delta;
}

function sortValue(pair: Pair, sort: SortKey): number | null {
  if (sort === "strike") return pair.strike;
  if (sort === "callOi") return pair.callOi;
  if (sort === "callChg") return pair.callChg;
  if (sort === "putOi") return pair.putOi;
  return pair.putChg;
}

function ScanTable({
  rows,
  sort,
  descending,
  sortBy,
  changeLabel,
}: {
  rows: Pair[];
  sort: SortKey;
  descending: boolean;
  sortBy: (key: SortKey) => void;
  changeLabel: string;
}) {
  const headers: { label: string; key: SortKey | null; cls: string }[] = [
    { label: "Call LTP", key: null, cls: "" },
    { label: "Call OI (Lakhs)", key: "callOi", cls: "" },
    { label: "Call OI Change", key: "callChg", cls: "" },
    { label: "Strike", key: "strike", cls: "strike-col" },
    { label: "Put LTP", key: null, cls: "" },
    { label: "Put OI (Lakhs)", key: "putOi", cls: "" },
    { label: "Put OI Change", key: "putChg", cls: "" },
  ];
  if (!rows.length) {
    return <p className="muted">No strikes matched the current window.</p>;
  }
  return (
    <table className="oi-scan">
      <thead>
        <tr>
          {headers.map((column) => (
            <th
              key={column.label}
              className={column.cls || undefined}
              style={column.key ? { cursor: "pointer" } : undefined}
              onClick={column.key ? () => sortBy(column.key!) : undefined}
              title={column.label.includes("Change") ? changeLabel : undefined}
            >
              {column.label}{column.key && column.key === sort ? (descending ? " ▼" : " ▲") : ""}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.key} className={row.isAtm ? "atm-row" : undefined}>
            <td>{fmtLtp(row.call?.ltp)}</td>
            <td>{fmtLakhs(row.callOi)}</td>
            <td className={chgClass(row.callChg)}>{fmtLakhsSigned(row.callChg)}</td>
            <td className="strike-col">
              {fmt(row.strike)}
              {row.isAtm ? <span className="atm-badge"> ATM</span> : null}
              {row.expiry ? <span className="muted"> {row.expiry}</span> : null}
            </td>
            <td>{fmtLtp(row.put?.ltp)}</td>
            <td>{fmtLakhs(row.putOi)}</td>
            <td className={chgClass(row.putChg)}>{fmtLakhsSigned(row.putChg)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function toContracts(pairs: Pair[], side: "call" | "put", shown?: string[]): ControlContract[] {
  const expiry = controlExpiry(pairs, shown);
  return pairs
    .filter((row) => !expiry || row.expiry === expiry)
    .map((row) => ({
      strike: row.strike,
      ltp: side === "call" ? num(row.call?.ltp) : num(row.put?.ltp),
      oi: side === "call" ? row.callOi : row.putOi,
      oiChange: side === "call" ? row.callChg : row.putChg,
    }));
}

function controlExpiry(pairs: Pair[], shown: string[] | undefined): string | null {
  if (shown?.[0]) return shown[0];
  return pairs.find((row) => row.expiry)?.expiry ?? null;
}

function num(value: unknown): number | null {
  if (value == null || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function fmtLtp(value: unknown) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  if (Number.isNaN(number)) return "—";
  return number.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function chgClass(value: number | null) {
  if (value === null || value === undefined) return "muted";
  if (value > 0) return "pass";
  if (value < 0) return "fail";
  return "muted";
}
