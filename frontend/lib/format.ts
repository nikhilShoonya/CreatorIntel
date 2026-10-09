import type { Platform } from "@/types";

export const NA = "N/A";

/** 1_200_000 -> "1.2M", 890_000 -> "890K", 950 -> "950" */
export function formatCompact(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NA;
  const abs = Math.abs(value);
  const units: [number, string][] = [
    [1e9, "B"],
    [1e6, "M"],
    [1e3, "K"],
  ];
  for (const [size, suffix] of units) {
    if (abs >= size) {
      const scaled = value / size;
      const digits = Math.abs(scaled) >= 100 ? 0 : 1;
      // Truncate rather than round (e.g. 25.863 -> 25.8)
      const pow = Math.pow(10, digits);
      const truncated = Math.trunc(scaled * pow) / pow;
      return `${truncated.toFixed(digits).replace(/\.0$/, "")}${suffix}`;
    }
  }
  return Math.trunc(value).toString();
}

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NA;
  return Math.round(value).toLocaleString("en-US");
}

export function formatPercent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NA;
  return `${value.toFixed(digits)}%`;
}

export function formatConfidence(value: number | null | undefined): string {
  if (value === null || value === undefined) return NA;
  return `${Math.round(value * 100)}%`;
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return NA;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return NA;
  return date.toLocaleString("en-IN", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export const PLATFORM_LABELS: Record<Platform | "facebook", string> = {
  youtube: "YouTube",
  instagram: "Instagram",
  facebook: "Facebook",
  invalid: "Invalid link",
  unsupported: "Unsupported",
};

export function platformLabel(value: string): string {
  return PLATFORM_LABELS[value as keyof typeof PLATFORM_LABELS] ?? value;
}

/** Only http(s) URLs are ever rendered as links. */
export function safeHref(url: string | null | undefined): string | undefined {
  if (!url) return undefined;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.toString() : undefined;
  } catch {
    return undefined;
  }
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  return (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
}

export function humanize(value: string | null | undefined): string {
  if (!value) return NA;
  return value.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}
