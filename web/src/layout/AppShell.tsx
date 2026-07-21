import { NavLink, Outlet, useLocation } from "react-router-dom";
import type { SSEStatus } from "../hooks";

const NAV: { to: string; label: string; ico: string; end?: boolean }[] = [
  { to: "/", label: "Overview", ico: "◉", end: true },
  { to: "/trade", label: "Trade", ico: "▣" },
  { to: "/agent", label: "Agent", ico: "◎" },
  { to: "/history", label: "History", ico: "▤" },
  { to: "/settings", label: "Settings", ico: "⚙" },
];

const TITLES: Record<string, string> = {
  "/": "Overview",
  "/trade": "Trade",
  "/agent": "Agent",
  "/history": "History",
  "/settings": "Settings",
};

export function AppShell({
  sseStatus,
  updated,
  mode,
  enabled,
}: {
  sseStatus: SSEStatus;
  updated: string;
  mode?: string;
  enabled?: boolean;
}) {
  const loc = useLocation();
  const title = TITLES[loc.pathname] ?? "Dashboard";
  const sseLabel =
    sseStatus === "open" ? "live" : sseStatus === "connecting" ? "connecting" : "offline";
  const modeCls =
    mode === "live" ? "live" : mode === "dry" || mode === "test" ? "dry" : "";

  return (
    <div className="app-shell">
      <nav className="app-nav" aria-label="Main">
        <div className="nav-brand">
          <span className="name">USDC Bot</span>
          <span className="tag">ops console</span>
        </div>
        {NAV.map((n) => (
          <NavLink
            key={n.to}
            to={n.to}
            end={n.end}
            className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
          >
            <span className="nav-ico" aria-hidden>
              {n.ico}
            </span>
            {n.label}
          </NavLink>
        ))}
        <div className="nav-foot">
          Paper + live micro.
          <br />
          Edge entry: not claimed.
        </div>
      </nav>

      <div className="app-main">
        <header className="app-top">
          <h1>{title}</h1>
          <div className="sub">
            {mode != null && (
              <span className={`badge ${modeCls}`}>{String(mode).toUpperCase()}</span>
            )}
            {enabled != null && (
              <span className={`badge ${enabled ? "on" : "off"}`}>
                {enabled ? "ON" : "OFF"}
              </span>
            )}
            <span>
              <span className={`dot ${sseStatus === "open" ? "" : "off"}`} />
              {sseLabel}
              {updated ? ` · ${updated}` : ""}
            </span>
          </div>
        </header>
        <main className="page">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
