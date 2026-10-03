const ACRONYMS: Record<string, string> = {
  ema: "EMA",
  rsi: "RSI",
  atr: "ATR",
  vwap: "VWAP",
  oi: "OI",
  iv: "IV",
  pnl: "P&L",
  mis: "MIS",
  bps: "bps",
  rr: "R:R",
};

export const FIELD_CHOICES: Record<string, string[]> = {
  direction: ["BULLISH", "BEARISH"],
  momentum_unit: ["points", "percent"],
  oi_unit: ["contracts", "lots"],
  price_source: ["front_futures", "spot"],
  oi_source: ["front_futures", "selected_option"],
  evaluate_on: ["candle_close", "tick"],
  confirmation_mode: ["all", "min_n"],
  bullish_option_type: ["CE", "PE"],
  bearish_option_type: ["PE", "CE"],
  delta_missing_action: ["allow", "warn", "reject"],
  iv_high_action: ["allow", "warn", "reject"],
  stop_method: ["option_percent", "underlying_atr", "underlying_level"],
  candidate_strike_range: ["2", "3", "5", "8", "10"],
  timeframe_minutes: ["1", "3", "5", "10", "15"],
};

const TIME_FIELDS = new Set([
  "momentum_time",
  "oi_time",
  "pe_midday_time",
  "time_start",
  "time_end",
  "time_exit",
  "entry_start",
  "entry_stop",
  "forced_square_off",
  "mis_cutoff",
]);

export function labelFor(name: string) {
  const words = name.split("_").map((word) => ACRONYMS[word] ?? word);
  const first = words[0];
  const head = ACRONYMS[name.split("_")[0]] ? first : first.charAt(0).toUpperCase() + first.slice(1);
  return [head, ...words.slice(1)].join(" ");
}

export function FieldInput({ name, value, onChange }: { name: string; value: any; onChange: (value: any) => void }) {
  if (typeof value === "boolean") {
    return <input type="checkbox" checked={value} onChange={(event) => onChange(event.target.checked)} />;
  }
  const choices = FIELD_CHOICES[name];
  if (choices) {
    const numeric = choices.every((choice) => choice !== "" && !Number.isNaN(Number(choice)));
    return (
      <select
        value={String(value ?? choices[0])}
        onChange={(event) => onChange(numeric ? Number(event.target.value) : event.target.value)}
      >
        {choices.map((choice) => <option key={choice}>{choice}</option>)}
      </select>
    );
  }
  if (TIME_FIELDS.has(name)) {
    return <input type="time" value={String(value ?? "")} onChange={(event) => onChange(event.target.value)} />;
  }
  if (typeof value === "number") {
    return <input value={value} onChange={(event) => onChange(event.target.value === "" ? null : Number(event.target.value))} />;
  }
  return <input value={value ?? ""} onChange={(event) => onChange(event.target.value)} />;
}

export function FieldGroup({
  title,
  note,
  keys,
  values,
  onChange,
  children,
}: {
  title: string;
  note?: string;
  keys: string[];
  values: Record<string, any>;
  onChange: (key: string, value: any) => void;
  children?: React.ReactNode;
}) {
  const present = keys.filter((key) => key in values);
  if (!present.length) return null;
  return (
    <section className="card">
      <h2>{title}</h2>
      {note ? <p className="muted">{note}</p> : null}
      <div className="form">
        {present.map((key) => (
          <label key={key}>
            {labelFor(key)}
            <FieldInput name={key} value={values[key]} onChange={(next) => onChange(key, next)} />
          </label>
        ))}
      </div>
      {children}
    </section>
  );
}

export function DateField({
  value,
  min,
  max,
  onChange,
}: {
  value: string;
  min?: string;
  max?: string;
  onChange: (value: string) => void;
}) {
  return (
    <input
      type="date"
      className="date-field"
      value={value}
      min={min || undefined}
      max={max || undefined}
      onChange={(event) => {
        const next = event.target.value;
        if (next === "" || /^\d{4}-\d{2}-\d{2}$/.test(next)) onChange(next);
      }}
      onClick={(event) => {
        const el = event.currentTarget as HTMLInputElement & { showPicker?: () => void };
        try {
          el.showPicker?.();
        } catch {
          /* picker already open */
        }
      }}
    />
  );
}
