"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  ChartNoAxesColumnIncreasing,
  ChevronDown,
  Clapperboard,
  History,
  LayoutDashboard,
  LibraryBig,
  LifeBuoy,
  Settings,
  Table2,
  Upload,
} from "lucide-react";

import { HelpDialog } from "./HelpDialog";

const NAV = [
  { href: "/", label: "Upload & Analyze", Icon: Upload },
  { href: "/creators", label: "Creator List", Icon: Table2 },
  { href: "/history", label: "History", Icon: History },
];

const SETTINGS = { href: "/settings", label: "Settings", Icon: Settings };

// Independent module: Video Performance
const VIDEO_PERFORMANCE = [
  { href: "/video-performance", label: "Dashboard", Icon: LayoutDashboard },
  { href: "/video-performance/library", label: "Tracking Library", Icon: LibraryBig },
];

function isActive(pathname: string, href: string) {
  if (href === "/" || href === "/video-performance") return pathname === href;
  return pathname.startsWith(href);
}

const itemClass = (active: boolean) =>
  `flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors ${
    active ? "bg-accent-soft text-accent" : "text-slate-600 hover:bg-slate-50 hover:text-ink"
  }`;

function VideoPerformanceGroup({ pathname }: { pathname: string }) {
  const inModule = pathname.startsWith("/video-performance");
  const [expanded, setExpanded] = useState<boolean | null>(null); // null = follow the current route
  const open = expanded ?? inModule;
  return (
    <div>
      <button
        type="button"
        onClick={() => setExpanded(!open)}
        aria-expanded={open}
        className={`${itemClass(false)} w-full ${inModule ? "text-ink" : ""}`}
      >
        <Clapperboard size={18} aria-hidden="true" />
        <span className="flex-1 text-left">Video Performance</span>
        <ChevronDown size={16} className={`text-slate-400 transition-transform ${open ? "rotate-180" : ""}`} aria-hidden="true" />
      </button>
      {open && (
        <div className="mt-1 space-y-1 border-l border-line pl-2 ml-5">
          {VIDEO_PERFORMANCE.map(({ href, label, Icon }) => {
            const active = isActive(pathname, href);
            return (
              <Link key={href} href={href} prefetch={true} aria-current={active ? "page" : undefined} className={itemClass(active)}>
                <Icon size={16} aria-hidden="true" />
                {label}
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function Sidebar() {
  const pathname = usePathname();
  const [helpOpen, setHelpOpen] = useState(false);
  return (
    <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r border-line bg-white lg:flex">
      <Link href="/" className="flex items-center gap-3 px-6 pb-6 pt-6">
        <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent text-white">
          <ChartNoAxesColumnIncreasing size={20} strokeWidth={2.5} aria-hidden="true" />
        </span>
        <span>
          <span className="block text-[17px] font-semibold leading-tight tracking-tight text-ink">CreatorIntel</span>
          <span className="block text-xs text-muted">Channel Insights</span>
        </span>
      </Link>

      <nav className="flex-1 space-y-1 px-3" aria-label="Main">
        {NAV.map(({ href, label, Icon }) => {
          const active = isActive(pathname, href);
          return (
            <Link key={href} href={href} prefetch={true} aria-current={active ? "page" : undefined} className={itemClass(active)}>
              <Icon size={18} aria-hidden="true" />
              {label}
            </Link>
          );
        })}
        <VideoPerformanceGroup pathname={pathname} />
        <Link
          href={SETTINGS.href}
          prefetch={true}
          aria-current={isActive(pathname, SETTINGS.href) ? "page" : undefined}
          className={itemClass(isActive(pathname, SETTINGS.href))}
        >
          <SETTINGS.Icon size={18} aria-hidden="true" />
          {SETTINGS.label}
        </Link>
      </nav>

      <button
        type="button"
        onClick={() => setHelpOpen(true)}
        className="m-3 rounded-xl border border-line p-4 text-left transition-colors hover:border-indigo-200 hover:bg-accent-soft/50"
      >
        <span className="flex items-start gap-2.5">
          <LifeBuoy size={18} className="mt-0.5 text-accent" aria-hidden="true" />
          <span>
            <span className="block text-sm font-medium text-ink">Need help?</span>
            <span className="mt-0.5 block text-xs leading-relaxed text-muted">Learn how to use CreatorIntel step by step.</span>
          </span>
        </span>
      </button>
      <HelpDialog open={helpOpen} onClose={() => setHelpOpen(false)} />
    </aside>
  );
}

export function MobileNav() {
  const pathname = usePathname();
  return (
    <nav className="flex gap-1 overflow-x-auto border-b border-line bg-white px-3 py-2 lg:hidden" aria-label="Main">
      {[...NAV, ...VIDEO_PERFORMANCE.map((item) => ({ ...item, label: `Video ${item.label}` })), SETTINGS].map(({ href, label, Icon }) => {
        const active = isActive(pathname, href);
        return (
          <Link
            key={href}
            href={href}
            prefetch={true}
            className={`flex shrink-0 items-center gap-2 rounded-md px-3 py-1.5 text-sm ${
              active ? "bg-accent-soft text-accent" : "text-slate-600"
            }`}
          >
            <Icon size={16} aria-hidden="true" />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
