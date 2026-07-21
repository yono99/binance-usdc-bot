import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api } from "../api";
import { useEventSource, usePoll, type SSEStatus } from "../hooks";
import type { Account, OpenOrder, Stats, Status } from "../types";

type Ctx = {
  stats: Stats | null;
  status: Status | null;
  account: Account | null;
  available: string[];
  orders: OpenOrder[];
  tick: number;
  updated: string;
  sseStatus: SSEStatus;
  isLive: boolean;
  refreshAll: () => void;
  refetchOrders: () => void;
};

const DashboardCtx = createContext<Ctx | null>(null);

export function DashboardProvider({ children }: { children: ReactNode }) {
  const { status: sseStatus, subscribe } = useEventSource("/api/stream");
  const [stats, setStats] = useState<Stats | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [updated, setUpdated] = useState("");
  const [tick, setTick] = useState(0);

  const { data: account } = usePoll(api.account, 10000);
  const { data: symbolsResp } = usePoll(api.symbols, 600000);
  const { data: ordersResp, refetch: refetchOrders } = usePoll(api.openOrders, 10000);
  const available = symbolsResp?.symbols ?? [];

  useEffect(() => {
    const unsubs = [
      subscribe("snapshot", (e) => {
        const snap = e.data as { status?: Status; stats?: Stats };
        if (snap.status) setStatus(snap.status);
        if (snap.stats) setStats(snap.stats);
        setUpdated(new Date().toLocaleTimeString());
      }),
      subscribe("status", (e) => {
        setStatus(e.data as Status);
        setUpdated(new Date().toLocaleTimeString());
      }),
      subscribe("stats", (e) => {
        setStats(e.data as Stats);
        setUpdated(new Date().toLocaleTimeString());
      }),
      subscribe("trade", () => {
        setTick((t) => t + 1);
        api.stats().then(setStats).catch(() => {});
        setUpdated(new Date().toLocaleTimeString());
      }),
      subscribe("ping", () => {}),
    ];
    return () => unsubs.forEach((u) => u());
  }, [subscribe]);

  useEffect(() => {
    const id = setInterval(() => {
      setTick((t) => t + 1);
      setUpdated(new Date().toLocaleTimeString());
    }, 10000);
    return () => clearInterval(id);
  }, []);

  const refreshAll = useCallback(() => {
    api.stats().then(setStats).catch(() => {});
    api.status().then(setStatus).catch(() => {});
    refetchOrders();
  }, [refetchOrders]);

  const isLive = (account?.mode === "live" || status?.mode === "live") ?? false;

  const value = useMemo(
    () => ({
      stats,
      status,
      account,
      available,
      orders: ordersResp?.orders ?? [],
      tick,
      updated,
      sseStatus,
      isLive,
      refreshAll,
      refetchOrders,
    }),
    [
      stats,
      status,
      account,
      available,
      ordersResp,
      tick,
      updated,
      sseStatus,
      isLive,
      refreshAll,
      refetchOrders,
    ],
  );

  return <DashboardCtx.Provider value={value}>{children}</DashboardCtx.Provider>;
}

export function useDashboard(): Ctx {
  const c = useContext(DashboardCtx);
  if (!c) throw new Error("useDashboard outside provider");
  return c;
}
