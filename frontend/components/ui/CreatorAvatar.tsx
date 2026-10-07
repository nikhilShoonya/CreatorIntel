"use client";

import { useState } from "react";

import { PlatformIcon } from "@/components/ui/PlatformIcon";

/** Platform-tinted gradients used for the fallback (initials) avatar and profile covers. */
export const PLATFORM_GRADIENTS: Record<string, string> = {
  youtube: "linear-gradient(135deg, #ff4d6d 0%, #ff0033 55%, #c9184a 100%)",
  instagram: "linear-gradient(135deg, #f9ce34 0%, #ee2a7b 50%, #6228d7 100%)",
};
const NEUTRAL_GRADIENT = "linear-gradient(135deg, #94a3b8 0%, #64748b 100%)";

export function platformGradient(platform: string): string {
  return PLATFORM_GRADIENTS[platform] ?? NEUTRAL_GRADIENT;
}

function initials(name: string): string {
  const words = name
    .replace(/[^\p{L}\p{N}\s]/gu, " ")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (words.length === 0) return "?";
  return (words[0][0] + (words.length > 1 ? words[words.length - 1][0] : "")).toUpperCase();
}

interface Props {
  name: string;
  platform: string;
  src: string | null | undefined;
  size?: number;
  /** Show the small platform logo in the bottom-right corner. */
  badge?: boolean;
  className?: string;
}

/**
 * Creator profile picture with graceful fallback to initials.
 * Platform CDNs (yt3.ggpht.com, Instagram CDN) can expire or block hotlinking, so a failed
 * load always falls back instead of showing a broken image.
 */
export function CreatorAvatar({ name, platform, src, size = 40, badge = false, className = "" }: Props) {
  // Keyed by URL so a new src automatically starts fresh (no reset effect needed).
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  const [loadedSrc, setLoadedSrc] = useState<string | null>(null);
  const failed = Boolean(src) && failedSrc === src;
  const loaded = Boolean(src) && loadedSrc === src;

  const showImage = Boolean(src) && !failed;
  const badgeSize = Math.max(14, Math.round(size * 0.32));

  return (
    <span className={`relative inline-flex shrink-0 ${className}`} style={{ width: size, height: size }}>
      <span
        className="flex h-full w-full items-center justify-center overflow-hidden rounded-full font-semibold text-white"
        style={{ background: platformGradient(platform), fontSize: Math.round(size * 0.36) }}
        aria-hidden={showImage ? undefined : true}
      >
        {(!showImage || !loaded) && <span className="select-none">{initials(name)}</span>}
        {showImage && (
          // eslint-disable-next-line @next/next/no-img-element -- external, expiring CDN URLs; next/image adds no value here
          <img
            src={src ?? undefined}
            alt={`${name} profile picture`}
            width={size}
            height={size}
            loading="lazy"
            decoding="async"
            referrerPolicy="no-referrer"
            onLoad={() => setLoadedSrc(src ?? null)}
            onError={() => setFailedSrc(src ?? null)}
            className={`absolute inset-0 h-full w-full rounded-full object-cover transition-opacity duration-300 ${
              loaded ? "opacity-100" : "opacity-0"
            }`}
          />
        )}
      </span>
      {badge && (platform === "youtube" || platform === "instagram") && (
        <span
          className="absolute -bottom-0.5 -right-0.5 flex items-center justify-center rounded-full bg-white shadow-sm ring-1 ring-slate-200"
          style={{ width: badgeSize + 6, height: badgeSize + 6 }}
        >
          <PlatformIcon platform={platform} size={badgeSize} />
        </span>
      )}
    </span>
  );
}
