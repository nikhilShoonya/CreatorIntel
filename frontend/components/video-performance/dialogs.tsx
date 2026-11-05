"use client";

import { useState, type FormEvent, type ReactNode } from "react";
import { AlertTriangle, CheckCircle2, Clock3, ExternalLink, FileSpreadsheet, Info, Link2, ListPlus, Loader2, Upload } from "lucide-react";

import { Button } from "@/components/ui/controls";
import { Dialog } from "@/components/ui/Dialog";
import { FileDropzone } from "@/components/ui/FileDropzone";
import { metaUploadProgress, useMetaQueue } from "@/components/ui/MetaQueueBanner";
import { PlatformIcon } from "@/components/ui/PlatformIcon";
import { useApi } from "@/hooks/useApi";
import { useDebounce } from "@/hooks/useDebounce";
import { formatCompact, formatDateTime, formatNumber, formatPercent, platformLabel, safeHref } from "@/lib/format";
import { vpApi } from "@/lib/vpApi";
import type { VpLinkPreview, VpUpload, VpUploadResult, VpVideo } from "@/types/videoPerformance";
import { BatchProblems, BatchSummary, batchRefreshMs } from "./BulkUploadStatus";
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
      <Field label="Instagram username (optional with Apify Reels)">
        <input className={input} value={value} onChange={(e) => onChange(e.target.value)} placeholder="@username" maxLength={100} />
      </Field>
      <p className="mt-1.5 flex gap-1.5 text-xs text-muted">
        <Info size={13} className="mt-px shrink-0" />
        Direct Reel links need no username when Apify is configured. Meta-based tracking uses the posting
        Professional account&apos;s username when the owner cannot be resolved automatically.
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
const UPLOAD_TABS = [
  { id: "file", label: "Excel / CSV file", Icon: FileSpreadsheet, description: "Excel or CSV with Creator Name, Platform (optional) and Video Link." },
  { id: "single", label: "Single video link", Icon: Link2, description: "Track one YouTube video, Instagram reel, or public Facebook video or reel." },
  { id: "links", label: "Multiple video links", Icon: ListPlus, description: "Paste many links at once. They are uploaded together as one batch in Upload History." },
] as const;

export function UploadVideosDialog({
  open,
  onClose,
  onDone,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  onDone: () => void;
  /** Shows the confirmation of a video added with the single link option. */
  onSaved: (message: string) => void;
}) {
  const [tab, setTab] = useState<(typeof UPLOAD_TABS)[number]["id"]>("file");
  return (
    <Dialog open={open} title="Upload videos" description={UPLOAD_TABS.find((t) => t.id === tab)?.description} onClose={onClose} size="lg">
      {open && (
        <div className="space-y-4">
          <div className="flex flex-wrap gap-1 rounded-lg border border-line bg-white p-1" role="tablist" aria-label="Upload type">
            {UPLOAD_TABS.map(({ id, label, Icon }) => (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={tab === id}
                onClick={() => setTab(id)}
                className={`inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                  tab === id ? "bg-accent-soft text-accent" : "text-slate-600 hover:text-ink"
                }`}
              >
                <Icon size={15} aria-hidden="true" />
                {label}
              </button>
            ))}
          </div>
          {/* All stay mounted, so a chosen file or pasted links survive a look at another tab. */}
          <div hidden={tab !== "file"}>
            <UploadForm onClose={onClose} onDone={onDone} />
          </div>
          <div hidden={tab !== "single"}>
            <AddVideoForm onClose={onClose} onSaved={onSaved} />
          </div>
          <div hidden={tab !== "links"}>
            <BulkLinksForm onClose={onClose} onDone={onDone} />
          </div>
        </div>
      )}
    </Dialog>
  );
}

function LinksPreview({ preview }: { preview: VpLinkPreview }) {
  return (
    <div aria-live="polite" className="space-y-2 rounded-lg border border-line bg-slate-50 px-3 py-2.5 text-sm text-slate-600">
      <p>
        <span className="font-semibold text-ink">{preview.detected}</span> link{preview.detected === 1 ? "" : "s"} detected
        {" · "}
        <span className="font-medium text-emerald-700">{preview.valid} ready to upload</span>
        {preview.duplicates > 0 && ` · ${preview.duplicates} duplicate${preview.duplicates === 1 ? "" : "s"} skipped`}
        {preview.invalid > 0 && (
          <>
            {" · "}
            <span className="font-medium text-rose-700">{preview.invalid} invalid</span>
          </>
        )}
      </p>
      {Object.keys(preview.platforms).length > 0 && (
        <p className="flex flex-wrap items-center gap-x-4 gap-y-1">
          {Object.entries(preview.platforms).map(([platform, count]) => (
            <span key={platform} className="inline-flex items-center gap-1.5">
              <PlatformIcon platform={platform} size={15} />
              {platformLabel(platform)} <span className="font-medium tabular-nums text-ink">{count}</span>
            </span>
          ))}
        </p>
      )}
      {preview.invalid_links.length > 0 && (
        <details>
          <summary className="cursor-pointer font-medium text-rose-700">Show the invalid links</summary>
          <ul className="mt-2 max-h-40 space-y-1.5 overflow-y-auto text-xs">
            {preview.invalid_links.map((item, index) => (
              <li key={`${index}:${item.link}`}>
                <span className="break-all font-medium text-slate-700">{item.link}</span>
                <span className="block text-muted">{item.message}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function BulkLinksForm({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [text, setText] = useState("");
  const [name, setName] = useState("");
  const [uploaded, setUploaded] = useState<VpUploadResult | null>(null);
  const { busy, error, run } = useSubmit();
  const pasted = text.trim();
  // The server's parser is the only one: this count is what the upload will really do with the text.
  const debounced = useDebounce(pasted, 300);
  const preview = useApi(debounced ? `vp-links-preview:${debounced}` : null, () => vpApi.previewLinks(debounced), { keepPrevious: true });
  const upToDate = debounced === pasted && !preview.loading;
  const ready = upToDate && !preview.error && (preview.data?.valid ?? 0) > 0;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!ready) return;
    run(async () => {
      setUploaded(await vpApi.uploadLinks(pasted, name.trim() || undefined));
      onDone();
    });
  }

  if (uploaded) return <LinkBatchProgress result={uploaded} onClose={onClose} />;

  return (
    <form onSubmit={submit} className="space-y-4">
      <Field label="Video links" hint="One per line, or separated by spaces, tabs or commas. You can paste a column or table straight from Excel.">
        <textarea
          className="mt-1.5 h-44 w-full resize-y rounded-lg border border-line px-3 py-2 font-mono text-xs leading-5 outline-none placeholder:text-slate-400 focus:border-accent focus:ring-2 focus:ring-indigo-100"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={"https://www.instagram.com/reel/…\nhttps://www.youtube.com/watch?v=…\nhttps://www.facebook.com/reel/…"}
          spellCheck={false}
        />
      </Field>
      {pasted && preview.error && <ErrorText error={preview.error} />}
      {pasted && !preview.error && preview.data && <LinksPreview preview={preview.data} />}
      <Field label="Upload name" hint="Optional - shown in Upload History. Left empty, it is named after the date and time.">
        <input className={input} value={name} onChange={(e) => setName(e.target.value)} maxLength={200} />
      </Field>
      <ErrorText error={error} />
      <div className="flex justify-end gap-2 pb-2">
        <Button onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" disabled={busy || !ready}>
          {busy ? <Loader2 size={15} className="animate-spin" /> : <Upload size={15} />}
          {busy ? "Uploading & checking links…" : ready ? `Upload ${preview.data?.valid} video${preview.data?.valid === 1 ? "" : "s"}` : "Upload"}
        </Button>
      </div>
    </form>
  );
}

/** Live status of a batch just uploaded: the work itself runs on the server, so this window can be closed. */
function LinkBatchProgress({ result, onClose }: { result: VpUploadResult; onClose: () => void }) {
  const id = result.upload.id;
  const { data, error } = useApi(`vp-link-batch:${id}`, () => vpApi.uploadRows(id), {
    refreshMs: (d: { upload: VpUpload } | undefined) => (d ? batchRefreshMs(d.upload) : 3000),
  });
  const { data: metaQueue } = useMetaQueue();
  const upload = data?.upload ?? result.upload;
  const waiting = metaUploadProgress(upload, metaQueue);
  return (
    <div className="space-y-4 pb-2">
      <p className="text-sm font-medium text-ink">{upload.filename}</p>
      <BatchSummary upload={upload} />
      {waiting && (
        <p className="flex items-center gap-1.5 text-sm text-amber-700">
          <Clock3 size={14} className="shrink-0" /> {waiting}
        </p>
      )}
      {error && <ErrorText error={error} />}
      {data && <BatchProblems rows={data.rows} />}
      <p className="text-xs text-muted">
        {upload.videos_in_progress > 0
          ? "You can close this window. Processing continues on the server, and this batch's status stays up to date in Upload History."
          : "This batch's videos and insights are in Upload History."}
      </p>
      <div className="flex justify-end">
        <Button variant="primary" onClick={onClose}>
          Done
        </Button>
      </div>
    </div>
  );
}

const ROW_LABEL: Record<string, string> = {
  added: "Added",
  already_tracked: "Already tracked",
  duplicate: "Duplicate",
  invalid: "Invalid",
};

function UploadForm({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
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
    const tone = upload.added + upload.already_tracked === 0 ? "failure" : upload.invalid > 0 ? "partial" : "success";
    return (
      <div className="space-y-4 pb-2">
        <p
          role="status"
          className={`flex items-center gap-2 rounded-lg border px-4 py-3 text-sm font-medium ${
            tone === "success"
              ? "border-emerald-200 bg-emerald-50 text-emerald-800"
              : tone === "partial"
                ? "border-amber-200 bg-amber-50 text-amber-800"
                : "border-rose-200 bg-rose-50 text-rose-700"
          }`}
        >
          {tone === "success" ? <CheckCircle2 size={17} /> : <AlertTriangle size={17} />}
          {tone === "success"
            ? `${upload.filename} uploaded`
            : tone === "partial"
              ? `${upload.filename} uploaded - ${upload.invalid} row${upload.invalid === 1 ? "" : "s"} could not be used`
              : "No video in this file could be tracked"}
        </p>
        <div className="grid grid-cols-2 divide-line rounded-lg border border-line text-center sm:grid-cols-4 sm:divide-x">
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
        <p className="text-xs text-muted">
          New videos and existing videos recovered with missing Instagram owner metadata are queued for fetching.
          Check the tracking table for retrieval results. Other already-tracked videos keep their stored data.
        </p>
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
      <FileDropzone
        file={file}
        onChange={setFile}
        disabled={busy}
        hint={
          <>
            Columns: <strong>Video Link</strong> (required), Creator Name, Platform (optional - detected from the link), and for
            an optional <strong>Username</strong> column for Meta tracking. With Apify enabled, direct Instagram Reel links need no username.
            For a bare shortcode, set Platform to Instagram or use an Instagram Reel URL header. Facebook share links
            (facebook.com/share/… and fb.watch/…) are converted to the real video link automatically; metrics are only
            available for public Facebook videos. Duplicate and already-tracked videos are not fetched again.
          </>
        }
      />
      <ErrorText error={error} />
      <div className="flex justify-end gap-2 pb-2">
        <Button onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" disabled={busy || !file}>
          {busy ? <Loader2 size={15} className="animate-spin" /> : <Upload size={15} />}
          {busy ? "Uploading & checking links…" : "Upload"}
        </Button>
      </div>
    </form>
  );
}

// --------------------------------------------------------------- history
const FACEBOOK_METRICS_SOURCE = "apify_facebook_metrics";

/** The Facebook counts beyond views, likes and comments. A rounded public figure never looks exact. */
function FacebookMetrics({ video }: { video: VpVideo }) {
  const value = (amount: number | null, group: "content" | "profile", metric: string) => {
    if (amount === null) return <span className="text-slate-400">N/A</span>;
    const precision = video.metric_status?.[group]?.[metric]?.precision;
    return `${precision === "compact_rounded" ? "≈" : ""}${formatNumber(amount)}`;
  };
  const items: [string, ReactNode][] = [
    ["Plays", value(video.plays, "content", "plays")],
    ["Reactions", value(video.reactions, "content", "reactions")],
    ["Shares", value(video.shares, "content", "shares")],
    ["Followers", value(video.profile_followers, "profile", "profileFollowers")],
    ["Page likes", value(video.profile_page_likes, "profile", "profilePageLikes")],
    ["Type", video.content_type ?? <span className="text-slate-400">N/A</span>],
  ];
  return (
    <dl className="grid grid-cols-2 divide-line rounded-lg border border-line text-center sm:grid-cols-3 lg:grid-cols-6 lg:divide-x">
      {items.map(([label, shown]) => (
        <div key={label} className="px-3 py-2">
          <dt className="text-xs text-muted">{label}</dt>
          <dd className="font-semibold capitalize tabular-nums text-ink">{shown}</dd>
        </div>
      ))}
    </dl>
  );
}

export function HistoryDialog({ videoId, onClose }: { videoId: number | null; onClose: () => void }) {
  const { data, error } = useApi(videoId === null ? null : `vp-history:${videoId}`, () => vpApi.history(videoId as number));
  const video = data?.video;
  const href = safeHref(video?.video_url);
  const facebookColumns = video?.platform === "facebook" && Boolean(data?.snapshots.some((snap) => snap.plays !== null || snap.reactions !== null || snap.shares !== null));
  return (
    <Dialog open={videoId !== null} title="View history" description={video?.display_title} onClose={onClose} size={facebookColumns ? "xl" : "lg"}>
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
          {video.metrics_source === FACEBOOK_METRICS_SOURCE && <FacebookMetrics video={video} />}
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
                    {facebookColumns && <th className="px-4 py-2.5 text-right">Plays</th>}
                    <th className="px-4 py-2.5 text-right">Likes</th>
                    {facebookColumns && <th className="px-4 py-2.5 text-right">Reactions</th>}
                    <th className="px-4 py-2.5 text-right">Comments</th>
                    {facebookColumns && <th className="px-4 py-2.5 text-right">Shares</th>}
                    <th className="px-4 py-2.5 text-right">Engagement</th>
                  </tr>
                </thead>
                <tbody>
                  {data.snapshots.map((snap) =>
                    snap.outdated ? (
                      <tr
                        key={snap.captured_at}
                        className="border-t border-line bg-slate-50/60 text-slate-400"
                        title="The platform's API briefly returned an older, cached count right after a newer one. It is not used for any number."
                      >
                        <td className="px-4 py-2 whitespace-nowrap">{formatDateTime(snap.captured_at)}</td>
                        <td className="px-4 py-2 text-right tabular-nums line-through">{formatNumber(snap.views)}</td>
                        <td colSpan={facebookColumns ? 7 : 4} className="px-4 py-2 text-right text-xs">Older cached count from the API - ignored</td>
                      </tr>
                    ) : (
                    <tr key={snap.captured_at} className="border-t border-line">
                      <td className="px-4 py-2 whitespace-nowrap">{formatDateTime(snap.captured_at)}</td>
                      <td className="px-4 py-2 text-right tabular-nums font-medium">
                        {snap.views_approximate && "≈"}
                        {formatNumber(snap.views)}
                      </td>
                      <td className={`px-4 py-2 text-right tabular-nums ${snap.views_change && snap.views_change > 0 ? "text-emerald-700" : "text-muted"}`}>
                        {snap.views_change === null ? "-" : formatSigned(snap.views_change)}
                      </td>
                      {facebookColumns && <td className="px-4 py-2 text-right tabular-nums">{formatNumber(snap.plays)}</td>}
                      <td className="px-4 py-2 text-right tabular-nums">{formatNumber(snap.likes)}</td>
                      {facebookColumns && <td className="px-4 py-2 text-right tabular-nums">{formatNumber(snap.reactions)}</td>}
                      <td className="px-4 py-2 text-right tabular-nums">{formatNumber(snap.comments)}</td>
                      {facebookColumns && <td className="px-4 py-2 text-right tabular-nums">{formatNumber(snap.shares)}</td>}
                      <td className="px-4 py-2 text-right tabular-nums">{formatPercent(snap.engagement_rate)}</td>
                    </tr>
                    ),
                  )}
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
