import type { SentimentCounts } from "@/types/videoPerformance";
import { SENTIMENT_COLORS } from "./badges";

const ORDER = [
  { key: "positive", label: "Positive" },
  { key: "neutral", label: "Neutral" },
  { key: "negative", label: "Negative" },
] as const;

/**
 * Proportion bar for a sentiment distribution. Identity never relies on color alone:
 * the legend always shows label + count + share, and each segment has a tooltip.
 */
export function SentimentBar({ counts, showLegend = true, height = 10 }: { counts: SentimentCounts; showLegend?: boolean; height?: number }) {
  const analysed = counts.positive + counts.neutral + counts.negative;
  const segments = ORDER.map((item) => ({ ...item, value: counts[item.key], color: SENTIMENT_COLORS[item.label] })).filter(
    (s) => s.value > 0,
  );

  return (
    <div>
      {analysed === 0 ? (
        <div className="rounded bg-slate-100" style={{ height }} aria-label="No analysed videos yet" />
      ) : (
        <div className="flex w-full gap-[2px]" style={{ height }} role="img" aria-label={segments.map((s) => `${s.label} ${s.value}`).join(", ")}>
          {segments.map((segment, index) => (
            <div
              key={segment.key}
              title={`${segment.label}: ${segment.value} (${Math.round((segment.value / analysed) * 100)}%)`}
              className={`${index === 0 ? "rounded-l" : ""} ${index === segments.length - 1 ? "rounded-r" : ""} transition-opacity hover:opacity-80`}
              style={{ width: `${(segment.value / analysed) * 100}%`, backgroundColor: segment.color, minWidth: 4 }}
            />
          ))}
        </div>
      )}
      {showLegend && (
        <div className="mt-2.5 flex flex-wrap gap-x-5 gap-y-1 text-xs text-slate-600">
          {ORDER.map((item) => {
            const value = counts[item.key];
            return (
              <span key={item.key} className="inline-flex items-center gap-1.5">
                <span className="h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: SENTIMENT_COLORS[item.label] }} aria-hidden="true" />
                {item.label}
                <span className="font-semibold text-ink tabular-nums">{value}</span>
                {analysed > 0 && <span className="text-muted">({Math.round((value / analysed) * 100)}%)</span>}
              </span>
            );
          })}
          {counts.not_analyzed > 0 && <span className="text-muted">{counts.not_analyzed} not analysed</span>}
        </div>
      )}
    </div>
  );
}
