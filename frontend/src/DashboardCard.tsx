import { fmt } from "./lib";

function pct(value: unknown) {
  if (value === null || value === undefined || value === "") return "—";
  return `${Number(value).toFixed(1)}%`;
}

function mark(ok: boolean | undefined) {
  return ok ? "✓" : "✕";
}

function statusLabel(status: string | undefined) {
  if (status === "READY") return "MARKET READY";
  if (status === "WAITING") return "WAITING";
  return "NOT READY";
}

function directionGlyph(direction: string | undefined) {
  if (direction === "BEARISH") return "▼";
  if (direction === "NEUTRAL" || direction === "NOT READY") return "●";
  return "▲";
}

function decisionClass(label: string | undefined) {
  if (label === "BUY") return "pass";
  if (label === "DATA NOT READY") return "data-unavailable";
  if (String(label || "").includes("WATCH")) return "warn";
  return "muted";
}

export function DashboardCard({
  market,
}: {
  market: any;
  kind?: "CE" | "PE";
  onSide?: (side: "CE" | "PE") => void;
}) {
  const ce = market.ce_strategy;
  const pe = market.pe_strategy;
  const snapshot = market.market_snapshot || {};
  const readiness = market.trade_readiness || {};
  const pack = market.candidates || {};
  const present = market.presentation || {};
  const trend = present.trend || market.displayed_direction || market.direction || "NEUTRAL";
  const confidence = present.trend_confidence || {};
  const decision = present.decision || {};
  const preferred = present.preferred_contract;
  const preferredSide = present.preferred_side;
  const indicators = present.indicators || {};
  const items = indicators.items || [];
  const green = indicators.green ?? 0;
  const total = indicators.total ?? 0;

  return (
    <section className="card dash-card">
      <h2>{market.symbol}</h2>

      <div className="dash-summary">
        <p className={`market-status status-${String(market.market_status || "NOT READY").replace(/\s+/g, "-")}`}>
          <strong>● {statusLabel(market.market_status)}</strong>
        </p>
        <p className="muted" title="Usable session data only. Never a BUY recommendation.">
          Market Ready means session data is usable. It is not a BUY.
        </p>

        <h3>Market Trend</h3>
        <p className={trend === "BEARISH" ? "bearish" : trend === "BULLISH" ? "bullish" : "muted"}>
          <strong>{directionGlyph(trend)} {trend}</strong>
        </p>
        <p>
          Trend confidence{" "}
          {confidence.percent == null ? "—" : `${confidence.percent}%`}
          {confidence.total ? ` (${confidence.passed}/${confidence.total} tape votes)` : ""}
        </p>
        <p>
          Preferred side{" "}
          <strong>{preferredSide || "NONE"}</strong>
        </p>
      </div>

      <PreferredContract
        trend={trend}
        label={present.preferred_label}
        contract={preferred}
        gaps={present.contract_gaps || []}
        notes={decision.reason}
      />

      <div className="dash-counts">
        <p><strong>{indicators.text || `Green indicators: ${green} / ${total}`}</strong></p>
        <p>{indicators.percent_text || (indicators.percent == null ? "Green percentage: —" : `Green percentage: ${Number(indicators.percent).toFixed(1)}%`)}</p>
        <p>CE score {pct(readiness.ce_score_percent ?? ce?.score_percent)} · PE score {pct(readiness.pe_score_percent ?? pe?.score_percent)}</p>
        <p className={decisionClass(decision.label)}>
          Trade readiness <strong>{decision.label || "NO TRADE"}</strong>
        </p>
        <p className="muted">{decision.reason || readiness.message}</p>
      </div>

      <IndicatorSummary items={items} />

      <StrategyBlock
        title="CE Strategy"
        result={ce}
        enabled={market.ce_strategy_enabled !== false}
        preferred={preferredSide === "CE"}
        buy={Boolean(decision.buy && preferredSide === "CE")}
      />
      <StrategyBlock
        title="PE Strategy"
        result={pe}
        enabled={market.pe_strategy_enabled !== false}
        preferred={preferredSide === "PE"}
        buy={Boolean(decision.buy && preferredSide === "PE")}
      />

      <div className="readiness">
        <h3>Trade Readiness</h3>
        <p className={decisionClass(decision.label)}><strong>{decision.label || "NO TRADE"}</strong></p>
        <p>
          <span className={readiness.ce_passed ? "pass" : "fail"}>CE {mark(readiness.ce_passed)} {readiness.ce_passed_count ?? ce?.passed_count}/{readiness.ce_total ?? ce?.total_count}</span>
          {"  "}
          <span className={readiness.pe_passed ? "pass" : "fail"}>PE {mark(readiness.pe_passed)} {readiness.pe_passed_count ?? pe?.passed_count}/{readiness.pe_total ?? pe?.total_count}</span>
        </p>
        <p className="muted">{decision.reason || readiness.message}</p>
      </div>

      <details className="why-block">
        <summary>Detailed analysis</summary>
        <div className="snapshot">
          <h3>Market Snapshot</h3>
          <dl>
            <div><dt>LTP</dt><dd>{fmt(snapshot.ltp ?? market.ltp)}</dd></div>
            <div><dt>Futures</dt><dd>{fmt(snapshot.futures ?? market.futures_ltp)}</dd></div>
            <div><dt>VWAP</dt><dd>{fmt(snapshot.vwap ?? market.vwap)}</dd></div>
            <div><dt>EMA20</dt><dd>{fmt(snapshot.ema20 ?? market.ema)}</dd></div>
            <div><dt>RSI</dt><dd>{fmt(snapshot.rsi ?? market.rsi)}</dd></div>
            <div><dt>ATR</dt><dd>{fmt(snapshot.atr ?? market.atr)}</dd></div>
          </dl>
        </div>
        <p className="muted">Price source {market.price_source}. OI source {market.oi_source}. An OI increase is not proof of long buildup.</p>
        <p className="muted">{market.direction_note}</p>
        <p className="paper">{present.note || "HYPOTHETICAL / PAPER only. No broker orders."}</p>
        <details>
          <summary>Direction basis</summary>
          <ConditionList rows={market.direction_basis || []} />
        </details>
        <details>
          <summary>Candidate scores</summary>
          <p className="muted">
            Threshold {pack.threshold ?? market.entry_threshold ?? "—"}%. These scores are not a BUY by themselves.
          </p>
          <p>CE {pack.ce ? `${pack.ce.passed_count}/${pack.ce.total_count} · ${pct(pack.ce.score_percent)}` : "—"}</p>
          <p>PE {pack.pe ? `${pack.pe.passed_count}/${pack.pe.total_count} · ${pct(pack.pe.score_percent)}` : "—"}</p>
          {pack.reason ? <p className="muted">{pack.reason}</p> : null}
        </details>
      </details>
    </section>
  );
}

function PreferredContract({
  trend,
  label,
  contract,
  gaps,
  notes,
}: {
  trend: string;
  label?: string;
  contract: any;
  gaps: string[];
  notes?: string;
}) {
  const confirmed = trend === "BULLISH" || trend === "BEARISH";
  return (
    <div className={confirmed && contract ? "preferred-card" : "preferred-card none"}>
      <h3>Preferred Contract</h3>
      <p><strong>{label || "NO PREFERRED CONTRACT — MARKET NOT CONFIRMED"}</strong></p>
      {confirmed && contract ? (
        <>
          <p className="big">{contract.tradingsymbol || "—"}</p>
          <p>
            {contract.option_type} {fmt(contract.strike)} · Expiry {contract.expiry || "—"} · LTP {fmt(contract.ltp)}
          </p>
          <p>
            Bid {fmt(contract.bid)} / Ask {fmt(contract.ask)}
            {contract.spread_percent == null ? "" : ` · spread ${Number(contract.spread_percent).toFixed(2)}%`}
          </p>
          <p>
            Liquidity OI {fmt(contract.oi)} · Vol {fmt(contract.volume)} · Lot {fmt(contract.lot_size)}
          </p>
        </>
      ) : (
        <p className="muted">{notes || "Do not highlight CE or PE as a BUY until the tape confirms a direction."}</p>
      )}
      {gaps.length && confirmed ? <p className="warn">Missing: {gaps.join(", ")}</p> : null}
    </div>
  );
}

function IndicatorSummary({ items }: { items: any[] }) {
  return (
    <div className="indicator-summary">
      <h3>Indicator Summary</h3>
      {!items.length ? <p className="muted">Indicator rows are not available yet.</p> : null}
      <ul className="indicator-list">
        {items.map((row) => (
          <li key={row.name} className={row.state === "PASS" ? "ind-pass" : row.state === "FAIL" ? "ind-fail" : "ind-na"} title={row.reason || row.required || ""}>
            <span className="ind-mark">{row.mark || (row.state === "PASS" ? "✓" : row.state === "FAIL" ? "✕" : "–")}</span>
            <span>{row.name}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function StrategyBlock({
  title,
  result,
  enabled,
  preferred,
  buy,
}: {
  title: string;
  result: any;
  enabled: boolean;
  preferred: boolean;
  buy: boolean;
}) {
  if (!result) return <p className="muted">{title}: no result yet.</p>;
  let status = "NO TRADE";
  if (!enabled) status = "OFF";
  else if (buy) status = "BUY";
  else if (preferred) status = `${result.option_type || ""} WATCH`.trim();
  else status = `${result.passed_count}/${result.total_count}`;
  return (
    <div className={`strategy-block${preferred ? " preferred-side" : ""}`}>
      <h3>{title}</h3>
      <p>
        <span className={enabled ? "pass" : "fail"}>● {enabled ? "ON" : "OFF"}</span>
        <span className="muted"> · {result.passed_count}/{result.total_count} · {pct(result.score_percent)}</span>
      </p>
      <p className={buy ? "pass" : preferred ? "warn" : "muted"}><strong>{status}</strong></p>
      <p className="muted">{result.option_symbol || "—"} · Strike {fmt(result.strike)} · LTP {fmt(result.premium_ltp)}</p>
      <details>
        <summary>Conditions</summary>
        {result.data_quality ? <p className="warn">{result.data_quality}</p> : null}
        <ConditionList rows={result.conditions || []} />
      </details>
    </div>
  );
}

function ConditionList({ rows }: { rows: any[] }) {
  if (!rows.length) return <p className="muted">No condition detail returned.</p>;
  return (
    <ul className="cond-list">
      {rows.map((row) => {
        const status = String(row.status || "");
        const na = status === "OFF" || status === "DATA UNAVAILABLE" || status === "UNAVAILABLE";
        const ok = row.passed === true || status === "PASS";
        return (
          <li key={row.name}>
            <details>
              <summary className={na ? "data-unavailable" : ok ? "pass" : "fail"}>
                {na ? "–" : mark(ok)} {row.name}
              </summary>
              <p>Current: {row.current ?? "—"}</p>
              <p>Required: {row.required ?? "—"}</p>
              <p>Result: {na ? "UNAVAILABLE" : ok ? "PASSED" : "FAILED"}</p>
              {!ok && row.failure_reason ? <p className="muted">{row.failure_reason}</p> : null}
            </details>
          </li>
        );
      })}
    </ul>
  );
}
