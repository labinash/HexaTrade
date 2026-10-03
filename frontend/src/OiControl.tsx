import { useEffect, useMemo, useState } from "react";
import { fmt } from "./lib";
import {
  ControlContract,
  DEFAULT_THRESHOLD_LAKHS,
  aroundAtm,
  fmtLakhs,
  fmtLakhsSigned,
  fmtPercent,
  pruneSelection,
  summariseControl,
} from "./oiControlMath";

const QUICK = [2, 3, 5];

export function OiControlPanel({
  underlying,
  expiry,
  atm,
  calls,
  puts,
}: {
  underlying: string;
  expiry: string | null;
  atm: number | null;
  calls: ControlContract[];
  puts: ControlContract[];
}) {
  const [selectedCall, setSelectedCall] = useState<number[]>([]);
  const [selectedPut, setSelectedPut] = useState<number[]>([]);
  const [thresholdLakhs, setThresholdLakhs] = useState(DEFAULT_THRESHOLD_LAKHS);

  const callStrikes = useMemo(() => uniqueStrikes(calls), [calls]);
  const putStrikes = useMemo(() => uniqueStrikes(puts), [puts]);
  const callKey = callStrikes.join(",");
  const putKey = putStrikes.join(",");

  useEffect(() => {
    setSelectedCall([]);
    setSelectedPut([]);
  }, [underlying, expiry]);

  useEffect(() => {
    const available = callKey ? callKey.split(",").map(Number) : [];
    setSelectedCall((prev) => keepIfSame(prev, pruneSelection(prev, available)));
  }, [callKey]);

  useEffect(() => {
    const available = putKey ? putKey.split(",").map(Number) : [];
    setSelectedPut((prev) => keepIfSame(prev, pruneSelection(prev, available)));
  }, [putKey]);

  const callRows = calls.filter((row) => selectedCall.includes(row.strike));
  const putRows = puts.filter((row) => selectedPut.includes(row.strike));
  const totals = summariseControl(callRows, putRows, thresholdLakhs);

  return (
    <section className="card oi-control">
      <h2>Multi-Strike OI Control</h2>
      <p className="muted">
        Select CE and PE strikes independently from the current {underlying} chain
        {expiry ? ` (${expiry})` : ""}. Totals use raw open interest and are shown in lakhs.
        This is an OI concentration reading, not a guaranteed market direction.
      </p>

      <StrikeSelector
        title="CALL CE STRIKES"
        contracts={calls}
        strikes={callStrikes}
        selected={selectedCall}
        atm={atm}
        totalOi={totals.callOi}
        onChange={setSelectedCall}
      />
      <StrikeSelector
        title="PUT PE STRIKES"
        contracts={puts}
        strikes={putStrikes}
        selected={selectedPut}
        atm={atm}
        totalOi={totals.putOi}
        onChange={setSelectedPut}
      />

      <label className="oi-threshold">
        Minimum OI Difference
        <span className="row">
          <input
            type="number"
            min={0}
            step={0.5}
            value={thresholdLakhs}
            onChange={(event) => setThresholdLakhs(Number(event.target.value) || 0)}
          />
          <span className="muted">L</span>
        </span>
      </label>
      <p className="muted">Differences smaller than this threshold are treated as balanced.</p>

      <OiTotalComparison totals={totals} />
    </section>
  );
}

function StrikeSelector({
  title,
  contracts,
  strikes,
  selected,
  atm,
  totalOi,
  onChange,
}: {
  title: string;
  contracts: ControlContract[];
  strikes: number[];
  selected: number[];
  atm: number | null;
  totalOi: number;
  onChange: (next: number[]) => void;
}) {
  const oiByStrike = useMemo(() => {
    const map = new Map<number, number | null>();
    for (const row of contracts) map.set(row.strike, row.oi);
    return map;
  }, [contracts]);
  function toggle(strike: number) {
    onChange(selected.includes(strike) ? selected.filter((item) => item !== strike) : [...selected, strike].sort((a, b) => a - b));
  }
  return (
    <div className="oi-selector">
      <div className="oi-selector-head">
        <h3>{title}{selected.length ? ` · ${selected.length} selected` : ""}</h3>
      </div>
      <div className="row">
        <button type="button" onClick={() => onChange([...strikes])}>Select All</button>
        <button type="button" onClick={() => onChange([])}>Clear</button>
        {QUICK.map((n) => (
          <button key={n} type="button" onClick={() => onChange(aroundAtm(strikes, atm, n))}>
            ATM ±{n}
          </button>
        ))}
      </div>
      <p className="oi-total-mini muted">Total OI {fmtLakhs(totalOi)}</p>
      <div className="oi-chips">
        {strikes.map((strike) => (
          <button
            key={strike}
            type="button"
            className={selected.includes(strike) ? "tab active" : "tab"}
            onClick={() => toggle(strike)}
          >
            {fmt(strike)}
            <span className="oi-chip-oi">{fmtChipOi(oiByStrike.get(strike))}</span>
            {atm != null && strike === atm ? " ATM" : ""}
          </button>
        ))}
        {!strikes.length ? <span className="muted">No strikes in the current chain window.</span> : null}
      </div>
    </div>
  );
}

function fmtChipOi(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "[—]";
  return `[${fmtLakhs(value)}]`;
}

function OiTotalComparison({ totals }: { totals: ReturnType<typeof summariseControl> }) {
  return (
    <div className="oi-compare">
      <h3>OI Control Indication</h3>
      <p className="muted">Derived from selected-strike open interest only. Not a guaranteed bull or bear call.</p>
      <dl>
        <div><dt>CALL OI</dt><dd>{fmtLakhs(totals.callOi)}</dd></div>
        <div><dt>PUT OI</dt><dd>{fmtLakhs(totals.putOi)}</dd></div>
        <div><dt>OI DIFFERENCE</dt><dd className={chgClass(totals.oiDifference)}>{fmtLakhsSigned(totals.oiDifference)}</dd></div>
        <div><dt>OI DIFFERENCE %</dt><dd className={chgClass(totals.oiDifferencePercent)}>{fmtPercent(totals.oiDifferencePercent)}</dd></div>
      </dl>
      <p>
        <span className="muted">OI Structure</span>
        <br />
        <strong>{totals.structureLabel}</strong>
      </p>
      <p className="muted">
        OI Balance: {totals.structure === "CALL" ? "CALL OI > PUT OI" : totals.structure === "PUT" ? "PUT OI > CALL OI" : "No clear imbalance"}
      </p>
      <dl>
        <div><dt>CALL OI CHANGE</dt><dd className={chgClass(totals.callChange)}>{fmtLakhsSigned(totals.callChange)}</dd></div>
        <div><dt>PUT OI CHANGE</dt><dd className={chgClass(totals.putChange)}>{fmtLakhsSigned(totals.putChange)}</dd></div>
        <div><dt>CHANGE DIFFERENCE</dt><dd className={chgClass(totals.changeDifference)}>{fmtLakhsSigned(totals.changeDifference)}</dd></div>
      </dl>
      <p>
        <span className="muted">OI Flow</span>
        <br />
        <strong>{totals.flowLabel}</strong>
      </p>
      <p>
        <span className="muted">OI Control</span>
        <br />
        <strong>{totals.controlLabel}</strong>
      </p>
      {totals.combined === "MIXED" ? (
        <p className="warn">Combined OI Context: Mixed — structure and flow do not agree, so no single side is forced.</p>
      ) : null}
      <p className="muted">Bull/Bear Context: {totals.contextLabel}</p>
    </div>
  );
}

function uniqueStrikes(rows: ControlContract[]): number[] {
  return [...new Set(rows.map((row) => row.strike))].sort((a, b) => a - b);
}

function keepIfSame(prev: number[], next: number[]): number[] {
  if (prev.length === next.length && prev.every((strike, index) => strike === next[index])) return prev;
  return next;
}

function chgClass(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value) || value === 0) return "muted";
  return value > 0 ? "pass" : "fail";
}
