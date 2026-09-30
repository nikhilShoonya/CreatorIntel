"use client";

import { api } from "@/lib/api";
import type { CreatorDetail, CreatorFilters, CreatorListResponse, CreatorStatus, UploadDetail, UploadSummary } from "@/types";
import { useApi } from "./useApi";

const ACTIVE: CreatorStatus[] = ["Pending", "Processing"];
const isActive = (status: CreatorStatus) => ACTIVE.includes(status);

/** Polls while any visible row is still being processed (or when `live` is set). */
export function useCreators(filters: CreatorFilters, page: number, pageSize: number, refreshToken: number, live = false) {
  const key = JSON.stringify({ filters, page, pageSize, refreshToken });
  return useApi(key, () => api.listCreators(filters, page, pageSize), {
    keepPrevious: true,
    refreshMs: (data: CreatorListResponse | undefined) =>
      live || data?.items.some((c) => isActive(c.status)) ? 3000 : false,
  });
}

export function useFacets(uploadId: string | undefined, refreshToken: number) {
  return useApi(`facets:${uploadId ?? "all"}:${refreshToken}`, () => api.facets(uploadId), { keepPrevious: true });
}

/** Polls every 1.5 s while the upload is still processing. */
export function useUpload(uploadId: string | null) {
  return useApi(uploadId ? `upload:${uploadId}` : null, () => api.getUpload(uploadId as string), {
    refreshMs: (data: UploadDetail | undefined) =>
      data?.status === "processing" || data?.items.some((item) => isActive(item.status)) ? 2000 : false,
  });
}

export function useUploads() {
  return useApi("uploads", () => api.listUploads(), {
    keepPrevious: true,
    refreshMs: (data: UploadSummary[] | undefined) => (data?.some((u) => u.status === "processing") ? 4000 : false),
  });
}

export function useConfigStatus() {
  return useApi("config", () => api.configStatus());
}

export function useCreatorDetail(creatorId: number | null) {
  return useApi(creatorId === null ? null : `creator:${creatorId}`, () => api.getCreator(creatorId as number), {
    refreshMs: (data: CreatorDetail | undefined) => (data && isActive(data.status) ? 2500 : false),
  });
}
