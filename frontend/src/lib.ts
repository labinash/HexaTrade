import { useEffect, useState } from "react";
import { api } from "./api";

export function usePoll<T>(path: string, ms = 2000) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!path) return;
    let stop = false;
    let requestId = 0;
    let inFlight = false;
    const load = () => {
      if (stop || inFlight) return;
      inFlight = true;
      const id = ++requestId;
      const controller = new AbortController();
      const watchdog = window.setTimeout(() => controller.abort(), Math.max(ms * 15, 120000));
      api<T>(path, { signal: controller.signal })
        .then((value) => {
          if (!stop && id === requestId) {
            setData(value);
            setError("");
          }
        })
        .catch((err) => {
          if (stop || id !== requestId) return;
          const timedOut = err?.name === "AbortError";
          setError(timedOut ? "Request timed out. Retrying…" : err.message);
        })
        .finally(() => {
          if (id === requestId) inFlight = false;
          window.clearTimeout(watchdog);
        });
    };
    load();
    const timer = setInterval(load, ms);
    return () => {
      stop = true;
      requestId += 1;
      inFlight = false;
      clearInterval(timer);
    };
  }, [path, ms]);
  return { data, error };
}

const chartCache = new Map<string, { at: number; data: unknown }>();
const CHART_CACHE_MS = 60000;

export function clearChartCache() {
  chartCache.clear();
}

export function useChartQuery<T>(path: string, live: boolean) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [debounced, setDebounced] = useState(path);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(path), live ? 0 : 400);
    return () => window.clearTimeout(timer);
  }, [path, live]);

  useEffect(() => {
    let stop = false;
    const cached = !live ? chartCache.get(debounced) : undefined;
    if (cached && Date.now() - cached.at < CHART_CACHE_MS) {
      setData(cached.data as T);
      setError("");
      setLoading(false);
      return () => { stop = true; };
    }
    setLoading(true);
    setData(null);
    setError("");
    const controller = new AbortController();
    const watchdog = window.setTimeout(() => controller.abort(), live ? 20000 : 180000);
    api<T>(debounced, { signal: controller.signal })
      .then((value) => {
        if (stop) return;
        setData(value);
        setError("");
        if (!live) chartCache.set(debounced, { at: Date.now(), data: value });
      })
      .catch((err) => {
        if (stop) return;
        setError(err?.name === "AbortError" ? "Request timed out. Click OK to retry." : err.message);
        setData(null);
      })
      .finally(() => {
        window.clearTimeout(watchdog);
        if (!stop) setLoading(false);
      });
    const timer = live ? window.setInterval(() => {
      api<T>(debounced).then((value) => { if (!stop) { setData(value); setError(""); } }).catch(() => undefined);
    }, 4000) : 0;
    return () => {
      stop = true;
      controller.abort();
      window.clearTimeout(watchdog);
      if (timer) window.clearInterval(timer);
    };
  }, [debounced, live]);

  return { data, error, loading };
}

export function fmt(value: unknown) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  if (Number.isNaN(number)) return String(value);
  return number.toLocaleString("en-IN", { maximumFractionDigits: 2 });
}

export function fmt4(value: unknown) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  if (Number.isNaN(number)) return String(value);
  return number.toLocaleString("en-IN", { maximumFractionDigits: 4 });
}
