"use client";

import { Suspense } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { X } from "lucide-react";

import { CreatorsTable } from "@/components/creators/CreatorsTable";
import { useUpload } from "@/hooks/useData";

function CreatorList() {
  const params = useSearchParams();
  const q = params.get("q") ?? "";
  const uploadParam = params.get("upload");
  const uploadId = uploadParam && /^[a-f0-9]{32}$/.test(uploadParam) ? uploadParam : undefined;
  const { data: upload } = useUpload(uploadId ?? null);

  return (
    <CreatorsTable
      key={`${q}|${uploadId ?? ""}`}
      title={uploadId ? "Creators in upload" : "All Creators"}
      initialQuery={q}
      uploadId={uploadId}
      live={upload?.status === "processing"}
      headerExtra={
        uploadId && (
          <Link
            href="/creators"
            className="inline-flex max-w-xs items-center gap-1.5 rounded-md bg-accent-soft px-2 py-1 text-xs font-medium text-accent hover:bg-indigo-100"
            title="Show all creators"
          >
            <span className="truncate">{upload?.filename ?? "Selected upload"}</span>
            <X size={13} />
          </Link>
        )
      }
    />
  );
}

export default function CreatorsPage() {
  return (
    <div className="mx-auto max-w-[1600px] space-y-5">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">Creator List</h1>
        <p className="text-sm text-muted">Every creator analysed so far. Search, filter, sort and export.</p>
      </div>
      <Suspense fallback={<div className="h-96 animate-pulse rounded-xl border border-line bg-white" />}>
        <CreatorList />
      </Suspense>
    </div>
  );
}
