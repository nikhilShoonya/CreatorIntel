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

// Last response per key, kept for the browser session (module state survives client-side navigation).
// Revisiting a page shows this immediately while fresh data loads in the background.
const responseCache = new Map<string, unknown>();
const CACHE_LIMIT = 200;

function remember(key: string, data: unknown) {
  responseCache.delete(key);
  responseCache.set(key, data);
  if (responseCache.size > CACHE_LIMIT) responseCache.delete(responseCache.keys().next().value as string);
}

/**
 * Fetches whenever `key` changes (null = skip).
 *
 * Polling is sequential: the next request is scheduled only after the previous
 * one finished, so a slow backend never causes overlapping requests or
 * discarded responses. Polling pauses while the browser tab is hidden.
 * Previously loaded data for the same key is shown instantly (stale-while-revalidate).
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
    const cacheKey: string = key;
    let cancelled = false;
    let timer: number | undefined;
    let lastData: T | undefined;
    let waitingForTab = false;

    // While the tab is hidden, polling pauses; it resumes (with an immediate refresh) when the tab is visible again.
    const onVisible = () => {
      if (waitingForTab && document.visibilityState === "visible") {
        waitingForTab = false;
        run();
      }
    };
    document.addEventListener("visibilitychange", onVisible);

    const schedule = (data: T | undefined, failed: boolean) => {
      const refresh = refreshRef.current;
      const ms = typeof refresh === "function" ? refresh(data) : refresh;
      if (!ms) return;
      timer = window.setTimeout(
        () => {
          if (document.visibilityState === "hidden") waitingForTab = true;
          else run();
        },
        failed ? Math.max(ms, ERROR_RETRY_MS) : ms,
      );
    };

    function run() {
      fetcherRef.current().then(
        (data) => {
          if (cancelled) return;
          lastData = data;
          remember(cacheKey, data);
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
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [key, tick]);

  const current = state.key === key;
  const reload = useCallback(() => setTick((t) => t + 1), []);
  const cached = key !== null && !current ? (responseCache.get(key) as T | undefined) : undefined;

  return {
    data: current ? state.data : (cached ?? (keepPrevious ? state.data : undefined)),
    error: current ? state.error : undefined,
    loading: key !== null && !current,
    reload,
  };
}
