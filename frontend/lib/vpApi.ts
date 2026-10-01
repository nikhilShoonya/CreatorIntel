import { API_URL, ApiError, downloadFile, jsonInit, request, toQuery } from "@/lib/api";
import type {
  VpCreator,
  VpDashboard,
  VpFilters,
  VpHistory,
  VpJob,
  VpUpload,
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
  jobs: () => request<VpJob[]>(`${BASE}/jobs`),
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

  creators: () => request<VpCreator[]>(`${BASE}/creators`),
  addCreator: (input: { channel_url: string; creator_name?: string; backfill?: number }) =>
    request<VpCreator>(`${BASE}/creators`, jsonInit("POST", input)),
  updateCreator: (id: number, input: { creator_name?: string; enabled?: boolean }) =>
    request<VpCreator>(`${BASE}/creators/${id}`, jsonInit("PUT", input)),
  deleteCreator: (id: number, deleteVideos: boolean) =>
    request<ActionResult>(`${BASE}/creators/${id}?delete_videos=${deleteVideos ? "true" : "false"}`, jsonInit("DELETE")),
  discover: (id: number) => request<ActionResult>(`${BASE}/creators/${id}/discover`, jsonInit("POST")),

  uploads: () => request<VpUpload[]>(`${BASE}/uploads`),
  uploadRows: (id: string) => request<{ upload: VpUpload; rows: VpUploadRow[] }>(`${BASE}/uploads/${encodeURIComponent(id)}/rows`),
  downloadUploadRows: (id: string, format: "excel" | "csv") =>
    downloadFile(`${BASE}/uploads/${encodeURIComponent(id)}/rows/export?format=${format}`, `video_rows.${format === "excel" ? "xlsx" : "csv"}`),
  exportVideos: (kind: "excel" | "csv", filters: VpFilters) =>
    downloadFile(`${BASE}/exports/${kind}${toQuery({ ...filters })}`, `video_performance.${kind === "excel" ? "xlsx" : "csv"}`),

  async upload(file: File): Promise<VpUploadResult> {
    const body = new FormData();
    body.append("file", file);
    return request<VpUploadResult>(`${BASE}/uploads`, { method: "POST", body });
  },
};

export { API_URL, ApiError };
