import { useCallback, useEffect, useRef, useState } from "react";

/** Polling sederhana: panggil fetcher tiap `ms`, plus refetch manual. */
export function usePoll<T>(fetcher: () => Promise<T>, ms = 10000) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fnRef = useRef(fetcher);
  fnRef.current = fetcher;

  const refetch = useCallback(async () => {
    try {
      setData(await fnRef.current());
      setError(null);
    } catch (e) {
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
