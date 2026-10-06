import { AlertCircle, Ban, CheckCircle2, Clock3, Frown, Loader2, Meh, PauseCircle, Radio, Smile, VideoOff } from "lucide-react";

import type { VpCreator, VpStatus } from "@/types/videoPerformance";

const base = "inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-2 py-1 text-xs font-medium ring-1 ring-inset";

const VIDEO_STATUS: Record<VpStatus, { cls: string; Icon: typeof Smile; spin?: boolean }> = {
  Tracking: { cls: "bg-emerald-50 text-emerald-700 ring-emerald-200", Icon: Radio },
  Partial: { cls: "bg-amber-50 text-amber-700 ring-amber-200", Icon: AlertCircle },
  Paused: { cls: "bg-slate-50 text-slate-600 ring-slate-200", Icon: PauseCircle },
  Pending: { cls: "bg-slate-50 text-slate-600 ring-slate-200", Icon: Clock3 },
  Processing: { cls: "bg-indigo-50 text-indigo-700 ring-indigo-200", Icon: Loader2, spin: true },
  Completed: { cls: "bg-sky-50 text-sky-700 ring-sky-200", Icon: CheckCircle2 },
  Failed: { cls: "bg-rose-50 text-rose-700 ring-rose-200", Icon: AlertCircle },
  Unsupported: { cls: "bg-zinc-100 text-zinc-600 ring-zinc-200", Icon: Ban },
  "Video Down": { cls: "bg-red-50 text-red-700 ring-red-300", Icon: VideoOff },
};

export function VpStatusBadge({ status, reason }: { status: VpStatus; reason?: string | null }) {
  const style = VIDEO_STATUS[status] ?? VIDEO_STATUS.Pending;
  return (
    <span className={`${base} ${style.cls}`} title={reason ?? undefined}>
      <style.Icon size={13} className={style.spin ? "animate-spin" : undefined} aria-hidden="true" />
      {status}
    </span>
  );
}

const CREATOR_STATUS: Record<VpCreator["status"], string> = {
  Active: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  Pending: "bg-slate-50 text-slate-600 ring-slate-200",
  Paused: "bg-slate-50 text-slate-600 ring-slate-200",
  Failed: "bg-rose-50 text-rose-700 ring-rose-200",
  Unsupported: "bg-zinc-100 text-zinc-600 ring-zinc-200",
};

export function CreatorStatusBadge({ status, reason }: { status: VpCreator["status"]; reason?: string | null }) {
  return (
    <span className={`${base} ${CREATOR_STATUS[status]}`} title={reason ?? undefined}>
      {status}
    </span>
  );
}

// Sentiment is a polarity scale: green / gray midpoint / red (validated palette).
export const SENTIMENT_COLORS = { Positive: "#059669", Neutral: "#94a3b8", Negative: "#e11d48" } as const;

const SENTIMENT_STYLE = {
  Positive: { cls: "bg-emerald-50 text-emerald-700 ring-emerald-200", Icon: Smile },
  Neutral: { cls: "bg-slate-100 text-slate-600 ring-slate-200", Icon: Meh },
  Negative: { cls: "bg-rose-50 text-rose-700 ring-rose-200", Icon: Frown },
} as const;

export function VpSentimentBadge({ sentiment, confidence }: { sentiment: string | null; confidence?: number | null }) {
  if (!sentiment || !(sentiment in SENTIMENT_STYLE)) return <span className="text-sm text-slate-400">N/A</span>;
  const style = SENTIMENT_STYLE[sentiment as keyof typeof SENTIMENT_STYLE];
  return (
    <span
      className={`${base} ${style.cls}`}
      title={confidence !== null && confidence !== undefined ? `Confidence ${Math.round(confidence * 100)}%` : undefined}
    >
      <style.Icon size={13} aria-hidden="true" />
      {sentiment}
    </span>
  );
}
