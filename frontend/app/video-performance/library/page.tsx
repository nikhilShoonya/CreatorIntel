"use client";

import { Suspense, useRef, useState, type ComponentProps } from "react";
import { useSearchParams } from "next/navigation";
import { FilePlus, FileSpreadsheet, ListVideo, Upload, UserPlus, Users } from "lucide-react";

import { Button, Card } from "@/components/ui/controls";
import { CreatorsPanel } from "@/components/video-performance/CreatorsPanel";
import { UploadsPanel } from "@/components/video-performance/UploadsPanel";
import { AddCreatorDialog, AddVideoDialog, UploadVideosDialog } from "@/components/video-performance/dialogs";
import { sortFromParam, statusFromParam, VideoTable } from "@/components/video-performance/VideoTable";

type Tab = "videos" | "creators" | "uploads";
type Toast = { tone: "success" | "error"; message: string } | null;

export default function TrackingLibraryPage() {
  const [tab, setTab] = useState<Tab>("videos");
  const [refreshToken, setRefreshToken] = useState(0);
  const [dialog, setDialog] = useState<"upload" | "video" | "creator" | null>(null);
  const [toast, setToast] = useState<Toast>(null);
  const toastTimer = useRef<number | undefined>(undefined);

  function showToast(tone: "success" | "error", message: string) {
    window.clearTimeout(toastTimer.current);
    setToast({ tone, message });
    toastTimer.current = window.setTimeout(() => setToast(null), 4000);
  }

  const bump = () => setRefreshToken((n) => n + 1);
  const saved = (message: string) => {
    showToast("success", message);
    bump();
  };

  return (
    <div className="mx-auto max-w-[1600px] space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-accent">Video Performance</p>
          <h1 className="text-xl font-semibold tracking-tight text-ink">Tracking Library</h1>
          <p className="text-sm text-muted">Videos are checked automatically every day - no need to upload the list again.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => setDialog("upload")}>
            <Upload size={16} /> Upload Excel
          </Button>
          <Button onClick={() => setDialog("creator")}>
            <UserPlus size={16} /> Track creator
          </Button>
          <Button variant="primary" onClick={() => setDialog("video")}>
            <FilePlus size={16} /> Add video
          </Button>
        </div>
      </div>

      <Card className="overflow-hidden">
        <div className="flex gap-1 px-5 pt-4" role="tablist">
          {(
            [
              { id: "videos", label: "Tracked videos", Icon: ListVideo },
              { id: "creators", label: "Tracked creators", Icon: Users },
              { id: "uploads", label: "Uploads", Icon: FileSpreadsheet },
            ] as const
          ).map(({ id, label, Icon }) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={tab === id}
              onClick={() => setTab(id)}
              className={`inline-flex items-center gap-2 rounded-t-lg border-b-2 px-3 pb-3 pt-1 text-sm font-medium ${
                tab === id ? "border-accent text-accent" : "border-transparent text-slate-500 hover:text-ink"
              }`}
            >
              <Icon size={16} />
              {label}
            </button>
          ))}
        </div>
        <div className="border-t border-line pt-4">
          {tab === "videos" && (
            <Suspense fallback={<div className="mx-5 mb-5 h-64 animate-pulse rounded-lg bg-slate-50" />}>
              <VideoTableFromUrl refreshToken={refreshToken} onToast={showToast} onChanged={bump} />
            </Suspense>
          )}
          {tab === "creators" && <CreatorsPanel refreshToken={refreshToken} onToast={showToast} onChanged={bump} />}
          {tab === "uploads" && <UploadsPanel refreshToken={refreshToken} onToast={showToast} onChanged={bump} />}
        </div>
      </Card>

      <UploadVideosDialog open={dialog === "upload"} onClose={() => setDialog(null)} onDone={bump} />
      <AddVideoDialog open={dialog === "video"} onClose={() => setDialog(null)} onSaved={saved} />
      <AddCreatorDialog
        open={dialog === "creator"}
        onClose={() => setDialog(null)}
        onSaved={(message) => {
          saved(message);
          setTab("creators");
        }}
      />

      {toast && (
        <div
          role="status"
          className={`fixed bottom-6 right-6 z-[60] max-w-sm rounded-lg px-4 py-3 text-sm shadow-lg ${
            toast.tone === "success" ? "bg-slate-900 text-white" : "bg-rose-600 text-white"
          }`}
        >
          {toast.message}
        </div>
      )}
    </div>
  );
}

/** Opens sorted / filtered when linked from the dashboard (e.g. ?sort=engagement_rate, ?status=Video%20Down). */
function VideoTableFromUrl(props: Omit<ComponentProps<typeof VideoTable>, "initialSort" | "initialStatus">) {
  const params = useSearchParams();
  const sort = sortFromParam(params.get("sort"));
  const status = statusFromParam(params.get("status"));
  return <VideoTable key={`${sort ?? ""}:${status ?? ""}`} initialSort={sort} initialStatus={status} {...props} />;
}
