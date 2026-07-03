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
