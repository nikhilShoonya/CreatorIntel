"use client";

import { useState } from "react";
import { MoreVertical, Pencil, Power, PowerOff, Radar, Trash2, Users } from "lucide-react";

import { EmptyState, ErrorBanner } from "@/components/ui/controls";
import { ConfirmDialog } from "@/components/ui/Dialog";
import { Menu } from "@/components/ui/Menu";
import { PlatformIcon } from "@/components/ui/PlatformIcon";
import { useApi } from "@/hooks/useApi";
import { platformLabel, safeHref, formatDateTime } from "@/lib/format";
import { vpApi } from "@/lib/vpApi";
import type { VpCreator } from "@/types/videoPerformance";
import { CreatorStatusBadge } from "./badges";
import { EditCreatorDialog } from "./dialogs";
import { formatChecked } from "./format";

interface Props {
  refreshToken: number;
  onToast: (tone: "success" | "error", message: string) => void;
  onChanged: () => void;
}

export function CreatorsPanel({ refreshToken, onToast, onChanged }: Props) {
  const [local, setLocal] = useState(0);
  const [editing, setEditing] = useState<VpCreator | null>(null);
  const [deleting, setDeleting] = useState<VpCreator | null>(null);
  const [alsoVideos, setAlsoVideos] = useState(false);
  const { data, error, reload } = useApi(`vp-creators:${refreshToken + local}`, () => vpApi.creators(), {
    keepPrevious: true,
    refreshMs: (d: VpCreator[] | undefined) => (d?.some((c) => c.status === "Pending") ? 2500 : false),
  });

  async function act(action: () => Promise<{ message: string }>) {
    try {
      onToast("success", (await action()).message);
      setLocal((n) => n + 1);
      onChanged();
    } catch (err) {
      onToast("error", err instanceof Error ? err.message : "Action failed");
    }
  }

  function setEnabled(creator: VpCreator, enabled: boolean) {
    act(async () => {
      await vpApi.updateCreator(creator.id, { enabled });
      return { message: enabled ? "Creator tracking enabled" : "Creator tracking disabled" };
    });
  }

  const refresh = () => {
    setLocal((n) => n + 1);
    onChanged();
  };

  if (error) return <div className="px-5 pb-5"><ErrorBanner message={error} onRetry={reload} /></div>;
  if (data && data.length === 0)
    return (
      <EmptyState
        icon={<Users size={22} />}
        title="No creators tracked"
        description="Track a YouTube channel or Instagram Professional account and new videos are added automatically every day."
      />
    );

  return (
    <>
      <div className="table-scroll overflow-x-auto border-t border-line">
        <table className="w-full min-w-[1100px] text-sm">
          <thead className="bg-slate-50 text-left text-xs font-semibold text-slate-600">
            <tr>
              {["Creator", "Platform", "Channel", "Status", "Videos tracked", "Detected", "Tracking since", "Last check", ""].map((h) => (
                <th key={h} className="whitespace-nowrap border-b border-line px-4 py-3">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {!data
              ? Array.from({ length: 3 }, (_, i) => (
                  <tr key={i}>
                    <td colSpan={9} className="border-b border-line px-4 py-4">
                      <div className="h-4 animate-pulse rounded bg-slate-100" />
                    </td>
                  </tr>
                ))
              : data.map((creator) => {
                  const href = safeHref(creator.channel_url);
                  return (
                    <tr key={creator.id} className="hover:bg-slate-50">
                      <td className="border-b border-line px-4 py-3">
                        <p className="font-medium text-ink">{creator.creator_name}</p>
                        {creator.platform_name && creator.platform_name !== creator.creator_name && (
                          <p className="text-xs text-muted">{creator.platform_name}</p>
                        )}
                      </td>
                      <td className="border-b border-line px-4 py-3">
                        <span className="inline-flex items-center gap-2">
                          <PlatformIcon platform={creator.platform} />
                          {platformLabel(creator.platform)}
                        </span>
                      </td>
                      <td className="max-w-[240px] truncate border-b border-line px-4 py-3">
                        {href ? (
                          <a href={href} target="_blank" rel="noopener noreferrer" className="text-accent underline decoration-indigo-200 underline-offset-2">
                            {creator.channel_url.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "")}
                          </a>
                        ) : (
                          creator.channel_url
                        )}
                      </td>
                      <td className="border-b border-line px-4 py-3">
                        <CreatorStatusBadge status={creator.status} reason={creator.status_reason} />
                        {creator.status_reason && <p className="mt-1 max-w-[260px] text-xs text-muted">{creator.status_reason}</p>}
                      </td>
                      <td className="border-b border-line px-4 py-3 tabular-nums">{creator.video_count}</td>
                      <td className="border-b border-line px-4 py-3 tabular-nums">{creator.videos_discovered}</td>
                      <td className="whitespace-nowrap border-b border-line px-4 py-3 text-slate-600">{formatDateTime(creator.tracking_since)}</td>
                      <td className="whitespace-nowrap border-b border-line px-4 py-3 text-slate-600">{formatChecked(creator.last_discovery_at)}</td>
                      <td className="border-b border-line px-4 py-3 text-right">
                        <Menu
                          label={`Actions for ${creator.creator_name}`}
                          width={210}
                          trigger={(props) => (
                            <button type="button" {...props} className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100" aria-label={`Actions for ${creator.creator_name}`}>
                              <MoreVertical size={16} />
                            </button>
                          )}
                          items={[
                            {
                              label: "Check for new videos",
                              icon: <Radar size={15} />,
                              disabled: !creator.enabled || creator.status === "Unsupported",
                              onSelect: () => act(() => vpApi.discover(creator.id)),
                            },
                            creator.enabled
                              ? { label: "Disable tracking", icon: <PowerOff size={15} />, onSelect: () => setEnabled(creator, false) }
                              : { label: "Enable tracking", icon: <Power size={15} />, onSelect: () => setEnabled(creator, true) },
                            { label: "Edit", icon: <Pencil size={15} />, onSelect: () => setEditing(creator) },
                            {
                              label: "Delete",
                              icon: <Trash2 size={15} />,
                              danger: true,
                              onSelect: () => {
                                setAlsoVideos(false);
                                setDeleting(creator);
                              },
                            },
                          ]}
                        />
                      </td>
                    </tr>
                  );
                })}
          </tbody>
        </table>
      </div>

      <EditCreatorDialog
        creator={editing}
        onClose={() => setEditing(null)}
        onSaved={(message) => {
          onToast("success", message);
          refresh();
        }}
      />
      <ConfirmDialog
        open={deleting !== null}
        title="Delete creator tracking?"
        message={
          <>
            <span className="font-medium text-ink">{deleting?.creator_name}</span> will no longer be checked for new videos.
          </>
        }
        confirmLabel="Delete"
        onConfirm={async () => {
          if (!deleting) return;
          const result = await vpApi.deleteCreator(deleting.id, alsoVideos);
          onToast("success", result.message);
          refresh();
        }}
        onClose={() => setDeleting(null)}
      >
        <label className="mt-4 flex cursor-pointer items-start gap-2.5 rounded-lg border border-line p-3 text-sm">
          <input type="checkbox" checked={alsoVideos} onChange={(e) => setAlsoVideos(e.target.checked)} className="mt-0.5 h-4 w-4 accent-[var(--color-accent)]" />
          <span>
            <span className="font-medium text-ink">Also delete its tracked videos</span>
            <span className="block text-muted">Otherwise the videos keep being tracked on their own.</span>
          </span>
        </label>
      </ConfirmDialog>
    </>
  );
}
