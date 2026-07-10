import { useCallback, useEffect, useRef, useState } from "react";

/** Polling sederhana: panggil fetcher tiap `ms`, plus refetch manual. */
export function usePoll<T>(fetcher: () => Promise<T>, ms = 10000) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fnRef = useRef(fetcher);
  fnRef.current = fetcher;
  const seqRef = useRef(0);   // cegah respons USANG (out-of-order) menimpa data yg lebih baru

  const refetch = useCallback(async () => {
    const seq = ++seqRef.current;
    try {
      const result = await fnRef.current();
      if (seq !== seqRef.current) return;   // refetch lebih baru sudah menyusul → buang hasil ini
      setData(result);
      setError(null);
    } catch (e) {
      if (seq !== seqRef.current) return;
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    refetch();
    const id = setInterval(refetch, ms);
    return () => clearInterval(id);
  }, [ms, refetch]);

  return { data, error, refetch };
}

// ===== SSE (Server-Sent Events) — real-time push =====

export type SSEEvent = {
  type: string;       // snapshot | status | stats | trade | order_update | candle | ...
  data: unknown;
  ts: number;
};

export type SSEStatus = "connecting" | "open" | "closed";

/**
 * Satu koneksi SSE multiplex untuk SEMUA data dashboard.
 *
 * Backend /api/stream mengirim dua format frame:
 *   1. `event: <type>\ndata: {...}\n\n`   → event bernama (snapshot, ping)
 *   2. `data: {type, data, ts}\n\n`        → unnamed (default) — isi sudah {type,data,ts}
 *
 * Komponen subscribe via subscribe(type, handler). Handler dipanggil tiap event
 * dengan tipe cocok. Return fungsi unsubscribe.
 *
 * Auto-reconnect: EventSource native sudah handle (readyState CONNECTING ulang).
 * `onerror` cuma update status badge; tidak manual retry.
 */
export function useEventSource(url = "/api/stream") {
  const [status, setStatus] = useState<SSEStatus>("connecting");
  const [lastEvent, setLastEvent] = useState<SSEEvent | null>(null);
  // map type → Set<handler>. Pakai ref supaya handler registrasi di render gak
  // bikin reconnect. register() return unreg fn (useEffect cleanup friendly).
  const handlers = useRef<Map<string, Set<(e: SSEEvent) => void>>>(new Map());
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    const es = new EventSource(url);
    esRef.current = es;

    es.onopen = () => setStatus("open");
    es.onerror = () => setStatus("closed");   // EventSource auto-reconnect native

    // frame unnamed: data berisi {type, data, ts}
    es.onmessage = (e) => {
      try {
        const parsed: SSEEvent = JSON.parse(e.data);
        setLastEvent(parsed);
        const hs = handlers.current.get(parsed.type);
        if (hs) for (const h of hs) h(parsed);
        // wildcard "*" — listen semua
        const all = handlers.current.get("*");
        if (all) for (const h of all) h(parsed);
      } catch { /* ignore malformed */ }
    };

    // frame bernama: event: snapshot / ping → dispatch juga
    const namedTypes = ["snapshot", "ping"];
    const namedDispatch = (e: MessageEvent, type: string) => {
      const ev: SSEEvent = { type, data: e.data ? safeParse(e.data) : {}, ts: Date.now() / 1000 };
      setLastEvent(ev);
      const hs = handlers.current.get(type);
      if (hs) for (const h of hs) h(ev);
      const all = handlers.current.get("*");
      if (all) for (const h of all) h(ev);
    };
    namedTypes.forEach((t) => es.addEventListener(t, (e) => namedDispatch(e as MessageEvent, t)));

    return () => es.close();
  }, [url]);

  /** Subscribe ke tipe event tertentu. Return fungsi unsubscribe. */
  const subscribe = useCallback((type: string, handler: (e: SSEEvent) => void) => {
    if (!handlers.current.has(type)) handlers.current.set(type, new Set());
    handlers.current.get(type)!.add(handler);
    return () => {
      handlers.current.get(type)?.delete(handler);
    };
  }, []);

  return { status, lastEvent, subscribe, es: esRef };
}

function safeParse(s: string): unknown {
  try { return JSON.parse(s); } catch { return s; }
}
