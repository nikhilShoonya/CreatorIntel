import { ChevronLeft, ChevronRight } from "lucide-react";

export const PAGE_SIZES = [10, 25, 50, 100];

function pageList(current: number, total: number): (number | "…")[] {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const pages = new Set([1, total, current - 1, current, current + 1].filter((p) => p >= 1 && p <= total));
  const sorted = [...pages].sort((a, b) => a - b);
  const result: (number | "…")[] = [];
  sorted.forEach((page, index) => {
    if (index > 0 && page - sorted[index - 1] > 1) result.push("…");
    result.push(page);
  });
  return result;
}

interface Props {
  page: number;
  totalPages: number;
  total: number;
  pageSize: number;
  onPage: (page: number) => void;
  onPageSize: (size: number) => void;
}

export function Pagination({ page, totalPages, total, pageSize, onPage, onPageSize }: Props) {
  const start = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const end = Math.min(page * pageSize, total);
  const navButton = "flex h-8 w-8 items-center justify-center rounded-md border border-line bg-white text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40";

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line px-5 py-3 text-sm text-muted">
      <div className="flex items-center gap-3">
        <span>
          Showing {start} to {end} of {total} results
        </span>
        <label className="flex items-center gap-2">
          <span className="sr-only sm:not-sr-only">Rows</span>
          <select
            value={pageSize}
            onChange={(event) => onPageSize(Number(event.target.value))}
            className="h-8 rounded-md border border-line bg-white px-2 text-sm text-slate-700"
            aria-label="Rows per page"
          >
            {PAGE_SIZES.map((size) => (
              <option key={size} value={size}>
                {size}
              </option>
            ))}
          </select>
        </label>
      </div>
      <nav className="flex items-center gap-1" aria-label="Pagination">
        <button type="button" className={navButton} onClick={() => onPage(page - 1)} disabled={page <= 1} aria-label="Previous page">
          <ChevronLeft size={16} />
        </button>
        {pageList(page, totalPages).map((item, index) =>
          item === "…" ? (
            <span key={`gap-${index}`} className="px-1.5">
              …
            </span>
          ) : (
            <button
              key={item}
              type="button"
              onClick={() => onPage(item)}
              aria-current={item === page ? "page" : undefined}
              className={`h-8 min-w-8 rounded-md px-2 text-sm font-medium ${
                item === page ? "bg-accent text-white" : "border border-line bg-white text-slate-600 hover:bg-slate-50"
              }`}
            >
              {item}
            </button>
          ),
        )}
        <button type="button" className={navButton} onClick={() => onPage(page + 1)} disabled={page >= totalPages} aria-label="Next page">
          <ChevronRight size={16} />
        </button>
      </nav>
    </div>
  );
}
