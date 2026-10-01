"use client";

import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

/** Width of an element, kept up to date with ResizeObserver (charts draw in real pixels, never stretched). */
function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

/** Tiny bar sparkline for KPI cards. `null` = no data that day (drawn as a faint stub). */
export function MiniBars({ values, color, label }: { values: (number | null)[]; color: string; label: string }) {
  const bars = values.slice(-12);
  const max = Math.max(0, ...bars.map((v) => v ?? 0));
  const width = 64;
  const height = 30;
  const gap = 2;
  const barWidth = (width - gap * (bars.length - 1)) / Math.max(bars.length, 1);
  return (
    <svg width={width} height={height} role="img" aria-label={label} className="shrink-0">
      <title>{label}</title>
      {bars.map((value, index) => {
        const h = value && max > 0 ? Math.max(3, (value / max) * height) : 2;
        return (
          <rect
            key={index}
            x={index * (barWidth + gap)}
            y={height - h}
            width={barWidth}
            height={h}
            rx={1.5}
            fill={color}
            opacity={value ? 0.35 + (0.65 * (index + 1)) / bars.length : 0.18}
          />
        );
      })}
    </svg>
  );
}

/** Tiny line of a video's daily views gained (per-row trend in the ranking tables). */
export function MiniLine({ values, label }: { values: (number | null)[]; label: string }) {
  const known = values.map((v, i) => [i, v] as const).filter((p): p is readonly [number, number] => p[1] !== null);
  if (known.length < 2) return <span className="text-xs text-slate-400" title="Trend appears after two daily checks">—</span>;
  const width = 56;
  const height = 20;
  const min = Math.min(...known.map((p) => p[1]));
  const max = Math.max(...known.map((p) => p[1]));
  const first = known[0][0];
  const span = Math.max(known[known.length - 1][0] - first, 1);
  const points = known.map(([i, v]) => `${((i - first) / span) * (width - 4) + 2},${max === min ? height / 2 : height - 2 - ((v - min) / (max - min)) * (height - 4)}`);
  const last = known[known.length - 1][1];
  const average = known.reduce((sum, p) => sum + p[1], 0) / known.length;
  const rising = last >= average;
  return (
    <svg width={width} height={height} role="img" aria-label={label}>
      <title>{label}</title>
      <polyline points={points.join(" ")} fill="none" stroke={rising ? "#059669" : "#e11d48"} strokeWidth={1.75} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

export interface TrendDatum {
  label: string; // x-axis label, e.g. "25 Sep"
  fullLabel: string; // tooltip title
  value: number | null; // null = no data that day
}

/** Area chart with hover tooltip. Days without data are left empty, never drawn as zero. */
export function TrendChart({
  data,
  format,
  change,
  color = "#4f46e5",
}: {
  data: TrendDatum[];
  format: (value: number) => string;
  change: (value: number, previous: number) => string | null;
  color?: string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const height = 250;
  const pad = { top: 12, right: 12, bottom: 26, left: 46 };
  const values = data.map((d) => d.value).filter((v): v is number => v !== null);
  const rawMax = Math.max(0, ...values);
  const rawMin = Math.min(0, ...values);
  const ticks = niceTicks(rawMin, rawMax);
  const yMin = ticks[0];
  const yMax = ticks[ticks.length - 1];
  const innerW = Math.max(width - pad.left - pad.right, 1);
  const innerH = height - pad.top - pad.bottom;
  const x = (i: number) => pad.left + (data.length > 1 ? (i / (data.length - 1)) * innerW : innerW / 2);
  const y = (v: number) => pad.top + innerH - ((v - yMin) / (yMax - yMin || 1)) * innerH;

  const known = data.map((d, i) => [i, d.value] as const).filter((p): p is readonly [number, number] => p[1] !== null);
  const line = known.map(([i, v], n) => `${n ? "L" : "M"}${x(i)},${y(v)}`).join(" ");
  const area = known.length > 1 ? `${line} L${x(known[known.length - 1][0])},${y(Math.max(yMin, 0))} L${x(known[0][0])},${y(Math.max(yMin, 0))} Z` : "";
  const labelEvery = Math.ceil(data.length / Math.max(Math.floor(innerW / 64), 1));
  const gradientId = `trend-${color.replace("#", "")}`;

  function onMove(event: ReactPointerEvent<SVGRectElement>) {
    const box = event.currentTarget.getBoundingClientRect();
    const ratio = (event.clientX - box.left) / box.width;
    const index = Math.round(ratio * (data.length - 1));
    setHover(Math.min(Math.max(index, 0), data.length - 1));
  }

  const hovered = hover !== null ? data[hover] : null;
  const previous = hover !== null ? [...data.slice(0, hover)].reverse().find((d) => d.value !== null) : undefined;
  const delta = hovered?.value != null && previous?.value != null ? change(hovered.value, previous.value) : null;

  return (
    <div ref={ref} className="relative w-full" style={{ height }}>
      {width > 0 && (
        <svg width={width} height={height} className="overflow-visible">
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor={color} stopOpacity={0.22} />
              <stop offset="1" stopColor={color} stopOpacity={0.02} />
            </linearGradient>
          </defs>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={pad.left} x2={width - pad.right} y1={y(t)} y2={y(t)} stroke="#eef0f4" />
              <text x={pad.left - 8} y={y(t)} dy="0.32em" textAnchor="end" className="fill-slate-400 text-[11px] tabular-nums">
                {format(t)}
              </text>
            </g>
          ))}
          {data.map((d, i) =>
            i % labelEvery === 0 || i === data.length - 1 ? (
              <text key={d.fullLabel} x={x(i)} y={height - 6} textAnchor="middle" className="fill-slate-400 text-[11px]">
                {d.label}
              </text>
            ) : null,
          )}
          {area && <path d={area} fill={`url(#${gradientId})`} />}
          {known.length > 0 && <path d={line} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" />}
          {known.map(([i, v]) => (
            <circle key={i} cx={x(i)} cy={y(v)} r={hover === i ? 5 : 3} fill="#fff" stroke={color} strokeWidth={2} />
          ))}
          {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={pad.top} y2={pad.top + innerH} stroke={color} strokeOpacity={0.25} strokeDasharray="3 3" />}
          <rect
            x={pad.left}
            y={pad.top}
            width={innerW}
            height={innerH}
            fill="transparent"
            onPointerMove={onMove}
            onPointerLeave={() => setHover(null)}
          />
        </svg>
      )}
      {hovered && hover !== null && (
        <div
          className="pointer-events-none absolute z-10 min-w-[128px] rounded-lg border border-line bg-white px-3 py-2 text-xs shadow-lg shadow-slate-900/10"
          style={{
            left: Math.min(Math.max(x(hover) - 64, 0), Math.max(width - 140, 0)),
            top: hovered.value !== null ? Math.max(y(hovered.value) - 72, 0) : pad.top,
          }}
        >
          <p className="text-muted">{hovered.fullLabel}</p>
          <p className="mt-0.5 text-sm font-semibold tabular-nums text-ink">{hovered.value !== null ? format(hovered.value) : "No data"}</p>
          {delta && <p className={`tabular-nums ${delta.startsWith("-") ? "text-rose-600" : "text-emerald-600"}`}>{delta}</p>}
        </div>
      )}
    </div>
  );
}

function niceTicks(min: number, max: number, count = 5): number[] {
  if (max === min) return max === 0 ? [0, 1] : [Math.min(0, min), max * 1.2 || 1];
  const rough = (max - min) / (count - 1);
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= rough) ?? rough;
  const start = Math.floor(min / step) * step;
  const ticks = [];
  for (let t = start; t <= max + step * 0.5; t += step) ticks.push(Number(t.toFixed(10)));
  if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + step);
  return ticks;
}

export interface DonutSegment {
  label: string;
  value: number;
  color: string;
}

/** Ring chart; an empty ring (track only) when nothing has been analysed yet. */
export function Donut({ segments, center, caption, size = 150 }: { segments: DonutSegment[]; center: string; caption: string; size?: number }) {
  const stroke = 18;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const total = segments.reduce((sum, s) => sum + s.value, 0);
  const gap = segments.filter((s) => s.value > 0).length > 1 ? 2 : 0;
  let offset = 0;
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={`${center} ${caption}`} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="#eef0f4" strokeWidth={stroke} />
        {total > 0 &&
          segments.map((segment) => {
            if (!segment.value) return null;
            const length = (segment.value / total) * circumference;
            const element = (
              <circle
                key={segment.label}
                cx={size / 2}
                cy={size / 2}
                r={radius}
                fill="none"
                stroke={segment.color}
                strokeWidth={stroke}
                strokeDasharray={`${Math.max(length - gap, 0)} ${circumference}`}
                strokeDashoffset={-offset}
              >
                <title>{`${segment.label}: ${segment.value}`}</title>
              </circle>
            );
            offset += length;
            return element;
          })}
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-2xl font-semibold tabular-nums text-ink">{center}</span>
        <span className="text-xs text-muted">{caption}</span>
      </div>
    </div>
  );
}
