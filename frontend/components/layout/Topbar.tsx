"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Check, Loader2, RefreshCw, Search } from "lucide-react";

import { Dialog } from "@/components/ui/Dialog";

import { refreshAllData, useApi } from "@/hooks/useApi";
import { useStoredValue } from "@/hooks/useStoredValue";
import { api } from "@/lib/api";
import { initials } from "@/lib/format";
import { vpApi } from "@/lib/vpApi";

export const USER_NAME_KEY = "creatorintel:user-name";

export function Topbar() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [name] = useStoredValue(USER_NAME_KEY, "Team Member");

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    const q = query.trim();
    router.push(q ? `/creators?q=${encodeURIComponent(q)}` : "/creators");
  }

  return (
    <header className="sticky top-0 z-30 flex h-16 items-center justify-between gap-4 border-b border-line bg-white/90 px-4 backdrop-blur sm:px-6">
      <form onSubmit={onSubmit} role="search" className="relative w-full max-w-md">
        <Search size={17} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search creators, platform, genre, language…"
          aria-label="Search creators"
          maxLength={200}
          className="h-10 w-full rounded-lg border border-line bg-white pl-10 pr-3 text-sm outline-none placeholder:text-slate-400 focus:border-accent focus:ring-2 focus:ring-indigo-100"
        />
      </form>

      <div className="flex shrink-0 items-center gap-2">
        <RefreshDataButton />
        <Link href="/settings" className="flex shrink-0 items-center gap-3 rounded-lg px-2 py-1.5 hover:bg-slate-50" title="Profile & settings">
          <span className="flex h-9 w-9 items-center justify-center rounded-full bg-accent text-sm font-semibold text-white">
            {initials(name)}
          </span>
          <span className="hidden text-sm font-medium text-ink sm:block">{name}</span>
        </Link>
      </div>
    </header>
  );
}

/** One button for the whole site: reloads every piece of data on the current page from the backend. */
function RefreshDataButton() {
  const [clickBusy, setClickBusy] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const pathname = usePathname();

  const { data: jobs } = useApi("topbar-jobs", () => vpApi.jobs(), {
    refreshMs: (d) => (d?.some((j) => j.running) ? 4000 : false),
  });
  // Background re-fetch of every creator on the creator list (started by this button).
  const { data: creatorJob } = useApi("topbar-creator-refresh", () => api.creatorRefreshStatus(), {
    refreshMs: (d) => (d?.running ? 3000 : false),
  });

  const isVideoPage = pathname.includes("/video-performance");
  const isCreatorPage = pathname.startsWith("/creators");

  const runningJob = jobs?.find((j) => j.running);
  const creatorRunning = !!creatorJob?.running;
  let progressText = "Refreshing…";
  if (creatorRunning && creatorJob.progress_total > 0) {
    const percent = Math.round((creatorJob.progress_done / creatorJob.progress_total) * 100);
    progressText = `Refreshing… ${creatorJob.progress_done}/${creatorJob.progress_total} creators (${percent}%)`;
  } else if (runningJob && typeof runningJob.progress_done === "number" && typeof runningJob.progress_total === "number" && runningJob.progress_total > 0) {
    const percent = Math.round((runningJob.progress_done / runningJob.progress_total) * 100);
    progressText = `Refreshing… ${percent}%`;
  }

  const isBusy = clickBusy || !!runningJob || creatorRunning;
  const state = isBusy ? "busy" : updatedAt ? "done" : "idle";

  function markUpdated() {
    setUpdatedAt(new Date().toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" }));
    window.setTimeout(() => setUpdatedAt(null), 2000);
  }

  // When the background creator refresh finishes, reload the table so the new numbers show up.
  const wasCreatorRunning = useRef(false);
  useEffect(() => {
    if (wasCreatorRunning.current && !creatorRunning) {
      void refreshAllData().then(markUpdated);
    }
    wasCreatorRunning.current = creatorRunning;
  }, [creatorRunning]);

  async function onClick() {
    setClickBusy(true);
    if (isVideoPage) {
      try {
        await vpApi.runJob("metrics_refresh");
      } catch (e) {
        console.error("Failed to start metrics_refresh job", e);
      }
    } else if (isCreatorPage) {
      try {
        await api.refreshAllCreators();
      } catch (e) {
        console.error("Failed to start creator refresh", e);
      }
    }
    await refreshAllData();
    setClickBusy(false);
    markUpdated();
  }

  const loadingTitle = creatorRunning || !isVideoPage ? "Refreshing Creator List…" : "Refreshing Tracking Videos…";

  return (
    <>
      <button
        type="button"
        onClick={onClick}
        disabled={clickBusy}
        title={updatedAt ? `Reload all data · last refreshed at ${updatedAt}` : "Reload all data"}
        aria-live="polite"
        className="inline-flex h-9 items-center gap-2 rounded-lg border border-line bg-white px-3 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50 hover:text-ink disabled:cursor-wait"
      >
        {state === "done" ? (
          <Check size={16} className="text-emerald-600" />
        ) : (
          <RefreshCw size={16} />
        )}
        <span className="hidden md:inline">{state === "done" ? "Updated" : "Refresh data"}</span>
      </button>

      <Dialog open={state === "busy"} title={loadingTitle} onClose={() => { }} size="sm">
        <div className="flex flex-col items-center justify-center py-10">
          <Loader2 size={36} className="animate-spin text-accent mb-6" />
          <p className="text-base text-slate-700 font-medium">
            {progressText}
          </p>
        </div>
      </Dialog>
    </>
  );
}
