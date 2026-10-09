import { formatCompact, NA } from "@/lib/format";

/** +500 / -120 / N/A */
export function formatSigned(value: number | null | undefined): string {
  if (value === null || value === undefined) return NA;
  const text = formatCompact(Math.abs(value));
  return value > 0 ? `+${text}` : value < 0 ? `-${text}` : "0";
}

export function formatGrowth(value: number | null | undefined): string {
  if (value === null || value === undefined) return NA;
  return `${value > 0 ? "+" : ""}${value.toFixed(1)}%`;
}

/** "Today 06:30", "Yesterday 18:02", "29 Sept" (with `withTime`: "29 Sept, 06:30 pm"; older years add the year) */
export function formatChecked(value: string | null | undefined, withTime = false): string {
  if (!value) return "Never";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Never";
  const now = new Date();
  const time = date.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
  const dayDiff = Math.round(
    (new Date(now.toDateString()).getTime() - new Date(date.toDateString()).getTime()) / 86_400_000,
  );
  if (dayDiff === 0) return `Today ${time}`;
  if (dayDiff === 1) return `Yesterday ${time}`;
  const day = date.toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    ...(date.getFullYear() !== now.getFullYear() ? { year: "numeric" } : {}),
  });
  return withTime ? `${day}, ${time}` : day;
}

export function growthTone(value: number | null | undefined): string {
  if (value === null || value === undefined || value === 0) return "text-slate-500";
  return value > 0 ? "text-emerald-700" : "text-rose-700";
}
