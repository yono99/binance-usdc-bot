import type {
  Account,
  AgentAB,
  AgentHealth,
  AgentPlan,
  AgentSettings,
  GeminiTrader,
  GeminiUsage,
  NewsLogEntry,
  OpenOrder,
  Ohlcv,
  ScreenLogEntry,
  Settings,
  Stats,
  Status,
  TradesResp,
} from "./types";

async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return r.json();
}

async function postJSON<T>(url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  return r.json();
}

export const api = {
  stats: () => getJSON<Stats>("/api/stats"),
  status: () => getJSON<Status>("/api/status"),
  account: () => getJSON<Account>("/api/account"),
  openOrders: () => getJSON<{ orders: OpenOrder[]; paper?: boolean; error?: string }>("/api/open-orders"),
  cancelOrder: (symbol: string, order_id: string) =>
    postJSON<{ ok: boolean; error?: string }>("/api/cancel-order", { symbol, order_id }),
  settings: (mode?: string) =>
    getJSON<Settings>(`/api/settings${mode != null ? "?mode=" + encodeURIComponent(mode) : ""}`),
  symbols: () => getJSON<{ symbols: string[]; error?: string }>("/api/symbols"),
  ohlcv: (symbol: string, tf: string, limit = 120) =>
    getJSON<Ohlcv>(`/api/ohlcv?symbol=${encodeURIComponent(symbol)}&tf=${tf}&limit=${limit}`),
  trades: (q: string) => getJSON<TradesResp>(`/api/trades${q ? "?" + q : ""}`),
  csvHref: (q: string) => `/api/trades.csv${q ? "?" + q : ""}`,
  newsLog: (limit = 100) => getJSON<{ log: NewsLogEntry[] }>(`/api/news-log?limit=${limit}`),
  screenLog: (limit = 200) => getJSON<{ log: ScreenLogEntry[] }>(`/api/screen-log?limit=${limit}`),
  geminiUsage: (recent = 30) => getJSON<GeminiUsage>(`/api/gemini-usage?recent=${recent}`),
  geminiTrader: () => getJSON<GeminiTrader>("/api/gemini-trader"),
  geminiModels: () => getJSON<{ models: string[] }>("/api/gemini-models"),
  agentSettings: () => getJSON<AgentSettings>("/api/agent-settings"),
  saveAgentSettings: (body: Partial<AgentSettings>) =>
    postJSON<AgentSettings>("/api/agent-settings", body),
  agentHealth: () => getJSON<AgentHealth>("/api/agent-health"),
  agentPlan: () => getJSON<AgentPlan>("/api/plan"),
  agentAB: () => getJSON<AgentAB>("/api/ab"),

  saveSettings: (body: Record<string, unknown>) => postJSON<Settings>("/api/settings", body),
  setMode: (mode: string) => postJSON<{ ok: boolean; mode: string }>("/api/mode", { mode }),
  validateKey: (key: string, secret: string) =>
    postJSON<{ valid: boolean; balance_usdc?: number; balance_usdt?: number; error?: string }>("/api/validate-key", { key, secret }),
  notifyTest: () => postJSON<{ ok: boolean; error?: string }>("/api/notify-test"),
  close: (symbol: string) => postJSON<{ ok: boolean }>("/api/close", { symbol }),
  closeAll: () => postJSON<{ ok: boolean }>("/api/close-all"),
  deleteTrade: (id: number) =>
    fetch(`/api/trades/${id}`, { method: "DELETE" }).then((r) => r.json()),
  clearTrades: () => postJSON<{ ok: boolean; removed: number }>("/api/trades/clear"),
  resetGeminiUsage: () =>
    postJSON<{ ok: boolean; removed: number }>("/api/gemini-usage/reset"),
  ddReset: (mode?: string) =>
    postJSON<{ ok: boolean; mode: string; note: string }>("/api/dd-reset", mode ? { mode } : undefined),
};

export const f = (n: number | null | undefined, d = 2): string =>
  n == null || Number.isNaN(Number(n)) ? "—" : Number(n).toFixed(d);
// Harga: desimal ADAPTIF (~6 angka penting) → gerak koin sub-sen (SHIB/PEPE 0.0044)
// tak lagi tersembunyi oleh pembulatan 4-desimal tetap. Cap 2..8 desimal.
export const fp = (n: number | null | undefined): string => {
  if (n == null || Number.isNaN(Number(n))) return "—";
  const x = Math.abs(Number(n));
  if (x === 0) return "0.00";
  const d = Math.min(8, Math.max(2, 5 - Math.floor(Math.log10(x))));
  return Number(n).toFixed(d);
};
export const cls = (v: number | null | undefined): string =>
  v == null ? "" : v > 0 ? "pos" : v < 0 ? "neg" : "";

// Format ISO timestamp → Asia/Jakarta (UTC+07:00, tanpa DST)
// Contoh output: "14 Jul 2026, 18:23 WIB" untuk tabel ringkas,
// atau        "14 Jul 2026, 18:23:11" untuk sel lengkap.
const _MONTHS_ID = [
  "Jan", "Feb", "Mar", "Apr", "Mei", "Jun",
  "Jul", "Agu", "Sep", "Okt", "Nov", "Des",
];
function _wibParts(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return new Date(d.getTime() + 7 * 60 * 60 * 1000);
}
export const fmtWIB = (iso: string | null | undefined): string => {
  const w = _wibParts(iso);
  if (!w) return iso || "—";
  const dd = String(w.getUTCDate()).padStart(2, "0");
  const mm = _MONTHS_ID[w.getUTCMonth()];
  const yy = w.getUTCFullYear();
  const hh = String(w.getUTCHours()).padStart(2, "0");
  const mi = String(w.getUTCMinutes()).padStart(2, "0");
  const ss = String(w.getUTCSeconds()).padStart(2, "0");
  return `${dd} ${mm} ${yy}, ${hh}:${mi}:${ss} WIB`;
};
export const fmtWIBdate = (iso: string | null | undefined): string => {
  const w = _wibParts(iso);
  if (!w) return iso || "—";
  const dd = String(w.getUTCDate()).padStart(2, "0");
  const mm = _MONTHS_ID[w.getUTCMonth()];
  const yy = w.getUTCFullYear();
  const hh = String(w.getUTCHours()).padStart(2, "0");
  const mi = String(w.getUTCMinutes()).padStart(2, "0");
  return `${dd} ${mm} ${yy}, ${hh}:${mi} WIB`;
};
