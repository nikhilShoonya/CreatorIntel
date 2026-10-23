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
  Users,
  type LucideIcon,
} from "lucide-react";

import { HelpDialog } from "./HelpDialog";

type NavItem = { href: string; label: string; Icon: LucideIcon };

// (creator uploads are on the Creator List - "Upload Creators")
const HISTORY: NavItem = { href: "/history", label: "History", Icon: History };
const SETTINGS: NavItem = { href: "/settings", label: "Settings", Icon: Settings };

// Creator Analytics: dashboard + the creator list (the list keeps its /creators address)
const CREATOR_ANALYTICS: NavItem[] = [
  { href: "/creator-analytics", label: "Dashboard", Icon: LayoutDashboard },
  { href: "/creators", label: "Creator List", Icon: Table2 },
];

// Independent module: Video Performance
const VIDEO_PERFORMANCE: NavItem[] = [
  { href: "/video-performance", label: "Dashboard", Icon: LayoutDashboard },
  { href: "/video-performance/library", label: "Tracking Library", Icon: LibraryBig },
];

// Dashboards are exact matches so they are not also active on the pages below them.
const EXACT = new Set(["/", "/video-performance", "/creator-analytics"]);

function isActive(pathname: string, href: string) {
  if (EXACT.has(href)) return pathname === href;
  return pathname.startsWith(href);
}

const itemClass = (active: boolean) =>
  `flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors ${
    active ? "bg-accent-soft text-accent" : "text-slate-600 hover:bg-slate-50 hover:text-ink"
  }`;

function NavLink({ item, pathname, size = 18 }: { item: NavItem; pathname: string; size?: number }) {
  const active = isActive(pathname, item.href);
  return (
    <Link href={item.href} prefetch={true} aria-current={active ? "page" : undefined} className={itemClass(active)}>
      <item.Icon size={size} aria-hidden="true" />
      {item.label}
    </Link>
  );
}

/** Expandable section: open while one of its pages is shown, and toggled by clicking its title. */
function NavGroup({ label, Icon, items, pathname }: { label: string; Icon: LucideIcon; items: NavItem[]; pathname: string }) {
  const inModule = items.some((item) => isActive(pathname, item.href));
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
        <Icon size={18} aria-hidden="true" />
        <span className="flex-1 text-left">{label}</span>
        <ChevronDown size={16} className={`text-slate-400 transition-transform ${open ? "rotate-180" : ""}`} aria-hidden="true" />
      </button>
      {open && (
        <div className="mt-1 space-y-1 border-l border-line pl-2 ml-5">
          {items.map((item) => (
            <NavLink key={item.href} item={item} pathname={pathname} size={16} />
          ))}
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
      <Link href="/creator-analytics" className="flex items-center gap-3 px-6 pb-6 pt-6">
        <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent text-white">
          <ChartNoAxesColumnIncreasing size={20} strokeWidth={2.5} aria-hidden="true" />
        </span>
        <span>
          <span className="block text-[17px] font-semibold leading-tight tracking-tight text-ink">CreatorIntel</span>
          <span className="block text-xs text-muted">Channel Insights</span>
        </span>
      </Link>

      <nav className="flex-1 space-y-1 overflow-y-auto px-3" aria-label="Main">
        <NavGroup label="Creator Analytics" Icon={Users} items={CREATOR_ANALYTICS} pathname={pathname} />
        <NavGroup label="Video Performance" Icon={Clapperboard} items={VIDEO_PERFORMANCE} pathname={pathname} />
        <NavLink item={HISTORY} pathname={pathname} />
        <NavLink item={SETTINGS} pathname={pathname} />
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
  const items: NavItem[] = [
    ...CREATOR_ANALYTICS.map((item) => (item.label === "Dashboard" ? { ...item, label: "Creator Dashboard" } : item)),
    ...VIDEO_PERFORMANCE.map((item) => ({ ...item, label: `Video ${item.label}` })),
    HISTORY,
    SETTINGS,
  ];
  return (
    <nav className="flex gap-1 overflow-x-auto border-b border-line bg-white px-3 py-2 lg:hidden" aria-label="Main">
      {items.map(({ href, label, Icon }) => {
        const active = isActive(pathname, href);
        return (
          <Link
            key={href}
            href={href}
            prefetch={true}
            aria-current={active ? "page" : undefined}
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
