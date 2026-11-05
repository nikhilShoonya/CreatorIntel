import { downloadFile, jsonInit, request, toQuery } from "@/lib/api";
import type {
  VpDashboard,
  VpFilters,
  VpHistory,
  VpJob,
  VpLinkPreview,
  VpUpload,
  VpUploadPlatformTotals,
  VpUploadResult,
  VpUploadRow,
  VpVideo,
  VpVideoList,
} from "@/types/videoPerformance";

const BASE = "/api/video-performance";

interface ActionResult {
  affected: number;
  message: string;
}

/** Client for the independent Video Performance module (/api/video-performance). */
export const vpApi = {
  dashboard: (days = 7) => request<VpDashboard>(`${BASE}/dashboard${toQuery({ days })}`),
  runJob: (job: VpJob["job_type"]) => request<ActionResult>(`${BASE}/jobs/${job}/run`, jsonInit("POST")),

  videos: (filters: VpFilters, page: number, pageSize: number) =>
    request<VpVideoList>(`${BASE}/videos${toQuery({ ...filters, page, page_size: pageSize })}`),
  facets: () => request<{ creators: string[]; statuses: string[] }>(`${BASE}/videos/facets`),
  history: (id: number) => request<VpHistory>(`${BASE}/history/${id}`),
  addVideo: (input: { video_url: string; creator_name?: string; instagram_username?: string }) =>
    request<VpVideo>(`${BASE}/videos`, jsonInit("POST", input)),
  updateVideo: (
    id: number,
    input: { creator_name?: string; video_url?: string; instagram_username?: string; tracking_status?: "tracking" | "paused" },
  ) => request<VpVideo>(`${BASE}/videos/${id}`, jsonInit("PUT", input)),
  deleteVideo: (id: number) => request<ActionResult>(`${BASE}/videos/${id}`, jsonInit("DELETE")),
  videoAction: (id: number, action: "refresh" | "retry" | "pause" | "resume") =>
    request<ActionResult>(`${BASE}/videos/${id}/${action}`, jsonInit("POST")),
  retryFailed: () => request<ActionResult>(`${BASE}/videos/retry-failed`, jsonInit("POST")),
  bulk: (ids: number[], action: "pause" | "resume" | "refresh" | "delete") =>
    request<ActionResult>(`${BASE}/videos/bulk`, jsonInit("POST", { ids, action })),

  uploads: () => request<VpUpload[]>(`${BASE}/uploads`),
  uploadRows: (id: string) => request<{ upload: VpUpload; rows: VpUploadRow[] }>(`${BASE}/uploads/${encodeURIComponent(id)}/rows`),
  uploadPlatformTotals: (id: string) => request<VpUploadPlatformTotals>(`${BASE}/uploads/${encodeURIComponent(id)}/platform-totals`),
  renameUpload: (id: string, filename: string) =>
    request<VpUpload>(`${BASE}/uploads/${encodeURIComponent(id)}`, jsonInit("PATCH", { filename })),
  deleteUpload: (id: string, deleteVideos: boolean) =>
    request<ActionResult>(`${BASE}/uploads/${encodeURIComponent(id)}?delete_videos=${deleteVideos ? "true" : "false"}`, jsonInit("DELETE")),
  refreshUpload: (id: string) => request<ActionResult>(`${BASE}/uploads/${encodeURIComponent(id)}/refresh`, jsonInit("POST")),
  downloadUploadFile: (upload: VpUpload) => downloadFile(`${BASE}/uploads/${encodeURIComponent(upload.id)}/file`, upload.filename),
  downloadUploadRows: (upload: VpUpload, kind: "excel" | "csv") =>
    downloadFile(
      `${BASE}/uploads/${encodeURIComponent(upload.id)}/rows/export${toQuery({ format: kind })}`,
      `${upload.filename.replace(/\.[^.]+$/, "")}_rows.${kind === "excel" ? "xlsx" : "csv"}`,
    ),
  exportVideos: (kind: "excel" | "csv", filters: VpFilters) =>
    downloadFile(`${BASE}/exports/${kind}${toQuery({ ...filters })}`, `video_performance.${kind === "excel" ? "xlsx" : "csv"}`),

  async upload(file: File): Promise<VpUploadResult> {
    const body = new FormData();
    body.append("file", file);
    return request<VpUploadResult>(`${BASE}/uploads`, { method: "POST", body });
  },
  previewLinks: (links: string) => request<VpLinkPreview>(`${BASE}/uploads/links/preview`, jsonInit("POST", { links })),
  uploadLinks: (links: string, name?: string) => request<VpUploadResult>(`${BASE}/uploads/links`, jsonInit("POST", { links, name })),
};
