"use client";

import { useRef, useState, type FormEvent, type ReactNode } from "react";
import { ExternalLink, FileSpreadsheet, Info, Loader2, Upload } from "lucide-react";

import { Button } from "@/components/ui/controls";
import { Dialog } from "@/components/ui/Dialog";
import { PlatformIcon } from "@/components/ui/PlatformIcon";
import { useApi } from "@/hooks/useApi";
import { formatCompact, formatDateTime, formatNumber, formatPercent, safeHref } from "@/lib/format";
import { vpApi } from "@/lib/vpApi";
import type { VpUploadResult, VpVideo } from "@/types/videoPerformance";
import { formatSigned } from "./format";

const input =
  "mt-1.5 h-10 w-full rounded-lg border border-line px-3 text-sm outline-none placeholder:text-slate-400 focus:border-accent focus:ring-2 focus:ring-indigo-100";

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="text-sm font-medium text-ink">{label}</span>
      {hint && <span className="block text-xs text-muted">{hint}</span>}
      {children}
    </label>
  );
}

function FormFooter({ busy, submitLabel, onCancel }: { busy: boolean; submitLabel: string; onCancel: () => void }) {
  return (
    <div className="flex justify-end gap-2 pb-2 pt-1">
      <Button onClick={onCancel} disabled={busy}>
        Cancel
      </Button>
      <Button type="submit" variant="primary" disabled={busy}>
        {busy && <Loader2 size={15} className="animate-spin" />}
        {submitLabel}
      </Button>
    </div>
  );
}

function ErrorText({ error }: { error: string | null }) {
  return error ? <p className="rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</p> : null;
}

/** Runs an async submit with busy/error state. */
function useSubmit() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }
  return { busy, error, setError, run };
}

// ------------------------------------------------------------- add video
export function AddVideoDialog({ open, onClose, onSaved }: { open: boolean; onClose: () => void; onSaved: (m: string) => void }) {
  return (
    <Dialog open={open} title="Add video" description="Track a single YouTube video, Instagram reel, or a video/reel on your Facebook Page. Views are checked daily." onClose={onClose}>
      {open && <AddVideoForm onClose={onClose} onSaved={onSaved} />}
    </Dialog>
  );
}

function AddVideoForm({ onClose, onSaved }: { onClose: () => void; onSaved: (m: string) => void }) {
  const [url, setUrl] = useState("");
  const [creator, setCreator] = useState("");
  const [username, setUsername] = useState("");
  const { busy, error, run } = useSubmit();
  const isInstagram = /instagr(am\.com|\.am)/i.test(url);

  function submit(event: FormEvent) {
    event.preventDefault();
    run(async () => {
      await vpApi.addVideo({
        video_url: url.trim(),
        creator_name: creator.trim() || undefined,
        instagram_username: isInstagram ? username.trim() || undefined : undefined,
      });
      onSaved("Video added - fetching views");
      onClose();
    });
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <Field label="Video link">
        <input className={input} value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://www.youtube.com/watch?v=…, https://www.instagram.com/reel/… or https://www.facebook.com/reel/…" required />
      </Field>
      <Field label="Creator name" hint="Optional - filled from the platform when available">
        <input className={input} value={creator} onChange={(e) => setCreator(e.target.value)} maxLength={300} />
      </Field>
      {isInstagram && <InstagramUsernameField value={username} onChange={setUsername} />}
      <ErrorText error={error} />
      <FormFooter busy={busy} submitLabel="Add video" onCancel={onClose} />
    </form>
  );
}

function InstagramUsernameField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div>
      <Field label="Instagram username of the reel owner">
        <input className={input} value={value} onChange={(e) => onChange(e.target.value)} placeholder="@username" maxLength={100} />
      </Field>
      <p className="mt-1.5 flex gap-1.5 text-xs text-muted">
        <Info size={13} className="mt-px shrink-0" />
        The official Instagram API finds a reel through its owner&apos;s Professional account, so the username is required
        unless the link already contains it (instagram.com/username/reel/…).
      </p>
    </div>
  );
}

// ------------------------------------------------------------ edit video
export function EditVideoDialog({ video, onClose, onSaved }: { video: VpVideo | null; onClose: () => void; onSaved: (m: string) => void }) {
  return (
    <Dialog open={video !== null} title="Edit tracked video" onClose={onClose}>
      {video && <EditVideoForm key={video.id} video={video} onClose={onClose} onSaved={onSaved} />}
    </Dialog>
  );
}

function EditVideoForm({ video, onClose, onSaved }: { video: VpVideo; onClose: () => void; onSaved: (m: string) => void }) {
  const [url, setUrl] = useState(video.video_url);
  const [creator, setCreator] = useState(video.creator_name ?? "");
  const [username, setUsername] = useState(video.owner_username ?? "");
  const [tracking, setTracking] = useState<"tracking" | "paused">(video.status === "Paused" ? "paused" : "tracking");
  const { busy, error, run } = useSubmit();
  const isInstagram = /instagr(am\.com|\.am)/i.test(url);

  function submit(event: FormEvent) {
    event.preventDefault();
    const changes: Parameters<typeof vpApi.updateVideo>[1] = {};
    if (creator.trim() !== (video.creator_name ?? "")) changes.creator_name = creator.trim();
    if (url.trim() !== video.video_url) changes.video_url = url.trim();
    if (isInstagram && username.trim().replace(/^@/, "") !== (video.owner_username ?? "")) changes.instagram_username = username.trim();
    if ((tracking === "paused") !== (video.status === "Paused")) changes.tracking_status = tracking;
    run(async () => {
      if (Object.keys(changes).length > 0) await vpApi.updateVideo(video.id, changes);
      onSaved(changes.video_url ? "Video link changed - history restarted" : "Video updated");
      onClose();
    });
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <Field label="Video link">
        <input className={input} value={url} onChange={(e) => setUrl(e.target.value)} required />
      </Field>
      {url.trim() !== video.video_url && (
        <p className="flex gap-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
          <Info size={15} className="mt-px shrink-0" />
          A different video starts a new history - the snapshots of the old link are removed.
        </p>
      )}
      <Field label="Creator name">
        <input className={input} value={creator} onChange={(e) => setCreator(e.target.value)} maxLength={300} />
      </Field>
      {isInstagram && <InstagramUsernameField value={username} onChange={setUsername} />}
      <Field label="Tracking status">
        <select className={input} value={tracking} onChange={(e) => setTracking(e.target.value as "tracking" | "paused")}>
          <option value="tracking">Tracking (checked daily)</option>
          <option value="paused">Paused</option>
        </select>
      </Field>
      <ErrorText error={error} />
      <FormFooter busy={busy} submitLabel="Save changes" onCancel={onClose} />
    </form>
  );
}

// ---------------------------------------------------------------- upload
export function UploadVideosDialog({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  return (
    <Dialog open={open} title="Upload video list" description="Excel or CSV with Creator Name, Platform (optional) and Video Link." onClose={onClose} size="lg">
      {open && <UploadForm onClose={onClose} onDone={onDone} />}
    </Dialog>
  );
}

const ROW_LABEL: Record<string, string> = {
  added: "Added",
  already_tracked: "Already tracked",
  duplicate: "Duplicate",
  invalid: "Invalid",
};

function UploadForm({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<VpUploadResult | null>(null);
  const { busy, error, run } = useSubmit();

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    run(async () => {
      setResult(await vpApi.upload(file));
      onDone();
    });
  }

  if (result) {
    const { upload } = result;
    const notAdded = result.rows.filter((r) => r.status !== "added" || r.message);
    return (
      <div className="space-y-4 pb-2">
        <div className="grid grid-cols-4 divide-x divide-line rounded-lg border border-line text-center">
          {[
            ["Added", upload.added, "text-emerald-700"],
            ["Already tracked", upload.already_tracked, "text-slate-700"],
            ["Duplicates", upload.duplicates, "text-slate-700"],
            ["Invalid", upload.invalid, upload.invalid ? "text-rose-700" : "text-slate-700"],
          ].map(([label, value, tone]) => (
            <div key={label as string} className="px-3 py-3">
              <p className="text-xs text-muted">{label}</p>
              <p className={`text-xl font-semibold tabular-nums ${tone}`}>{value as number}</p>
            </div>
          ))}
        </div>
        {notAdded.length > 0 && (
          <div className="max-h-64 overflow-y-auto rounded-lg border border-line">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-slate-50 text-left text-xs text-slate-600">
                <tr>
                  <th className="px-3 py-2">Row</th>
                  <th className="px-3 py-2">Creator</th>
                  <th className="px-3 py-2">Result</th>
                  <th className="px-3 py-2">Details</th>
                </tr>
              </thead>
              <tbody>
                {notAdded.map((row) => (
                  <tr key={row.row} className="border-t border-line">
                    <td className="px-3 py-2 tabular-nums text-muted">{row.row}</td>
                    <td className="px-3 py-2">{row.creator_name ?? "-"}</td>
                    <td className="px-3 py-2 whitespace-nowrap">{ROW_LABEL[row.status]}</td>
                    <td className="px-3 py-2 text-muted">{row.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="text-xs text-muted">Added videos are being fetched now and will then be checked every day.</p>
        <div className="flex justify-end">
          <Button variant="primary" onClick={onClose}>
            Done
          </Button>
        </div>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <button
        type="button"
        onClick={() => fileRef.current?.click()}
        className="flex w-full flex-col items-center gap-2 rounded-lg border border-dashed border-slate-300 px-4 py-8 text-sm text-slate-600 hover:border-accent hover:bg-accent-soft/40"
      >
        <FileSpreadsheet size={28} className="text-emerald-600" />
        {file ? <span className="font-medium text-ink">{file.name}</span> : <span>Choose a .xlsx, .xls or .csv file (max 10 MB)</span>}
      </button>
      <input ref={fileRef} type="file" accept=".xlsx,.xls,.csv" className="hidden" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
      <p className="text-xs text-muted">
        Columns: <strong>Video Link</strong> (required), Creator Name, Platform (optional - detected from the link), and for
        Instagram reels a <strong>Username</strong> column with the reel owner&apos;s handle. Facebook share links
        (facebook.com/share/… and fb.watch/…) are converted to the real video link automatically; metrics are only available
        for videos on your own Facebook Page. Duplicate videos are skipped.
      </p>
      <ErrorText error={error} />
      <div className="flex justify-end gap-2 pb-2">
        <Button onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" disabled={busy || !file}>
          {busy ? <Loader2 size={15} className="animate-spin" /> : <Upload size={15} />}
          Upload
        </Button>
      </div>
    </form>
  );
}

// --------------------------------------------------------------- history
export function HistoryDialog({ videoId, onClose }: { videoId: number | null; onClose: () => void }) {
  const { data, error } = useApi(videoId === null ? null : `vp-history:${videoId}`, () => vpApi.history(videoId as number));
  const video = data?.video;
  const href = safeHref(video?.video_url);
  return (
    <Dialog open={videoId !== null} title="View history" description={video?.display_title} onClose={onClose} size="lg">
      {error && <ErrorText error={error} />}
      {!data && !error && (
        <div className="flex justify-center py-10">
          <Loader2 className="animate-spin text-slate-400" />
        </div>
      )}
      {data && video && (
        <div className="space-y-4 pb-3">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-sm">
            <span className="inline-flex items-center gap-1.5">
              <PlatformIcon platform={video.platform} size={16} /> {video.creator_name ?? "Unknown creator"}
            </span>
            <span className="text-muted">
              Current <span className="font-semibold text-ink">{formatCompact(video.current_views)}</span> views
            </span>
            {href && (
              <a href={href} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-accent hover:underline">
                Open video <ExternalLink size={13} />
              </a>
            )}
          </div>
          {data.snapshots.length === 0 ? (
            <p className="rounded-lg bg-slate-50 px-4 py-6 text-center text-sm text-muted">No snapshots yet.</p>
          ) : (
            <div className="max-h-[50vh] overflow-y-auto rounded-lg border border-line">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-slate-50 text-left text-xs text-slate-600">
                  <tr>
                    <th className="px-4 py-2.5">Checked</th>
                    <th className="px-4 py-2.5 text-right">Views</th>
                    <th className="px-4 py-2.5 text-right">Change</th>
                    <th className="px-4 py-2.5 text-right">Likes</th>
                    <th className="px-4 py-2.5 text-right">Comments</th>
                    <th className="px-4 py-2.5 text-right">Engagement</th>
                  </tr>
                </thead>
                <tbody>
                  {data.snapshots.map((snap) => (
                    <tr key={snap.captured_at} className="border-t border-line">
                      <td className="px-4 py-2 whitespace-nowrap">{formatDateTime(snap.captured_at)}</td>
                      <td className="px-4 py-2 text-right tabular-nums font-medium">{formatNumber(snap.views)}</td>
                      <td className={`px-4 py-2 text-right tabular-nums ${snap.views_change && snap.views_change > 0 ? "text-emerald-700" : "text-muted"}`}>
                        {snap.views_change === null ? "-" : formatSigned(snap.views_change)}
                      </td>
                      <td className="px-4 py-2 text-right tabular-nums">{formatNumber(snap.likes)}</td>
                      <td className="px-4 py-2 text-right tabular-nums">{formatNumber(snap.comments)}</td>
                      <td className="px-4 py-2 text-right tabular-nums">{formatPercent(snap.engagement_rate)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {video.status_reason && <p className="text-xs text-muted">Note: {video.status_reason}</p>}
        </div>
      )}
    </Dialog>
  );
}
