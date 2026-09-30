"use client";

import { useCallback, useEffect, useRef, useState } from "react";

interface ApiState<T> {
  key: string | null;
  data?: T;
  error?: string;
}

type Refresh<T> = number | false | ((data: T | undefined) => number | false);

interface Options<T> {
  /** Poll interval in ms, or a function of the latest data (false = no polling). */
  refreshMs?: Refresh<T>;
  /** Keep showing the previous key's data while the new key loads (tables). */
  keepPrevious?: boolean;
}

const ERROR_RETRY_MS = 5000;

/**
 * Fetches whenever `key` changes (null = skip).
 *
 * Polling is sequential: the next request is scheduled only after the previous
 * one finished, so a slow backend never causes overlapping requests or
 * discarded responses.
 */
export function useApi<T>(key: string | null, fetcher: () => Promise<T>, options: Options<T> = {}) {
  const { refreshMs, keepPrevious = false } = options;
  const [state, setState] = useState<ApiState<T>>({ key: null });
  const [tick, setTick] = useState(0);
  const fetcherRef = useRef(fetcher);
  const refreshRef = useRef<Refresh<T> | undefined>(refreshMs);

  useEffect(() => {
    fetcherRef.current = fetcher;
    refreshRef.current = refreshMs;
  });

  useEffect(() => {
    if (key === null) return;
    let cancelled = false;
    let timer: number | undefined;
    let lastData: T | undefined;

    const schedule = (data: T | undefined, failed: boolean) => {
      const refresh = refreshRef.current;
      const ms = typeof refresh === "function" ? refresh(data) : refresh;
      if (ms) timer = window.setTimeout(run, failed ? Math.max(ms, ERROR_RETRY_MS) : ms);
    };

    function run() {
      fetcherRef.current().then(
        (data) => {
          if (cancelled) return;
          lastData = data;
          setState({ key, data });
          schedule(data, false);
        },
        (error: unknown) => {
          if (cancelled) return;
          setState((prev) => ({
            key,
            data: prev.key === key ? prev.data : undefined,
            error: error instanceof Error ? error.message : "Something went wrong",
          }));
          schedule(lastData, true);
        },
      );
    }

    run();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [key, tick]);

  const current = state.key === key;
  const reload = useCallback(() => setTick((t) => t + 1), []);

  return {
    data: current || keepPrevious ? state.data : undefined,
    error: current ? state.error : undefined,
    loading: key !== null && !current,
    reload,
  };
}
