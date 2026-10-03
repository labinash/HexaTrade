/** Multi-strike OI control maths. Totals use raw OI; lakhs are display only. */

export const LAKH = 100_000;
export const DEFAULT_THRESHOLD_LAKHS = 5;

export type ControlContract = {
  strike: number;
  ltp: number | null;
  oi: number | null;
  oiChange: number | null;
};

export type Concentration = "CALL" | "PUT" | "BALANCED";
export type CombinedControl = Concentration | "MIXED";

export type OiControlTotals = {
  callOi: number;
  putOi: number;
  oiDifference: number;
  oiDifferencePercent: number | null;
  callChange: number;
  putChange: number;
  changeDifference: number;
  structure: Concentration;
  flow: Concentration;
  combined: CombinedControl;
  structureLabel: string;
  flowLabel: string;
  controlLabel: string;
  contextLabel: string;
};

export function sumRaw(values: Array<number | null | undefined>): number {
  let total = 0;
  for (const value of values) {
    if (value != null && Number.isFinite(value)) total += value;
  }
  return total;
}

export function differencePercent(callOi: number, putOi: number): number | null {
  const denom = callOi + putOi;
  if (denom === 0) return null;
  return ((callOi - putOi) / denom) * 100;
}

export function concentrationOf(difference: number, thresholdRaw: number): Concentration {
  if (Math.abs(difference) < thresholdRaw) return "BALANCED";
  return difference > 0 ? "CALL" : "PUT";
}

export function combinedOf(structure: Concentration, flow: Concentration): CombinedControl {
  if (structure === "BALANCED" && flow === "BALANCED") return "BALANCED";
  if (structure === "BALANCED") return flow;
  if (flow === "BALANCED") return structure;
  if (structure !== flow) return "MIXED";
  return structure;
}

export function aroundAtm(strikes: number[], atm: number | null, eachSide: number): number[] {
  const ordered = [...new Set(strikes)].sort((a, b) => a - b);
  if (!ordered.length || atm == null || eachSide < 0) return [];
  let index = ordered.findIndex((strike) => strike === atm);
  if (index < 0) {
    index = ordered.reduce(
      (best, strike, i) => (Math.abs(strike - atm) < Math.abs(ordered[best] - atm) ? i : best),
      0,
    );
  }
  const start = Math.max(0, index - eachSide);
  const end = Math.min(ordered.length, index + eachSide + 1);
  return ordered.slice(start, end);
}

export function pruneSelection(selected: number[], available: number[]): number[] {
  const keep = new Set(available);
  return selected.filter((strike) => keep.has(strike));
}

export function summariseControl(
  calls: ControlContract[],
  puts: ControlContract[],
  thresholdLakhs: number,
): OiControlTotals {
  const callOi = sumRaw(calls.map((row) => row.oi));
  const putOi = sumRaw(puts.map((row) => row.oi));
  const callChange = sumRaw(calls.map((row) => row.oiChange));
  const putChange = sumRaw(puts.map((row) => row.oiChange));
  const oiDifference = callOi - putOi;
  const changeDifference = callChange - putChange;
  const thresholdRaw = Math.max(0, thresholdLakhs) * LAKH;
  const structure = concentrationOf(oiDifference, thresholdRaw);
  const flow = concentrationOf(changeDifference, thresholdRaw);
  const combined = combinedOf(structure, flow);
  return {
    callOi,
    putOi,
    oiDifference,
    oiDifferencePercent: differencePercent(callOi, putOi),
    callChange,
    putChange,
    changeDifference,
    structure,
    flow,
    combined,
    structureLabel: structureText(structure),
    flowLabel: flowText(flow),
    controlLabel: controlText(combined),
    contextLabel: contextText(combined),
  };
}

export function fmtLakhs(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const lakhs = value / LAKH;
  return `${lakhs.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} L`;
}

export function fmtLakhsSigned(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const lakhs = value / LAKH;
  const body = Math.abs(lakhs).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (lakhs > 0) return `+${body} L`;
  if (lakhs < 0) return `-${body} L`;
  return `${body} L`;
}

export function fmtPercent(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const body = Math.abs(value).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (value > 0) return `+${body}%`;
  if (value < 0) return `-${body}%`;
  return `${body}%`;
}

function structureText(side: Concentration): string {
  if (side === "CALL") return "Call-side OI concentration";
  if (side === "PUT") return "Put-side OI concentration";
  return "Balanced / no clear OI concentration";
}

function flowText(side: Concentration): string {
  if (side === "CALL") return "Call-side increase";
  if (side === "PUT") return "Put-side increase";
  return "No clear OI flow";
}

function controlText(side: CombinedControl): string {
  if (side === "CALL") return "CALL SIDE";
  if (side === "PUT") return "PUT SIDE";
  if (side === "MIXED") return "MIXED";
  return "BALANCED";
}

function contextText(side: CombinedControl): string {
  if (side === "CALL") return "Call-side concentration — OI positioning only, not a guaranteed direction";
  if (side === "PUT") return "Put-side concentration — OI positioning only, not a guaranteed direction";
  if (side === "MIXED") return "Mixed OI structure/flow — not a directional prediction";
  return "No clear OI concentration — not a directional prediction";
}
