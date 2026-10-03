import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { fmt, usePoll } from "./lib";

const MAX_VISIBLE = 5;
const LIFE_MS = 8000;
const EXIT_MS = 220;

export type PaperBuyNotice = {
  id: string;
  tradeId: number;
  underlying: string;
  optionType: string;
  strike: number;
  quantity: number;
  entryPrice: number;
  tradingsymbol?: string;
  timestamp?: string;
  kind?: "fill" | "signal" | "live";
  score?: number;
  source?: string;
  status?: string;
  orderId?: string;
  badge?: string;
};

type VisibleNotice = PaperBuyNotice & { leaving?: boolean };

type TickerState = { data: any; error: string };

const TickerContext = createContext<TickerState>({ data: null, error: "" });
const SignalPushContext = createContext<(row: PaperBuyNotice) => void>(() => {});

export function useTicker() {
  return useContext(TickerContext);
}

export function useTradeNoticePush() {
  return useContext(SignalPushContext);
}

export function PaperTradeNotificationProvider({ children }: { children: ReactNode }) {
  const ticker = usePoll<any>("/api/ticker", 2000);
  const [visible, setVisible] = useState<VisibleNotice[]>([]);
  const primed = useRef(false);
  const seen = useRef(new Set<string>());
  const queue = useRef<PaperBuyNotice[]>([]);
  const lifeTimers = useRef(new Map<string, number>());
  const exitTimers = useRef(new Map<string, number>());

  function enqueue(row: PaperBuyNotice) {
    if (!row.id || seen.current.has(row.id)) return;
    seen.current.add(row.id);
    browserNotify(row);
    setVisible((current) => {
      const active = current.filter((item) => !item.leaving);
      if (active.length >= MAX_VISIBLE) {
        queue.current.push(row);
        return current;
      }
      return [...current, row];
    });
  }

  useEffect(() => {
    return () => {
      lifeTimers.current.forEach((id) => window.clearTimeout(id));
      exitTimers.current.forEach((id) => window.clearTimeout(id));
      lifeTimers.current.clear();
      exitTimers.current.clear();
    };
  }, []);

  useEffect(() => {
    const fills = normalizeBuys(ticker.data?.paper_buys);
    const signals = normalizeIndexSignals(ticker.data?.index_signals);
    if (ticker.data == null) return;
    if (!primed.current) {
      fills.forEach((row) => seen.current.add(row.id));
      signals.forEach((row) => seen.current.add(row.id));
      primed.current = true;
      return;
    }
    for (const row of [...signals, ...fills]) enqueue(row);
  }, [ticker.data]);

  useEffect(() => {
    for (const item of visible) {
      if (item.leaving) continue;
      if (!lifeTimers.current.has(item.id)) {
        lifeTimers.current.set(
          item.id,
          window.setTimeout(() => beginExit(item.id), LIFE_MS),
        );
      }
    }
  }, [visible]);

  function beginExit(id: string) {
    const life = lifeTimers.current.get(id);
    if (life) window.clearTimeout(life);
    lifeTimers.current.delete(id);
    setVisible((current) => current.map((item) => (item.id === id ? { ...item, leaving: true } : item)));
    const prev = exitTimers.current.get(id);
    if (prev) window.clearTimeout(prev);
    exitTimers.current.set(
      id,
      window.setTimeout(() => finishExit(id), EXIT_MS),
    );
  }

  function finishExit(id: string) {
    const exit = exitTimers.current.get(id);
    if (exit) window.clearTimeout(exit);
    exitTimers.current.delete(id);
    setVisible((current) => {
      const next = current.filter((item) => item.id !== id);
      const waiting = queue.current.shift();
      if (!waiting) return next;
      return [...next, waiting];
    });
  }

  return (
    <TickerContext.Provider value={ticker}>
      <SignalPushContext.Provider value={enqueue}>
        {children}
        <PaperTradeToastStack notices={visible} />
        <AlertNoticeLayer ticker={ticker} />
      </SignalPushContext.Provider>
    </TickerContext.Provider>
  );
}

export function useChartSignalNotices(signals: any[], enabled: boolean, resetKey = "") {
  const push = useContext(SignalPushContext);
  const primed = useRef(false);
  const seen = useRef(new Set<string>());

  useEffect(() => {
    primed.current = false;
    seen.current.clear();
  }, [resetKey, enabled]);

  useEffect(() => {
    if (!enabled) return;
    const incoming = indexEntryNotices(signals);
    if (!primed.current) {
      incoming.forEach((row) => seen.current.add(row.id));
      primed.current = true;
      return;
    }
    for (const row of incoming) {
      if (seen.current.has(row.id)) continue;
      seen.current.add(row.id);
      push(row);
    }
  }, [enabled, resetKey, signals, push]);
}

function PaperTradeToastStack({ notices }: { notices: VisibleNotice[] }) {
  if (!notices.length) return null;
  return (
    <div className="paper-toast-stack" aria-live="polite" aria-relevant="additions text" aria-atomic="false">
      {notices.map((notice) => (
        <PaperTradeToast key={notice.id} notice={notice} />
      ))}
    </div>
  );
}

export function PaperTradeToast({ notice }: { notice: VisibleNotice }) {
  const isSell = notice.optionType === "SELL";
  const isSignal = notice.kind === "signal";
  const isLive = notice.kind === "live" || notice.badge === "LIVE ZERODHA";
  const contract = notice.tradingsymbol || `${notice.underlying} ${notice.strike ? `${fmt(notice.strike)} ` : ""}${notice.optionType}`;
  const title = isLive
    ? (notice.status || "ORDER SUBMITTED")
    : isSignal ? `PAPER ${notice.optionType} SIGNAL` : `PAPER ${notice.optionType === "SELL" ? "SELL" : "BUY"}`;
  const label = `${title} ${contract}${notice.entryPrice ? `, LTP ${fmt(notice.entryPrice)}` : ""}`;
  return (
    <div className={`paper-toast${notice.leaving ? " leaving" : ""}${isSell ? " sell" : ""}${isLive ? " live" : ""}`} role="status" aria-label={label}>
      <p className="paper-toast-title"><span aria-hidden="true">{isLive ? "●" : isSell ? "🔴" : "🟢"}</span> {title}</p>
      <p className="paper-toast-contract">{contract}</p>
      <p className="paper-toast-meta">
        {notice.quantity ? <span>Qty: {notice.quantity}</span> : <span>{isSignal ? "HYPOTHETICAL / PAPER" : isLive ? "LIVE ZERODHA" : "PAPER"}</span>}
        {notice.entryPrice ? <span>LTP: ₹{fmt(notice.entryPrice)}</span> : notice.score != null ? <span>Score {fmt(notice.score)}</span> : null}
      </p>
      {notice.orderId ? <p className="paper-toast-meta"><span>Order ID: {notice.orderId}</span></p> : null}
      {notice.badge ? <p className="muted">{notice.badge}</p> : null}
    </div>
  );
}

function AlertNoticeLayer({ ticker }: { ticker: TickerState }) {
  const primed = useRef(false);
  const seen = useRef(new Set<number>());
  const [visible, setVisible] = useState<any[]>([]);

  useEffect(() => {
    const config = ticker.data?.alert_config || {};
    const incoming = Array.isArray(ticker.data?.alert_notices) ? ticker.data.alert_notices : [];
    if (ticker.data == null) return;
    if (!primed.current) {
      incoming.forEach((row: any) => {
        const id = Number(row?.id);
        if (Number.isFinite(id)) seen.current.add(id);
      });
      primed.current = true;
      return;
    }
    if (config.notifications_enabled === false) return;
    for (const row of incoming) {
      const id = Number(row?.id);
      if (!Number.isFinite(id) || seen.current.has(id)) continue;
      seen.current.add(id);
      setVisible((current) => [...current.slice(-4), row]);
      if (config.sound_enabled) beep();
      if (config.browser_notify && typeof Notification !== "undefined" && Notification.permission === "granted") {
        const title = `${row.instrument} ${row.option_type} BUY`;
        const body = `${row.tradingsymbol || ""} strike ${row.strike} · conf ${row.confidence} · alert-only, not a prediction`;
        try {
          new Notification(title, { body, tag: `hextrade-alert-${id}` });
        } catch {
          /* notifications unavailable */
        }
      }
      window.setTimeout(() => {
        setVisible((current) => current.filter((item) => item.id !== id));
      }, 8000);
    }
  }, [ticker.data]);

  if (!visible.length) return null;
  return (
    <div className="alert-toast-stack" aria-live="polite">
      {visible.map((row) => (
        <div key={row.id} className="alert-toast" role="status">
          <p className="alert-toast-title">ALERT ONLY · {row.instrument} {row.option_type} BUY</p>
          <p>{row.tradingsymbol || `${row.instrument} ${row.strike} ${row.option_type}`}</p>
          <p className="muted">Conf {fmt(row.confidence)} · entry {fmt(row.entry_price)} · SL {fmt(row.stop_loss)}</p>
          {row.warning ? <p className="warn">{row.warning}</p> : null}
        </div>
      ))}
    </div>
  );
}

function beep() {
  try {
    const ctx = new AudioContext();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = 880;
    gain.gain.value = 0.04;
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.18);
    window.setTimeout(() => ctx.close(), 300);
  } catch {
    /* autoplay or AudioContext blocked */
  }
}

function browserNotify(notice: PaperBuyNotice) {
  try {
    if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
    const side = notice.optionType || "BUY";
    const title = notice.kind === "signal" ? `HexTrade paper ${side} signal` : `HexTrade paper ${side}`;
    const body = [
      notice.tradingsymbol || `${notice.underlying} ${side}`,
      notice.entryPrice ? `LTP ₹${fmt(notice.entryPrice)}` : "",
      notice.score != null ? `score ${fmt(notice.score)}` : "",
      "HYPOTHETICAL / PAPER. No broker order.",
    ].filter(Boolean).join(" · ");
    new Notification(title, { body, tag: `hextrade-${notice.id}` });
  } catch {
    /* notifications unavailable */
  }
}

function normalizeBuys(rows: unknown): PaperBuyNotice[] {
  if (!Array.isArray(rows)) return [];
  const out: PaperBuyNotice[] = [];
  for (const row of rows) {
    const tradeId = Number(row?.trade_id ?? row?.id);
    if (!Number.isFinite(tradeId) || tradeId <= 0) continue;
    const optionType = String(row?.option_type || "").toUpperCase();
    if (optionType !== "CE" && optionType !== "PE" && optionType !== "BUY" && optionType !== "SELL") continue;
    out.push({
      id: `fill:${tradeId}`,
      tradeId,
      underlying: String(row?.underlying || ""),
      optionType,
      strike: Number(row?.strike),
      quantity: Number(row?.quantity),
      entryPrice: Number(row?.entry_price),
      tradingsymbol: row?.tradingsymbol,
      timestamp: row?.timestamp,
      kind: "fill",
    });
  }
  return out;
}

function normalizeIndexSignals(rows: unknown): PaperBuyNotice[] {
  if (!Array.isArray(rows)) return [];
  const out: PaperBuyNotice[] = [];
  for (const row of rows) {
    const side = String(row?.side || row?.decision || "").toUpperCase();
    if (side !== "BUY" && side !== "SELL") continue;
    const symbol = String(row?.symbol || row?.underlying || "").toUpperCase();
    if (symbol !== "NIFTY" && symbol !== "SENSEX") continue;
    const unix = row?.bar_unix ?? "";
    const id = String(row?.id || `${symbol}|${side}|${unix}`);
    const score = side === "SELL" ? Number(row?.sell_score) : Number(row?.buy_score);
    out.push({
      id,
      tradeId: 0,
      underlying: symbol,
      optionType: side,
      strike: 0,
      quantity: 0,
      entryPrice: Number(row?.entry_price || 0),
      tradingsymbol: `${symbol} ${side}`,
      kind: "signal",
      score: Number.isFinite(score) ? score : undefined,
      source: "LIVE_INDEX",
    });
  }
  return out;
}

function indexEntryNotices(signals: any[]): PaperBuyNotice[] {
  if (!Array.isArray(signals)) return [];
  const out: PaperBuyNotice[] = [];
  for (const row of signals) {
    const source = String(row?.source || "");
    if (source !== "LIVE_INDEX") continue;
    const side = String(row?.option_type || "").toUpperCase();
    if (side !== "BUY" && side !== "SELL") continue;
    if (row?.marker_role === "exit" || /\|EXIT$/.test(String(row?.signal_id || ""))) continue;
    const symbol = String(row?.instrument || row?.underlying || "").toUpperCase();
    const unix = row?.time_unix ?? "";
    const id = `${symbol}|${side}|${unix}`;
    out.push({
      id,
      tradeId: Number(row?.paper_trade_id || 0),
      underlying: symbol,
      optionType: side,
      strike: Number(row?.strike || 0),
      quantity: Number(row?.quantity || 0),
      entryPrice: Number(row?.entry_price || row?.option_ltp || 0),
      tradingsymbol: row?.tradingsymbol || `${symbol} ${side}`,
      kind: "signal",
      score: row?.strategy_score != null ? Number(row.strategy_score) : undefined,
      source: String(row?.source || ""),
    });
  }
  return out;
}
