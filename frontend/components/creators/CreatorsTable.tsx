"use client";

import { useMemo, useRef, useState, type ReactNode } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown, Filter, Inbox, Loader2, Plus, Search, Trash2, X } from "lucide-react";

import { StatusBadge, SentimentBadge } from "@/components/ui/Badges";
import { Button, Card, EmptyState, ErrorBanner, Select } from "@/components/ui/controls";
import { ConfirmDialog } from "@/components/ui/Dialog";
import { useCreators, useFacets } from "@/hooks/useData";
import { useDebounce } from "@/hooks/useDebounce";
import { api } from "@/lib/api";
import { formatCompact, formatDateTime, platformLabel } from "@/lib/format";
import type { Creator, CreatorFilters, SortDir, SortKey } from "@/types";
import { formatChecked } from "../video-performance/format";
import { ChannelLinkCell, EngagementCell, GenreCell, NA_CELL, PlatformCell, TopVideoCell } from "./cells";
import { CreatorDetailsPanel } from "./CreatorDetailsPanel";
import { CreatorFormDialog } from "./CreatorFormDialog";
import { ExportMenu } from "./ExportMenu";
import { Pagination } from "./Pagination";
import { RowActions, type CreatorActions } from "./RowActions";

const STATUSES = ["Completed", "Partial", "Failed", "Processing", "Pending"];

interface Props {
  title: string;
  uploadId?: string;
  initialQuery?: string;
  /** Parent bumps this to force a refetch (e.g. upload progress). */
  refreshToken?: number;
  live?: boolean;
  headerExtra?: ReactNode;
  emptyState?: ReactNode;
  /** Called after a creator is added, edited or deleted (e.g. to refresh upload counts). */
  onChanged?: () => void;
}

type Toast = { tone: "success" | "error"; message: string } | null;

export function CreatorsTable({ title, uploadId, initialQuery = "", refreshToken = 0, live = false, headerExtra, emptyState, onChanged }: Props) {
  const [search, setSearch] = useState(initialQuery);
  const [platform, setPlatform] = useState("");
  const [genre, setGenre] = useState("");
  const [language, setLanguage] = useState("");
  const [sentiment, setSentiment] = useState("");
  const [status, setStatus] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir } | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [filtersOpen, setFiltersOpen] = useState(true);
  const [localRefresh, setLocalRefresh] = useState(0);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [toast, setToast] = useState<Toast>(null);
  const [checked, setChecked] = useState<Set<number>>(new Set());
  const [formTarget, setFormTarget] = useState<Creator | "new" | null>(null);
  const [deleteTargets, setDeleteTargets] = useState<Creator[] | null>(null);
  const toastTimer = useRef<number | undefined>(undefined);

  const debouncedSearch = useDebounce(search.trim(), 300);
  const filters: CreatorFilters = useMemo(
    () => ({
      q: debouncedSearch || undefined,
      platform: platform || undefined,
      genre: genre || undefined,
      language: language || undefined,
      sentiment: sentiment || undefined,
      status: status || undefined,
      upload_id: uploadId,
      sort_by: sort?.key,
      sort_dir: sort?.dir,
    }),
    [debouncedSearch, platform, genre, language, sentiment, status, uploadId, sort],
  );

  const token = refreshToken + localRefresh;
  const { data, error, loading, reload } = useCreators(filters, page, pageSize, token, live);
  const facets = useFacets(uploadId, token);

  const activeFilterCount = [platform, genre, language, sentiment, status].filter(Boolean).length;
  const hasQuery = activeFilterCount > 0 || debouncedSearch !== "";

  function showToast(next: Toast) {
    window.clearTimeout(toastTimer.current);
    setToast(next);
    toastTimer.current = window.setTimeout(() => setToast(null), 4000);
  }

  function goToPage(next: number) {
    setPage(next);
    setChecked(new Set());
  }

  function updateFilter(setter: (value: string) => void) {
    return (value: string) => {
      setter(value);
      goToPage(1);
    };
  }

  function clearFilters() {
    setPlatform("");
    setGenre("");
    setLanguage("");
    setSentiment("");
    setStatus("");
    setSearch("");
    goToPage(1);
  }

  function toggleSort(key: SortKey) {
    goToPage(1);
    setSort((current) => {
      if (!current || current.key !== key) return { key, dir: "desc" };
      if (current.dir === "desc") return { key, dir: "asc" };
      return null;
    });
  }

  async function runAction(action: () => Promise<{ message: string }>) {
    try {
      const result = await action();
      showToast({ tone: "success", message: result.message });
      setLocalRefresh((n) => n + 1);
    } catch (err) {
      showToast({ tone: "error", message: err instanceof Error ? err.message : "Action failed" });
    }
  }

  function afterChange(message: string) {
    showToast({ tone: "success", message });
    setLocalRefresh((n) => n + 1);
    onChanged?.();
  }

  async function deleteConfirmed() {
    const targets = deleteTargets ?? [];
    const result =
      targets.length === 1 ? await api.deleteCreator(targets[0].id) : await api.bulkDeleteCreators(targets.map((c) => c.id));
    setChecked((current) => {
      const next = new Set(current);
      targets.forEach((c) => next.delete(c.id));
      return next;
    });
    if (selectedId !== null && targets.some((c) => c.id === selectedId)) setSelectedId(null);
    afterChange(result.message);
  }

  function toggleRow(id: number) {
    setChecked((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const actions: CreatorActions = {
    onView: (creator) => setSelectedId(creator.id),
    onRetry: (creator) => runAction(() => api.retry(creator.id)),
    onReanalyze: (creator) => runAction(() => api.reanalyze(creator.id)),
    onEdit: (creator) => setFormTarget(creator),
    onDelete: (creator) => setDeleteTargets([creator]),
  };

  const options = (values: string[] | undefined, labeler: (v: string) => string = (v) => v) =>
    (values ?? []).map((value) => ({ value, label: labeler(value) }));

  const rows = data?.items ?? [];
  const firstIndex = data ? (data.page - 1) * data.page_size : 0;
  const checkedRows = rows.filter((c) => checked.has(c.id));
  const allChecked = rows.length > 0 && checkedRows.length === rows.length;

  return (
    <Card className="overflow-hidden">
      {/* Header + toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-3 px-5 pb-3 pt-4">
        <div className="flex items-center gap-3">
          <h2 className="text-lg font-semibold tracking-tight text-ink">
            {title} {data && <span className="text-slate-400">({data.total})</span>}
          </h2>
          {loading && <Loader2 size={16} className="animate-spin text-slate-400" aria-label="Loading" />}
          {headerExtra}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
            <input
              type="search"
              value={search}
              onChange={(event) => {
                setSearch(event.target.value);
                goToPage(1);
              }}
              placeholder="Search by name or link…"
              aria-label="Search creators in table"
              maxLength={200}
              className="h-9 w-64 rounded-lg border border-line bg-white pl-9 pr-3 text-sm shadow-xs outline-none placeholder:text-slate-400 focus:border-accent focus:ring-2 focus:ring-indigo-100"
            />
          </div>
          <Button onClick={() => setFiltersOpen((open) => !open)} aria-expanded={filtersOpen} aria-controls="creator-filters">
            <Filter size={15} />
            Filter
            {activeFilterCount > 0 && (
              <span className="rounded-full bg-accent px-1.5 text-[11px] font-semibold leading-5 text-white">{activeFilterCount}</span>
            )}
          </Button>
          <Button variant="primary" onClick={() => setFormTarget("new")}>
            <Plus size={16} />
            Add creator
          </Button>
          <ExportMenu
            filters={filters}
            disabled={!data || data.total === 0}
            onError={(message) => showToast({ tone: "error", message })}
          />
        </div>
      </div>

      {filtersOpen && (
        <div id="creator-filters" className="flex flex-wrap items-center gap-2 px-5 pb-4">
          <Select label="Platform" value={platform} onChange={updateFilter(setPlatform)} options={options(facets.data?.platforms, platformLabel)} className="w-40" />
          <Select label="Genre" value={genre} onChange={updateFilter(setGenre)} options={options(facets.data?.genres)} className="w-48" />
          <Select label="Language" value={language} onChange={updateFilter(setLanguage)} options={options(facets.data?.languages)} className="w-40" />
          <Select label="Sentiment" value={sentiment} onChange={updateFilter(setSentiment)} options={options(facets.data?.sentiments)} className="w-40" />
          <Select label="Status" value={status} onChange={updateFilter(setStatus)} options={options(STATUSES)} className="w-40" />
          {hasQuery && (
            <Button variant="ghost" onClick={clearFilters}>
              <X size={15} />
              Clear
            </Button>
          )}
        </div>
      )}

      {error && (
        <div className="px-5 pb-4">
          <ErrorBanner message={error} onRetry={reload} />
        </div>
      )}

      {checkedRows.length > 0 && (
        <div className="flex items-center justify-between gap-3 border-t border-indigo-100 bg-accent-soft px-5 py-2.5 text-sm">
          <span className="font-medium text-accent">
            {checkedRows.length} selected
          </span>
          <div className="flex gap-2">
            <Button variant="ghost" onClick={() => setChecked(new Set())}>
              Clear selection
            </Button>
            <Button variant="danger" onClick={() => setDeleteTargets(checkedRows)}>
              <Trash2 size={15} />
              Delete selected
            </Button>
          </div>
        </div>
      )}

      {/* Table */}
      {data && data.total === 0 ? (
        hasQuery ? (
          <EmptyState icon={<Search size={22} />} title="No creators match your filters" description="Try a different search term or clear the filters." action={<Button onClick={clearFilters}>Clear filters</Button>} />
        ) : (
          (emptyState ?? <EmptyState icon={<Inbox size={22} />} title="No creators yet" description="Upload an Excel or CSV file to start analysing creators." />)
        )
      ) : (
        <div className="table-scroll max-h-[70vh] overflow-auto border-t border-line">
          <table className="w-full min-w-[1480px] border-separate border-spacing-0 text-sm">
            <thead className="sticky top-0 z-20">
              <tr className="text-left text-xs font-semibold text-slate-600">
                <Th className="sticky left-0 z-30 w-10 min-w-10 max-w-10 bg-slate-50 !px-0 text-center">
                  <input
                    type="checkbox"
                    aria-label="Select all rows on this page"
                    checked={allChecked}
                    onChange={() => setChecked(allChecked ? new Set() : new Set(rows.map((c) => c.id)))}
                    className="h-4 w-4 cursor-pointer accent-[var(--color-accent)]"
                  />
                </Th>
                <Th className="sticky left-10 z-30 w-12 min-w-12 max-w-12 bg-slate-50 !px-0 text-center">#</Th>
                <Th className="sticky left-[88px] z-30 min-w-[190px] bg-slate-50 shadow-[inset_-1px_0_0_var(--color-line)]">Channel Name</Th>
                <Th>Platform</Th>
                <Th>Channel Link</Th>
                <SortTh label="Subscribers / Followers" sortKey="audience" sort={sort} onSort={toggleSort} align="center" />
                <SortTh label="Average Views" sortKey="average_views" sort={sort} onSort={toggleSort} align="center" />
                <Th>Top Performing Video</Th>
                <SortTh label="Engagement Rate" sortKey="engagement_rate" sort={sort} onSort={toggleSort} align="center" />
                <SortTh label="Genre" sortKey="genre" sort={sort} onSort={toggleSort} />
                <Th>Language</Th>
                <SortTh label="Sentiment" sortKey="sentiment" sort={sort} onSort={toggleSort} />
                <SortTh label="Last Checked" sortKey="data_fetched_at" sort={sort} onSort={toggleSort} align="left" />
                <Th>Status</Th>
                <Th className="text-right">Actions</Th>
              </tr>
            </thead>
            <tbody>
              {!data && !error
                ? Array.from({ length: 5 }, (_, i) => <SkeletonRow key={i} />)
                : rows.map((creator, index) => (
                    <CreatorRow
                      key={creator.id}
                      index={firstIndex + index + 1}
                      creator={creator}
                      actions={actions}
                      checked={checked.has(creator.id)}
                      onToggle={() => toggleRow(creator.id)}
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
          onPage={goToPage}
          onPageSize={(size) => {
            setPageSize(size);
            goToPage(1);
          }}
        />
      )}

      <CreatorDetailsPanel
        creatorId={selectedId}
        onClose={() => setSelectedId(null)}
        onAction={async (kind, id) => {
          try {
            const result = kind === "retry" ? await api.retry(id) : await api.reanalyze(id);
            showToast({ tone: "success", message: result.message });
            setLocalRefresh((n) => n + 1);
          } catch (err) {
            showToast({ tone: "error", message: err instanceof Error ? err.message : "Action failed" });
          }
        }}
        onEdit={(creator) => {
          setSelectedId(null);
          setFormTarget(creator);
        }}
        onDelete={(creator) => {
          setSelectedId(null);
          setDeleteTargets([creator]);
        }}
      />

      <CreatorFormDialog target={formTarget} uploadId={uploadId} onClose={() => setFormTarget(null)} onSaved={afterChange} />

      <ConfirmDialog
        open={deleteTargets !== null}
        title={deleteTargets && deleteTargets.length > 1 ? `Delete ${deleteTargets.length} creators?` : "Delete creator?"}
        message={
          deleteTargets && deleteTargets.length === 1 ? (
            <>
              <span className="font-medium text-ink">{deleteTargets[0].channel_name}</span> and all of its data will be removed from
              every upload. This cannot be undone.
            </>
          ) : (
            "The selected creators and all of their data will be removed from every upload. This cannot be undone."
          )
        }
        confirmLabel="Delete"
        onConfirm={deleteConfirmed}
        onClose={() => setDeleteTargets(null)}
      />

      {toast && (
        <div
          role="status"
          className={`fixed bottom-6 right-6 z-50 max-w-sm rounded-lg px-4 py-3 text-sm shadow-lg ${
            toast.tone === "success" ? "bg-slate-900 text-white" : "bg-rose-600 text-white"
          }`}
        >
          {toast.message}
        </div>
      )}
    </Card>
  );
}

function Th({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <th className={`whitespace-nowrap border-b border-line bg-slate-50 px-4 py-3 ${className}`}>{children}</th>;
}

function SortTh({ label, sortKey, sort, onSort, align = "left" }: { label: string; sortKey: SortKey; sort: { key: SortKey; dir: SortDir } | null; onSort: (key: SortKey) => void; align?: "left" | "center" | "right" }) {
  const active = sort?.key === sortKey;
  const Icon = !active ? ArrowUpDown : sort.dir === "desc" ? ArrowDown : ArrowUp;
  const alignClass = align === "right" ? "text-right" : align === "center" ? "text-center" : "text-left";
  return (
    <th
      className={`whitespace-nowrap border-b border-line bg-slate-50 px-4 py-3 ${alignClass}`}
      aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
    >
      <button type="button" onClick={() => onSort(sortKey)} className={`inline-flex items-center gap-1.5 hover:text-ink ${active ? "text-accent" : ""}`}>
        {label}
        <Icon size={13} className={active ? "text-accent" : "text-slate-400"} aria-hidden="true" />
      </button>
    </th>
  );
}

const td = "border-b border-line px-4 py-3 align-middle";

function CreatorRow({
  index,
  creator,
  actions,
  checked,
  onToggle,
}: {
  index: number;
  creator: Creator;
  actions: CreatorActions;
  checked: boolean;
  onToggle: () => void;
}) {
  // Sticky cells need an opaque background so scrolled columns never show through.
  const bg = checked ? "bg-indigo-50" : "bg-white";
  return (
    <tr className={`group ${checked ? "bg-indigo-50" : ""}`}>
      <td className={`${td} sticky left-0 z-10 w-10 min-w-10 max-w-10 ${bg} !px-0 text-center group-hover:bg-slate-50`}>
        <input
          type="checkbox"
          aria-label={`Select ${creator.channel_name}`}
          checked={checked}
          onChange={onToggle}
          className="h-4 w-4 cursor-pointer accent-[var(--color-accent)]"
        />
      </td>
      <td className={`${td} sticky left-10 z-10 w-12 min-w-12 max-w-12 ${bg} !px-0 text-center text-slate-500 group-hover:bg-slate-50`}>{index}</td>
      <td className={`${td} sticky left-[88px] z-10 ${bg} font-medium text-ink shadow-[inset_-1px_0_0_var(--color-line)] group-hover:bg-slate-50`}>
        <button type="button" onClick={() => actions.onView(creator)} className="max-w-[200px] truncate text-left hover:text-accent" title={creator.channel_name}>
          {creator.channel_name}
        </button>
      </td>
      <td className={`${td} group-hover:bg-slate-50`}>
        <PlatformCell platform={creator.platform} />
      </td>
      <td className={`${td} group-hover:bg-slate-50`}>
        <ChannelLinkCell creator={creator} />
      </td>
      <td className={`${td} text-center font-medium tabular-nums text-slate-800 group-hover:bg-slate-50`} title={creator.audience_count?.toLocaleString("en-US")}>
        {creator.audience_count === null ? NA_CELL : formatCompact(creator.audience_count)}
      </td>
      <td
        className={`${td} text-center tabular-nums text-slate-800 group-hover:bg-slate-50`}
        title={averageTooltip(creator)}
      >
        {creator.average_views === null ? NA_CELL : formatCompact(creator.average_views)}
      </td>
      <td className={`${td} group-hover:bg-slate-50`}>
        <TopVideoCell creator={creator} />
      </td>
      <td className={`${td} text-center tabular-nums group-hover:bg-slate-50`}>
        <EngagementCell creator={creator} />
      </td>
      <td className={`${td} group-hover:bg-slate-50`}>
        <GenreCell creator={creator} />
      </td>
      <td className={`${td} text-slate-700 group-hover:bg-slate-50`}>{creator.language ?? NA_CELL}</td>
      <td className={`${td} group-hover:bg-slate-50`}>
        <SentimentBadge sentiment={creator.sentiment} />
      </td>
      <td
        className={`${td} whitespace-nowrap text-slate-600 group-hover:bg-slate-50`}
        title={creator.data_fetched_at ? formatDateTime(creator.data_fetched_at) : undefined}
      >
        {formatChecked(creator.data_fetched_at, true)}
      </td>
      <td className={`${td} group-hover:bg-slate-50`}>
        <StatusBadge status={creator.status} title={creator.error_message} />
      </td>
      <td className={`${td} group-hover:bg-slate-50`}>
        <RowActions creator={creator} actions={actions} />
      </td>
    </tr>
  );
}

function SkeletonRow() {
  return (
    <tr>
      {Array.from({ length: 15 }, (_, i) => (
        <td key={i} className={td}>
          <div className="h-4 animate-pulse rounded bg-slate-100" />
        </td>
      ))}
    </tr>
  );
}

function averageTooltip(creator: Creator): string | undefined {
  if (!creator.average_views_sample_count) return undefined;
  const parts = [`Average of the latest ${creator.average_views_sample_count}`];
  if (creator.average_views_long !== null) parts.push(`Long-form (>3 min): ${formatCompact(creator.average_views_long)}`);
  if (creator.average_views_short !== null) parts.push(`Short-form (<=3 min): ${formatCompact(creator.average_views_short)}`);
  return parts.join("\n");
}
