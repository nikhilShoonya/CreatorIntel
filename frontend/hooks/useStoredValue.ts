"use client";

import { useCallback, useSyncExternalStore } from "react";

const EVENT = "creatorintel-storage";

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function subscribe(callback: () => void) {
  window.addEventListener("storage", callback);
  window.addEventListener(EVENT, callback);
  return () => {
    window.removeEventListener("storage", callback);
    window.removeEventListener(EVENT, callback);
  };
}

/** Per-browser convenience value (e.g. display name, last upload). Never used for secrets. */
export function useStoredValue(key: string, fallback: string): [string, (value: string | null) => void] {
  const value = useSyncExternalStore(
    subscribe,
    () => read(key) ?? fallback,
    () => fallback,
  );
  const setValue = useCallback(
    (next: string | null) => {
      try {
        if (next === null) window.localStorage.removeItem(key);
        else window.localStorage.setItem(key, next);
      } catch {
        /* storage unavailable (private mode) */
      }
      window.dispatchEvent(new Event(EVENT));
    },
    [key],
  );
  return [value, setValue];
}
