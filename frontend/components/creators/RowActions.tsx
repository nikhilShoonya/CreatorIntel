"use client";

import { Eye, ExternalLink, MoreVertical, Pencil, RefreshCw, Sparkles, Trash2 } from "lucide-react";

import { Menu } from "@/components/ui/Menu";
import { safeHref } from "@/lib/format";
import type { Creator } from "@/types";

export interface CreatorActions {
  onView: (creator: Creator) => void;
  onRetry: (creator: Creator) => void;
  onReanalyze: (creator: Creator) => void;
  onEdit: (creator: Creator) => void;
  onDelete: (creator: Creator) => void;
}

export function isBusy(creator: Pick<Creator, "status">) {
  return creator.status === "Pending" || creator.status === "Processing";
}

export function RowActions({ creator, actions }: { creator: Creator; actions: CreatorActions }) {
  const supported = creator.platform === "youtube" || creator.platform === "instagram";
  const href = supported ? safeHref(creator.channel_url) : undefined;
  const busy = isBusy(creator);
  const needsRetry = creator.status === "Failed" || creator.status === "Partial";

  return (
    <div className="flex items-center justify-end gap-1">
      <button
        type="button"
        onClick={() => actions.onView(creator)}
        className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100 hover:text-ink"
        aria-label={`View details for ${creator.channel_name}`}
        title="View details"
      >
        <Eye size={17} />
      </button>
      <Menu
        label={`Actions for ${creator.channel_name}`}
        width={200}
        trigger={(props) => (
          <button
            type="button"
            {...props}
            className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100 hover:text-ink"
            aria-label={`More actions for ${creator.channel_name}`}
          >
            <MoreVertical size={17} />
          </button>
        )}
        items={[
          { label: "View Details", icon: <Eye size={15} />, onSelect: () => actions.onView(creator) },
          {
            label: "Open Channel",
            icon: <ExternalLink size={15} />,
            disabled: !href,
            onSelect: () => href && window.open(href, "_blank", "noopener,noreferrer"),
          },
          {
            label: "Re-analyze",
            icon: <Sparkles size={15} />,
            disabled: !supported || busy,
            hint: "Run the AI genre / language / sentiment analysis again",
            onSelect: () => actions.onReanalyze(creator),
          },
          {
            label: needsRetry ? "Retry" : "Refresh Data",
            icon: <RefreshCw size={15} />,
            disabled: !supported || busy,
            hint: supported ? "Fetch fresh platform data for this creator only" : "Use Edit to fix the link first",
            onSelect: () => actions.onRetry(creator),
          },
          { label: "Edit", icon: <Pencil size={15} />, onSelect: () => actions.onEdit(creator) },
          { label: "Delete", icon: <Trash2 size={15} />, danger: true, onSelect: () => actions.onDelete(creator) },
        ]}
      />
    </div>
  );
}
