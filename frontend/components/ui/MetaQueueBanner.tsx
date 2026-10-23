"use client";

import { Clock3 } from "lucide-react";

import { useApi } from "@/hooks/useApi";
import { api } from "@/lib/api";
import type { MetaQueue } from "@/types";

/** "continues automatically after 13:05" (or "within a few minutes" when Meta calls are allowed again). */
export function metaResumeText(queue: MetaQueue | undefined): string {
  if (!queue?.resume_at) return "within a few minutes";
  return `after ${new Date(queue.resume_at).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}`;
}

/** The Meta queue status, refreshed every minute while something is waiting (shared by all users of it). */
export function useMetaQueue() {
  return useApi("meta-queue", () => api.metaQueue(), {
    keepPrevious: true,
    refreshMs: (d: MetaQueue | undefined) => (d && (d.waiting_videos > 0 || d.waiting_creators > 0) ? 60_000 : false),
  });
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** Shown while Instagram / Facebook work waits for Meta's hourly limit. */
export function MetaQueueBanner({ scope }: { scope: "videos" | "creators" }) {
  const { data } = useMetaQueue();
  if (!data) return null;
  const facebook = data.waiting_videos - data.waiting_instagram_videos;
  const parts =
    scope === "videos"
      ? [
          data.waiting_instagram_videos > 0 && plural(data.waiting_instagram_videos, "Instagram reel", "Instagram reels"),
          facebook > 0 && plural(facebook, "Facebook video", "Facebook videos"),
        ].filter(Boolean)
      : data.waiting_creators > 0
        ? [plural(data.waiting_creators, "Instagram creator", "Instagram creators")]
        : [];
  if (parts.length === 0) return null;
  const count = scope === "videos" ? data.waiting_videos : data.waiting_creators;
  return (
    <div role="status" className="flex items-start gap-2.5 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
      <Clock3 size={16} className="mt-0.5 shrink-0" />
      <p>
        <span className="font-semibold">
          {parts.join(" and ")} {count === 1 ? "is" : "are"} waiting for Meta&apos;s hourly limit
        </span>{" "}
        - {count === 1 ? "it continues" : "they continue"} automatically {metaResumeText(data)}, oldest first. Stored numbers
        stay visible meanwhile; nothing needs to be done.
        <span className="ml-1 text-amber-700/80">
          ({data.calls_last_hour} of {data.hourly_budget} Meta calls used in the last hour
          {data.reported_percent !== null ? `, Meta reports ${Math.round(data.reported_percent)}%` : ""})
        </span>
      </p>
    </div>
  );
}

/** "40 of 100 Instagram reels fetched, rest continue automatically after 13:05" for one uploaded file. */
export function metaUploadProgress(
  upload: { instagram_total: number; instagram_waiting: number; facebook_total: number; facebook_waiting: number },
  queue: MetaQueue | undefined,
): string | null {
  const total = upload.instagram_total + upload.facebook_total;
  const waiting = upload.instagram_waiting + upload.facebook_waiting;
  if (!waiting) return null;
  const label = upload.facebook_total === 0 ? "Instagram reels" : upload.instagram_total === 0 ? "Facebook videos" : "Instagram / Facebook videos";
  return `${total - waiting} of ${total} ${label} fetched, rest continue automatically ${metaResumeText(queue)}`;
}
