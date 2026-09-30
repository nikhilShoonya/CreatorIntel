"use client";

import { useEffect, useRef, useState } from "react";
import { FileSpreadsheet } from "lucide-react";

import { CreatorsTable } from "@/components/creators/CreatorsTable";
import { EmptyState } from "@/components/ui/controls";
import { ProcessingList } from "@/components/upload/ProcessingList";
import { UploadCard, validateClientFile } from "@/components/upload/UploadCard";
import { UploadStatusCard } from "@/components/upload/UploadStatusCard";
import { useUpload } from "@/hooks/useData";
import { useStoredValue } from "@/hooks/useStoredValue";
import { api } from "@/lib/api";

const LAST_UPLOAD_KEY = "creatorintel:last-upload";

export default function UploadAnalyzePage() {
  const [lastUploadId, setLastUploadId] = useStoredValue(LAST_UPLOAD_KEY, "");
  const [validating, setValidating] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  const uploadSectionRef = useRef<HTMLDivElement>(null);

  const { data: upload, error: uploadError, reload: reloadUpload } = useUpload(lastUploadId || null);
  const [retrying, setRetrying] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const processing = upload?.status === "processing";

  // The remembered upload was deleted (e.g. from History): fall back to the empty state.
  useEffect(() => {
    if (uploadError === "Upload not found") setLastUploadId(null);
  }, [uploadError, setLastUploadId]);

  async function handleFile(file: File) {
    const clientError = validateClientFile(file);
    if (clientError) {
      setValidationError(clientError);
      return;
    }
    setValidationError(null);
    setValidating(true);
    try {
      const created = await api.uploadFile(file);
      setLastUploadId(created.id);
    } catch (error) {
      setValidationError(error instanceof Error ? error.message : "Upload failed");
    } finally {
      setValidating(false);
    }
  }

  async function retryFailed() {
    if (!lastUploadId) return;
    setRetrying(true);
    setActionError(null);
    try {
      await api.retryFailed(lastUploadId);
      reloadUpload();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Retry failed");
    } finally {
      setRetrying(false);
    }
  }

  // Refetch the table when processing starts/ends; while processing it polls on its own.
  const refreshToken = processing ? 0 : 1;

  return (
    <div className="mx-auto max-w-[1600px] space-y-5">
      <div ref={uploadSectionRef} className="flex flex-col gap-5 lg:flex-row">
        <UploadCard onFile={handleFile} busy={validating} />
        <UploadStatusCard
          upload={upload}
          validating={validating}
          validationError={validationError}
          onReupload={() => uploadSectionRef.current?.querySelector<HTMLInputElement>("input[type=file]")?.click()}
          onRetryFailed={retryFailed}
          retrying={retrying}
          actionError={actionError}
        />
      </div>

      {processing && upload && <ProcessingList upload={upload} />}

      {lastUploadId ? (
        <CreatorsTable
          key={lastUploadId}
          title="Creator Channels"
          uploadId={lastUploadId}
          refreshToken={refreshToken}
          live={processing}
          onChanged={reloadUpload}
        />
      ) : (
        <section className="rounded-xl border border-line bg-white">
          <EmptyState
            icon={<FileSpreadsheet size={22} />}
            title="Upload your creator list"
            description="Upload an Excel or CSV file containing Channel Name and Channel Link. Each creator is enriched with YouTube or Instagram data and AI content analysis."
          />
        </section>
      )}
    </div>
  );
}
