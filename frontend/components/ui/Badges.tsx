import { AlertCircle, CheckCircle2, Clock3, Frown, Loader2, Meh, MinusCircle, Smile } from "lucide-react";

import type { CreatorStatus } from "@/types";

const base = "inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-2 py-1 text-xs font-medium ring-1 ring-inset";

export function SentimentBadge({ sentiment }: { sentiment: string | null }) {
  if (!sentiment) return <span className="text-sm text-slate-400">N/A</span>;
  const styles: Record<string, { cls: string; Icon: typeof Smile }> = {
    Positive: { cls: "bg-emerald-50 text-emerald-700 ring-emerald-200", Icon: Smile },
    Neutral: { cls: "bg-amber-50 text-amber-700 ring-amber-200", Icon: Meh },
    Negative: { cls: "bg-rose-50 text-rose-700 ring-rose-200", Icon: Frown },
  };
  const style = styles[sentiment] ?? { cls: "bg-slate-50 text-slate-500 ring-slate-200", Icon: MinusCircle };
  return (
    <span className={`${base} ${style.cls}`}>
      <style.Icon size={14} aria-hidden="true" />
      {sentiment}
    </span>
  );
}

const STATUS_STYLES: Record<CreatorStatus, { cls: string; Icon: typeof Smile; spin?: boolean }> = {
  Completed: { cls: "bg-emerald-50 text-emerald-700 ring-emerald-200", Icon: CheckCircle2 },
  Partial: { cls: "bg-amber-50 text-amber-700 ring-amber-200", Icon: AlertCircle },
  Failed: { cls: "bg-rose-50 text-rose-700 ring-rose-200", Icon: AlertCircle },
  Processing: { cls: "bg-indigo-50 text-indigo-700 ring-indigo-200", Icon: Loader2, spin: true },
  Pending: { cls: "bg-slate-50 text-slate-600 ring-slate-200", Icon: Clock3 },
};

export function StatusBadge({ status, title }: { status: CreatorStatus; title?: string | null }) {
  const style = STATUS_STYLES[status] ?? STATUS_STYLES.Pending;
  return (
    <span className={`${base} ${style.cls}`} title={title ?? undefined}>
      <style.Icon size={13} className={style.spin ? "animate-spin" : undefined} aria-hidden="true" />
      {status}
    </span>
  );
}
