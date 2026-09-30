"use client";

import { useState, type FormEvent } from "react";
import { Info, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/controls";
import { Dialog } from "@/components/ui/Dialog";
import { api } from "@/lib/api";
import type { Creator } from "@/types";

interface Props {
  /** null = closed; "new" = add; a creator = edit */
  target: Creator | "new" | null;
  uploadId?: string;
  onClose: () => void;
  onSaved: (message: string) => void;
}

const inputClass =
  "mt-1.5 h-10 w-full rounded-lg border border-line px-3 text-sm outline-none placeholder:text-slate-400 focus:border-accent focus:ring-2 focus:ring-indigo-100";

export function CreatorFormDialog({ target, uploadId, onClose, onSaved }: Props) {
  const editing = target !== null && target !== "new" ? target : null;
  // Remount the form per target so its fields start from the right values.
  const formKey = target === null ? "closed" : editing ? `edit-${editing.id}` : "new";

  return (
    <Dialog
      open={target !== null}
      title={editing ? "Edit creator" : "Add creator"}
      description={
        editing
          ? "Rename the creator or correct the channel link."
          : uploadId
            ? "Add a creator to this upload. Data is fetched right away."
            : "Add a single creator without uploading a file. Data is fetched right away."
      }
      onClose={onClose}
    >
      <CreatorForm key={formKey} editing={editing} uploadId={uploadId} onClose={onClose} onSaved={onSaved} />
    </Dialog>
  );
}

function CreatorForm({
  editing,
  uploadId,
  onClose,
  onSaved,
}: {
  editing: Creator | null;
  uploadId?: string;
  onClose: () => void;
  onSaved: (message: string) => void;
}) {
  const supported = editing ? editing.platform === "youtube" || editing.platform === "instagram" : true;
  const [name, setName] = useState(editing?.channel_name ?? "");
  const [link, setLink] = useState(editing?.channel_url ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const linkChanged = editing !== null && link.trim() !== editing.channel_url;

  async function submit(event: FormEvent) {
    event.preventDefault();
    const channelName = name.trim();
    const channelLink = link.trim();
    if (!channelName || !channelLink) {
      setError("Channel name and channel link are required.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (editing) {
        const changes: { channel_name?: string; channel_link?: string } = {};
        if (channelName !== editing.channel_name) changes.channel_name = channelName;
        if (channelLink !== editing.channel_url) changes.channel_link = channelLink;
        if (Object.keys(changes).length === 0) {
          onClose();
          return;
        }
        await api.updateCreator(editing.id, changes);
        onSaved(changes.channel_link ? "Link updated - fetching fresh data" : "Creator updated");
      } else {
        await api.createCreator({ channel_name: channelName, channel_link: channelLink, upload_id: uploadId });
        onSaved("Creator added - fetching data");
      }
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the creator");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4 pb-2">
      <label className="block">
        <span className="text-sm font-medium text-ink">Channel name</span>
        <input value={name} onChange={(e) => setName(e.target.value)} maxLength={300} placeholder="e.g. Trading Tech" className={inputClass} />
      </label>
      <label className="block">
        <span className="text-sm font-medium text-ink">Channel link</span>
        <input
          value={link}
          onChange={(e) => setLink(e.target.value)}
          maxLength={2048}
          placeholder="https://www.instagram.com/username/ or https://www.youtube.com/@handle"
          className={inputClass}
        />
      </label>

      {editing && (linkChanged || !supported) && (
        <p className="flex gap-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
          <Info size={15} className="mt-px shrink-0" />
          Changing the link clears this creator&apos;s current data and fetches it again for the new channel.
        </p>
      )}
      {error && <p className="rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</p>}

      <div className="flex justify-end gap-2 pt-1">
        <Button onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" disabled={busy}>
          {busy && <Loader2 size={15} className="animate-spin" />}
          {editing ? "Save changes" : "Add creator"}
        </Button>
      </div>
    </form>
  );
}
