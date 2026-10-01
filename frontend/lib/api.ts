import type {
  ConfigStatus,
  CreatorDetail,
  CreatorFilters,
  CreatorListResponse,
  Facets,
  InstagramTokenStatus,
  YouTubeQuota,
  UploadDetail,
  UploadRows,
  UploadSummary,
} from "@/types";

// 127.0.0.1 rather than "localhost": on Windows "localhost" tries IPv6 first and adds ~200 ms per request.
export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export function toQuery(params: Record<string, string | number | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, { cache: "no-store", ...init });
  } catch {
    throw new ApiError(`Cannot reach the backend at ${API_URL}. Is it running?`, 0);
  }
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") message = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(message, response.status);
  }
  return response.json() as Promise<T>;
}

export function jsonInit(method: string, body?: unknown): RequestInit {
  return {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  };
}

/** Download a file from the backend and save it with the server-provided filename. */
export async function downloadFile(path: string, fallbackName: string): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`);
  } catch {
    throw new ApiError(`Cannot reach the backend at ${API_URL}. Is it running?`, 0);
  }
  if (!response.ok) throw new ApiError(`Download failed (${response.status})`, response.status);
  const blob = await response.blob();
  const match = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") ?? "");
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = match?.[1] ?? fallbackName;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export interface DeleteResult {
  deleted: number;
  message: string;
}

export const api = {
  uploadFile(file: File): Promise<UploadDetail> {
    const body = new FormData();
    body.append("file", file);
    return request<UploadDetail>("/api/uploads", { method: "POST", body });
  },
  getUpload: (id: string) => request<UploadDetail>(`/api/uploads/${encodeURIComponent(id)}`),
  retryFailed: (id: string) =>
    request<UploadDetail>(`/api/uploads/${encodeURIComponent(id)}/retry-failed`, { method: "POST" }),
  listUploads: () => request<UploadSummary[]>("/api/uploads"),
  listCreators: (filters: CreatorFilters, page: number, pageSize: number) =>
    request<CreatorListResponse>(`/api/creators${toQuery({ ...filters, page, page_size: pageSize })}`),
  facets: (uploadId?: string) => request<Facets>(`/api/creators/facets${toQuery({ upload_id: uploadId })}`),
  getCreator: (id: number) => request<CreatorDetail>(`/api/creators/${id}`),
  retry: (id: number) => request<{ message: string }>(`/api/creators/${id}/retry`, { method: "POST" }),
  reanalyze: (id: number) => request<{ message: string }>(`/api/creators/${id}/reanalyze`, { method: "POST" }),
  configStatus: () => request<ConfigStatus>("/api/config/status"),

  // CRUD
  createCreator: (input: { channel_name: string; channel_link: string; upload_id?: string }) =>
    request<CreatorDetail>("/api/creators", jsonInit("POST", input)),
  updateCreator: (id: number, input: { channel_name?: string; channel_link?: string }) =>
    request<CreatorDetail>(`/api/creators/${id}`, jsonInit("PATCH", input)),
  deleteCreator: (id: number) => request<DeleteResult>(`/api/creators/${id}`, jsonInit("DELETE")),
  bulkDeleteCreators: (ids: number[]) => request<DeleteResult>("/api/creators/bulk-delete", jsonInit("POST", { ids })),
  deleteUpload: (id: string, deleteCreators: boolean) =>
    request<DeleteResult>(
      `/api/uploads/${encodeURIComponent(id)}?delete_creators=${deleteCreators ? "true" : "false"}`,
      jsonInit("DELETE"),
    ),

  download(kind: "excel" | "csv", filters: CreatorFilters): Promise<void> {
    const { sort_by, sort_dir, ...rest } = filters;
    return downloadFile(`/api/exports/${kind}${toQuery({ ...rest, sort_by, sort_dir })}`, `creatorintel_export.${kind === "excel" ? "xlsx" : "csv"}`);
  },
  uploadRows: (id: string) => request<UploadRows>(`/api/uploads/${encodeURIComponent(id)}/rows`),
  downloadUploadRows: (id: string, format: "excel" | "csv") =>
    downloadFile(`/api/uploads/${encodeURIComponent(id)}/rows/export?format=${format}`, `upload_rows.${format === "excel" ? "xlsx" : "csv"}`),
  youtubeQuota: () => request<YouTubeQuota>("/api/config/youtube-quota"),
  instagramToken: (refresh = false) =>
    request<InstagramTokenStatus>(`/api/config/instagram-token${refresh ? "?refresh=true" : ""}`),
};
