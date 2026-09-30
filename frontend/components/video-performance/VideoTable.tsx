"use client";

import { useMemo, useState, type ReactNode } from "react";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  ExternalLink,
  History,
  ListVideo,
  MoreVertical,
  Pause,
  Pencil,
  Play,
  RefreshCw,
  RotateCcw,
  Search,
  Trash2,
  X,
} from "lucide-react";

import { Button, EmptyState, ErrorBanner, Select } from "@/components/ui/controls";
import { ConfirmDialog } from "@/components/ui/Dialog";
import { Menu } from "@/components/ui/Menu";
import { PlatformIcon } from "@/components/ui/PlatformIcon";
import { Pagination } from "@/components/creators/Pagination";
import { useApi } from "@/hooks/useApi";
import { useDebounce } from "@/hooks/useDebounce";
import { formatCompact, formatPercent, platformLabel, safeHref } from "@/lib/format";
import { vpApi } from "@/lib/vpApi";
import type { VpFilters, VpSortKey, VpStatus, VpVideo, VpVideoList } from "@/types/videoPerformance";
import { VpSentimentBadge, VpStatusBadge } from "./badges";
import { EditVideoDialog, HistoryDialog } from "./dialogs";
import { formatChecked, formatGrowth, formatSigned, growthTone } from "./format";

const STATUSES: VpStatus[] = ["Tracking", "Partial", "Pending", "Processing", "Paused", "Completed", "Failed", "Unsupported"];
const BUSY: VpStatus[] = ["Pending", "Processing"];

interface Props {
  refreshToken: number;
  onToast: (tone: "success" | "error", message: string) => void;
  onChanged: () => void;
}

export function VideoTable({ refreshToken, onToast, onChanged }: Props) {
  const [search, setSearch] = useState("");
  const [platform, setPlatform] = useState("");
  const [creator, setCreator] = useState("");
  const [status, setStatus] = useState("");
  const [sort, setSort] = useState<{ key: VpSortKey; dir: "asc" | "desc" } | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [checked, setChecked] = useState<Set<number>>(new Set());
  const [localRefresh, setLocalRefresh] = useState(0);
  const [editing, setEditing] = useState<VpVideo | null>(null);
  const [historyId, setHistoryId] = useState<number | null>(null);
  const [deleting, setDeleting] = useState<VpVideo[] | null>(null);

  const q = useDebounce(search.trim(), 300);
  const filters: VpFilters = useMemo(
    () => ({
      q: q || undefined,
      platform: platform || undefined,
      creator: creator || undefined,
      status: status || undefined,
      sort_by: sort?.key,
      sort_dir: sort?.dir,
    }),
    [q, platform, creator, status, sort],
  );
  const token = refreshToken + localRefresh;
  const key = JSON.stringify({ filters, page, pageSize, token });
  const { data, error, reload } = useApi(key, () => vpApi.videos(filters, page, pageSize), {
    keepPrevious: true,
    refreshMs: (d: VpVideoList | undefined) => (d?.items.some((v) => BUSY.includes(v.status)) ? 2500 : false),
  });
  const facets = useApi(`vp-facets:${token}`, () => vpApi.facets(), { keepPrevious: true });

  const rows = data?.items ?? [];
  const failedCount = data?.status_counts.Failed ?? 0;
  const selected = rows.filter((v) => checked.has(v.id));
  const allChecked = rows.length > 0 && selected.length === rows.length;
  const hasFilters = Boolean(q || platform || creator || status);

  function resetPaging() {
    setPage(1);
    setChecked(new Set());
  }
  const setFilter = (setter: (v: string) => void) => (value: string) => {
    setter(value);
    resetPaging();
  };

  async function act(action: () => Promise<{ message: string }>) {
    try {
      const result = await action();
      onToast("success", result.message);
      setChecked(new Set());
      setLocalRefresh((n) => n + 1);
      onChanged();
    } catch (err) {
      onToast("error", err instanceof Error ? err.message : "Action failed");
    }
  }

  function toggleSort(sortKey: VpSortKey) {
    resetPaging();
    setSort((current) => (!current || current.key !== sortKey ? { key: sortKey, dir: "desc" } : current.dir === "desc" ? { key: sortKey, dir: "asc" } : null));
  }

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 px-5 pb-4">
        <div className="relative">
          <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            type="search"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              resetPaging();
            }}
            placeholder="Search title, caption, creator…"
            aria-label="Search tracked videos"
            className="h-9 w-64 rounded-lg border border-line bg-white pl-9 pr-3 text-sm shadow-xs outline-none placeholder:text-slate-400 focus:border-accent focus:ring-2 focus:ring-indigo-100"
          />
        </div>
        <Select label="Platform" value={platform} onChange={setFilter(setPlatform)} options={[{ value: "youtube", label: "YouTube" }, { value: "instagram", label: "Instagram" }]} className="w-36" />
        <Select label="Creator" value={creator} onChange={setFilter(setCreator)} options={(facets.data?.creators ?? []).map((c) => ({ value: c, label: c }))} className="w-48" />
        <Select
          label="Status"
          value={status}
          onChange={setFilter(setStatus)}
          options={STATUSES.map((s) => ({ value: s, label: `${s}${data?.status_counts[s] ? ` (${data.status_counts[s]})` : ""}` }))}
          className="w-44"
        />
        {hasFilters && (
          <Button
            variant="ghost"
            onClick={() => {
              setSearch("");
              setPlatform("");
              setCreator("");
              setStatus("");
              resetPaging();
            }}
          >
            <X size={15} /> Clear
          </Button>
        )}
        <div className="ml-auto">
          <Button onClick={() => act(() => vpApi.retryFailed())} disabled={failedCount === 0} title="Retry every video whose last check failed">
            <RotateCcw size={15} />
            Retry failed{failedCount ? ` (${failedCount})` : ""}
          </Button>
        </div>
      </div>

      {selected.length > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-indigo-100 bg-accent-soft px-5 py-2.5 text-sm">
          <span className="font-medium text-accent">{selected.length} selected</span>
          <div className="flex flex-wrap gap-2">
            <Button variant="ghost" onClick={() => setChecked(new Set())}>
              Clear selection
            </Button>
            <Button onClick={() => act(() => vpApi.bulk(selected.map((v) => v.id), "resume"))}>
              <Play size={15} /> Start tracking
            </Button>
            <Button onClick={() => act(() => vpApi.bulk(selected.map((v) => v.id), "pause"))}>
              <Pause size={15} /> Stop tracking
            </Button>
            <Button variant="danger" onClick={() => setDeleting(selected)}>
              <Trash2 size={15} /> Delete
            </Button>
          </div>
        </div>
      )}

      {error && (
        <div className="px-5 pb-4">
          <ErrorBanner message={error} onRetry={reload} />
        </div>
      )}

      {data && data.total === 0 ? (
        <EmptyState
          icon={<ListVideo size={22} />}
          title={hasFilters ? "No videos match your filters" : "No tracked videos yet"}
          description={hasFilters ? "Try a different search or clear the filters." : "Upload a video list, add a video, or track a creator to start."}
        />
      ) : (
        <div className="table-scroll max-h-[68vh] overflow-auto border-t border-line">
          <table className="w-full min-w-[1500px] border-separate border-spacing-0 text-sm">
            <thead className="sticky top-0 z-20 text-left text-xs font-semibold text-slate-600">
              <tr>
                <Th className="sticky left-0 z-30 w-10 min-w-10 max-w-10 px-0 text-center">
                  <input
                    type="checkbox"
                    aria-label="Select all rows on this page"
                    checked={allChecked}
                    onChange={() => setChecked(allChecked ? new Set() : new Set(rows.map((v) => v.id)))}
                    className="h-4 w-4 cursor-pointer accent-[var(--color-accent)]"
                  />
                </Th>
                <Th className="sticky left-10 z-30 w-12 min-w-12 max-w-12 px-0 text-center">#</Th>
                <Th className="sticky left-[88px] z-30 min-w-[260px] shadow-[inset_-1px_0_0_var(--color-line)]">Video</Th>
                <Th>Platform</Th>
                <Th>Creator</Th>
                <SortTh label="Current Views" k="current_views" sort={sort} onSort={toggleSort} />
                <Th className="text-right">Previous Views</Th>
                <SortTh label="Views Gained" k="views_gained" sort={sort} onSort={toggleSort} />
                <SortTh label="Growth %" k="growth_pct" sort={sort} onSort={toggleSort} />
                <SortTh label="Engagement Rate" k="engagement_rate" sort={sort} onSort={toggleSort} />
                <Th>Sentiment</Th>
                <SortTh label="Last Checked" k="last_checked_at" sort={sort} onSort={toggleSort} align="left" />
                <Th>Tracking Status</Th>
                <Th className="text-right">Actions</Th>
              </tr>
            </thead>
            <tbody>
              {!data && !error
                ? Array.from({ length: 5 }, (_, i) => (
                    <tr key={i}>
                      <td colSpan={14} className="border-b border-line px-4 py-4">
                        <div className="h-4 animate-pulse rounded bg-slate-100" />
                      </td>
                    </tr>
                  ))
                : rows.map((video, index) => (
                    <VideoRow
                      key={video.id}
                      index={(data!.page - 1) * data!.page_size + index + 1}
                      video={video}
                      checked={checked.has(video.id)}
                      onToggle={() =>
                        setChecked((current) => {
                          const next = new Set(current);
                          if (next.has(video.id)) next.delete(video.id);
                          else next.add(video.id);
                          return next;
                        })
                      }
                      onAction={(action) => act(() => vpApi.videoAction(video.id, action))}
                      onHistory={() => setHistoryId(video.id)}
                      onEdit={() => setEditing(video)}
                      onDelete={() => setDeleting([video])}
                    />
                  ))}
            </tbody>
          </table>
        </div>
      )}

      {data && data.total > 0 && (
        <Pagination
          page={data.page}
          totalPages={data.total_pages}
          total={data.total}
          pageSize={pageSize}
          onPage={(p) => {
            setPage(p);
            setChecked(new Set());
          }}
          onPageSize={(size) => {
            setPageSize(size);
            resetPaging();
          }}
        />
      )}

      <EditVideoDialog
        video={editing}
        onClose={() => setEditing(null)}
        onSaved={(message) => {
          onToast("success", message);
          setLocalRefresh((n) => n + 1);
          onChanged();
        }}
      />
      <HistoryDialog videoId={historyId} onClose={() => setHistoryId(null)} />
      <ConfirmDialog
        open={deleting !== null}
        title={deleting && deleting.length > 1 ? `Delete ${deleting.length} videos?` : "Delete tracked video?"}
        message={
          deleting && deleting.length === 1 ? (
            <>
              <span className="font-medium text-ink">{deleting[0].display_title}</span> and its whole view history will be removed.
            </>
          ) : (
            "The selected videos and their view history will be removed."
          )
        }
        confirmLabel="Delete"
        onConfirm={async () => {
          const targets = deleting ?? [];
          const result = targets.length === 1 ? await vpApi.deleteVideo(targets[0].id) : await vpApi.bulk(targets.map((v) => v.id), "delete");
          onToast("success", result.message);
          setChecked(new Set());
          setLocalRefresh((n) => n + 1);
          onChanged();
        }}
        onClose={() => setDeleting(null)}
      />
    </div>
  );
}

function Th({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <th className={`whitespace-nowrap border-b border-line bg-slate-50 px-4 py-3 ${className}`}>{children}</th>;
}

function SortTh({
  label,
  k,
  sort,
  onSort,
  align = "right",
}: {
  label: string;
  k: VpSortKey;
  sort: { key: VpSortKey; dir: "asc" | "desc" } | null;
  onSort: (k: VpSortKey) => void;
  align?: "left" | "right";
}) {
  const active = sort?.key === k;
  const Icon = !active ? ArrowUpDown : sort.dir === "desc" ? ArrowDown : ArrowUp;
  return (
    <th
      className={`whitespace-nowrap border-b border-line bg-slate-50 px-4 py-3 ${align === "right" ? "text-right" : ""}`}
      aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
    >
      <button type="button" onClick={() => onSort(k)} className={`inline-flex items-center gap-1.5 hover:text-ink ${active ? "text-accent" : ""}`}>
        {label}
        <Icon size={13} className={active ? "text-accent" : "text-slate-400"} aria-hidden="true" />
      </button>
    </th>
  );
}

const td = "border-b border-line px-4 py-3 align-middle";
const NA = <span className="text-slate-400">N/A</span>;

function VideoRow({
  index,
  video,
  checked,
  onToggle,
  onAction,
  onHistory,
  onEdit,
  onDelete,
}: {
  index: number;
  video: VpVideo;
  checked: boolean;
  onToggle: () => void;
  onAction: (action: "refresh" | "retry" | "pause" | "resume") => void;
  onHistory: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const href = safeHref(video.video_url);
  const bg = checked ? "bg-indigo-50" : "bg-white";
  const busy = BUSY.includes(video.status);
  const paused = video.status === "Paused" || video.status === "Completed";
  const retryable = video.status === "Failed" || video.status === "Partial" || video.status === "Unsupported";

  return (
    <tr className={`group ${checked ? "bg-indigo-50" : ""}`}>
      <td className={`${td} sticky left-0 z-10 w-10 min-w-10 max-w-10 ${bg} px-0 text-center group-hover:bg-slate-50`}>
        <input type="checkbox" aria-label={`Select ${video.display_title}`} checked={checked} onChange={onToggle} className="h-4 w-4 cursor-pointer accent-[var(--color-accent)]" />
      </td>
      <td className={`${td} sticky left-10 z-10 w-12 min-w-12 max-w-12 ${bg} px-0 text-center text-slate-500 group-hover:bg-slate-50`}>{index}</td>
      <td className={`${td} sticky left-[88px] z-10 ${bg} shadow-[inset_-1px_0_0_var(--color-line)] group-hover:bg-slate-50`}>
        <div className="max-w-[300px]">
          {href ? (
            <a href={href} target="_blank" rel="noopener noreferrer" className="group/link inline-flex max-w-full items-center gap-1 font-medium text-slate-800 hover:text-accent" title={video.display_title}>
              <span className="truncate">{video.display_title}</span>
              <ExternalLink size={12} className="shrink-0 text-slate-300 group-hover/link:text-accent" aria-hidden="true" />
            </a>
          ) : (
            <span className="font-medium">{video.display_title}</span>
          )}
          {video.source === "discovered" && <div className="text-[11px] text-indigo-600">Auto-detected</div>}
        </div>
      </td>
      <td className={`${td} group-hover:bg-slate-50`}>
        <span className="inline-flex items-center gap-2 whitespace-nowrap text-slate-700">
          <PlatformIcon platform={video.platform} />
          {platformLabel(video.platform)}
        </span>
      </td>
      <td className={`${td} max-w-[180px] truncate text-slate-700 group-hover:bg-slate-50`} title={video.creator_name ?? undefined}>
        {video.creator_name ?? NA}
      </td>
      <td className={`${td} text-right font-semibold tabular-nums text-ink group-hover:bg-slate-50`} title={video.current_views?.toLocaleString("en-US")}>
        {video.current_views === null ? NA : formatCompact(video.current_views)}
      </td>
      <td className={`${td} text-right tabular-nums text-slate-600 group-hover:bg-slate-50`}>
        {video.previous_views === null ? <span className="text-slate-400">-</span> : formatCompact(video.previous_views)}
      </td>
      <td className={`${td} text-right tabular-nums group-hover:bg-slate-50 ${growthTone(video.views_gained)}`}>
        {video.views_gained === null ? <span className="text-slate-400">-</span> : formatSigned(video.views_gained)}
      </td>
      <td className={`${td} text-right tabular-nums group-hover:bg-slate-50 ${growthTone(video.growth_pct)}`}>
        {video.growth_pct === null ? <span className="text-slate-400">-</span> : formatGrowth(video.growth_pct)}
      </td>
      <td className={`${td} text-right tabular-nums group-hover:bg-slate-50`} title={video.engagement_basis ? "(likes + comments) / views" : undefined}>
        {video.engagement_rate === null ? NA : formatPercent(video.engagement_rate)}
      </td>
      <td className={`${td} group-hover:bg-slate-50`}>
        <VpSentimentBadge sentiment={video.sentiment} confidence={video.sentiment_confidence} />
      </td>
      <td className={`${td} whitespace-nowrap text-slate-600 group-hover:bg-slate-50`}>{formatChecked(video.last_checked_at)}</td>
      <td className={`${td} group-hover:bg-slate-50`}>
        <VpStatusBadge status={video.status} reason={video.status_reason} />
      </td>
      <td className={`${td} group-hover:bg-slate-50`}>
        <div className="flex items-center justify-end gap-1">
          <button type="button" onClick={onHistory} className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100 hover:text-ink" title="View history" aria-label={`History of ${video.display_title}`}>
            <History size={16} />
          </button>
          <Menu
            label={`Actions for ${video.display_title}`}
            width={200}
            trigger={(props) => (
              <button type="button" {...props} className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100 hover:text-ink" aria-label={`More actions for ${video.display_title}`}>
                <MoreVertical size={16} />
              </button>
            )}
            items={[
              { label: "Open video", icon: <ExternalLink size={15} />, disabled: !href, onSelect: () => href && window.open(href, "_blank", "noopener,noreferrer") },
              { label: "View history", icon: <History size={15} />, onSelect: onHistory },
              { label: "Refresh now", icon: <RefreshCw size={15} />, disabled: busy, onSelect: () => onAction("refresh") },
              paused
                ? { label: "Start tracking", icon: <Play size={15} />, disabled: busy, onSelect: () => onAction("resume") }
                : { label: "Stop tracking", icon: <Pause size={15} />, disabled: busy, onSelect: () => onAction("pause") },
              { label: "Retry", icon: <RotateCcw size={15} />, disabled: !retryable || busy, onSelect: () => onAction("retry") },
              { label: "Edit", icon: <Pencil size={15} />, onSelect: onEdit },
              { label: "Delete", icon: <Trash2 size={15} />, danger: true, onSelect: onDelete },
            ]}
          />
        </div>
      </td>
    </tr>
  );
}
