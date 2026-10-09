import { CircleSlash, Link2Off } from "lucide-react";

import type { Platform } from "@/types";

function InstagramIcon({ size }: { size: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <defs>
        <linearGradient id="ig-grad" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0" stopColor="#f9ce34" />
          <stop offset="0.5" stopColor="#ee2a7b" />
          <stop offset="1" stopColor="#6228d7" />
        </linearGradient>
      </defs>
      <rect x="2" y="2" width="20" height="20" rx="6" fill="url(#ig-grad)" />
      <rect x="6.5" y="6.5" width="11" height="11" rx="5.5" fill="none" stroke="#fff" strokeWidth="1.8" />
      <circle cx="17.2" cy="6.8" r="1.1" fill="#fff" />
    </svg>
  );
}

function YouTubeIcon({ size }: { size: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <rect x="1.5" y="5" width="21" height="14" rx="4" fill="#ff0033" />
      <path d="M10 9.2v5.6l4.8-2.8z" fill="#fff" />
    </svg>
  );
}

function FacebookIcon({ size }: { size: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="11" fill="#1877F2" />
      <path d="M13.4 19.5v-6h2l.3-2.4h-2.3V9.6c0-.7.2-1.2 1.2-1.2h1.2V6.3c-.2 0-.9-.1-1.8-.1-1.8 0-3 1.1-3 3.1v1.8H9v2.4h2v6z" fill="#fff" />
    </svg>
  );
}

export function PlatformIcon({ platform, size = 18 }: { platform: Platform | string; size?: number }) {
  if (platform === "instagram") return <InstagramIcon size={size} />;
  if (platform === "youtube") return <YouTubeIcon size={size} />;
  if (platform === "facebook") return <FacebookIcon size={size} />;
  if (platform === "unsupported") return <CircleSlash size={size - 2} className="text-slate-400" aria-hidden="true" />;
  return <Link2Off size={size - 2} className="text-slate-400" aria-hidden="true" />;
}
