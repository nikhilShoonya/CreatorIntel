"use client";

import { useState, type ReactNode } from "react";
import { Clapperboard, Lightbulb, Table2 } from "lucide-react";

import { Dialog } from "@/components/ui/Dialog";

type Tab = "analytics" | "video" | "tips";

const TABS: { id: Tab; label: string; Icon: typeof Table2 }[] = [
  { id: "analytics", label: "Creator Analytics", Icon: Table2 },
  { id: "video", label: "Video Performance", Icon: Clapperboard },
  { id: "tips", label: "Good to know", Icon: Lightbulb },
];

function Steps({ items }: { items: { title: string; body: ReactNode }[] }) {
  return (
    <ol className="space-y-4">
      {items.map((item, index) => (
        <li key={item.title} className="flex gap-3">
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent-soft text-xs font-semibold text-accent">
            {index + 1}
          </span>
          <div>
            <p className="text-sm font-medium text-ink">{item.title}</p>
            <div className="mt-0.5 text-sm leading-relaxed text-slate-600">{item.body}</div>
          </div>
        </li>
      ))}
    </ol>
  );
}

function Tips({ items }: { items: { title: string; body: ReactNode }[] }) {
  return (
    <ul className="space-y-3">
      {items.map((item) => (
        <li key={item.title} className="rounded-lg border border-line px-4 py-3">
          <p className="text-sm font-medium text-ink">{item.title}</p>
          <div className="mt-0.5 text-sm leading-relaxed text-slate-600">{item.body}</div>
        </li>
      ))}
    </ul>
  );
}

const ANALYTICS = [
  {
    title: "Upload your creator list",
    body: (
      <>
        Go to <b>Upload &amp; Analyze</b> and choose an Excel or CSV file with two columns: <b>Channel Name</b> and{" "}
        <b>Channel Link</b> (YouTube or Instagram profile links).
      </>
    ),
  },
  {
    title: "Wait for the analysis",
    body: "Each creator is checked one by one. The status card shows the progress and the table fills in as results arrive.",
  },
  {
    title: "Read the results",
    body: "The table shows followers/subscribers, average views, top video, engagement rate, genre, language and sentiment. Click a creator's name to see all details.",
  },
  {
    title: "Search, filter and export",
    body: "Use the search box, filters and column sorting to find creators. Use Export to download the table as Excel or CSV.",
  },
  {
    title: "Manage creators",
    body: (
      <>
        Use <b>Add creator</b> to add one without a file. The <b>⋮</b> menu on each row lets you retry, re-analyze, edit or
        delete. <b>Creator List</b> shows everyone analysed so far and <b>History</b> shows past uploads.
      </>
    ),
  },
];

const VIDEO = [
  {
    title: "Add videos to track",
    body: (
      <>
        In <b>Video Performance → Tracking Library</b>, upload a file with a <b>Video Link</b> column (plus Creator Name if you
        like), or use <b>Add video</b> for a single link.
      </>
    ),
  },
  {
    title: "Views are checked every day automatically",
    body: "You don't need to upload the file again. Each day the latest views, likes and comments are saved, and the table shows views gained and growth since the day before.",
  },
  {
    title: "Track a creator for new videos",
    body: (
      <>
        Use <b>Track creator</b> with a YouTube channel or Instagram profile link. New videos they publish are added and tracked
        automatically.
      </>
    ),
  },
  {
    title: "Manage tracking",
    body: "From each row you can open the video, view its history, refresh now, stop or start tracking, retry, edit or delete. Select several rows for bulk actions.",
  },
  {
    title: "See the overview",
    body: (
      <>
        <b>Video Performance → Dashboard</b> shows totals, views gained today, newly detected videos, content sentiment and the
        best-performing videos.
      </>
    ),
  },
];

const TIPS = [
  {
    title: "What the statuses mean",
    body: (
      <>
        <b>Completed</b> - all data found. <b>Partial</b> - some values are not available from the platform. <b>Failed</b> - the
        check did not work (hover the badge to see why); use Retry. <b>Unsupported</b> - the platform does not allow this data.
      </>
    ),
  },
  {
    title: "N/A means not available",
    body: "Numbers are never guessed. If a platform does not provide a value (for example hidden likes), it is shown as N/A.",
  },
  {
    title: "Instagram accounts",
    body: "Instagram data is available for Business and Creator accounts. For a single reel, also give the owner's Instagram username.",
  },
  {
    title: "Genre, language and sentiment",
    body: "These are worked out by AI from the creator's own titles, captions and bio. Use Re-analyze if you want to run it again.",
  },
  {
    title: "Check the connections",
    body: (
      <>
        <b>Settings</b> shows whether YouTube, Instagram and AI analysis are connected.
      </>
    ),
  },
];

export function HelpDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [tab, setTab] = useState<Tab>("analytics");
  return (
    <Dialog open={open} title="How to use CreatorIntel" description="A quick guide to the two tools in this app." onClose={onClose} size="lg">
      <div className="pb-3">
        <div className="mb-5 flex gap-1 border-b border-line" role="tablist">
          {TABS.map(({ id, label, Icon }) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={tab === id}
              onClick={() => setTab(id)}
              className={`-mb-px inline-flex items-center gap-2 border-b-2 px-3 pb-2.5 text-sm font-medium ${
                tab === id ? "border-accent text-accent" : "border-transparent text-slate-500 hover:text-ink"
              }`}
            >
              <Icon size={15} />
              {label}
            </button>
          ))}
        </div>
        {tab === "analytics" && <Steps items={ANALYTICS} />}
        {tab === "video" && <Steps items={VIDEO} />}
        {tab === "tips" && <Tips items={TIPS} />}
      </div>
    </Dialog>
  );
}
