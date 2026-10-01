"use client";

import { useState, type FormEvent, type ReactNode } from "react";
import { AlertTriangle, CheckCircle2, Cpu, Database, SlidersHorizontal, XCircle } from "lucide-react";

import { TokenStatusCard, YouTubeQuotaCard } from "@/components/layout/TokenHealth";
import { USER_NAME_KEY } from "@/components/layout/Topbar";
import { Button, Card, ErrorBanner } from "@/components/ui/controls";
import { PlatformIcon } from "@/components/ui/PlatformIcon";
import { useConfigStatus } from "@/hooks/useData";
import { useStoredValue } from "@/hooks/useStoredValue";
import { API_URL } from "@/lib/api";

function Integration({ icon, name, configured, detail, envVar }: { icon: ReactNode; name: string; configured: boolean; detail: string; envVar: string }) {
  return (
    <div className="flex items-start gap-4 px-5 py-4">
      <div className="mt-0.5">{icon}</div>
      <div className="min-w-0 flex-1">
        <p className="font-medium text-ink">{name}</p>
        <p className="text-sm text-muted">{detail}</p>
        {!configured && (
          <p className="mt-1 text-xs text-slate-500">
            Set <code className="rounded bg-slate-100 px-1 py-0.5">{envVar}</code> in <code className="rounded bg-slate-100 px-1 py-0.5">backend/.env</code> and restart the backend.
          </p>
        )}
      </div>
      {configured ? (
        <span className="inline-flex items-center gap-1.5 text-sm font-medium text-emerald-700">
          <CheckCircle2 size={16} /> Configured
        </span>
      ) : (
        <span className="inline-flex items-center gap-1.5 text-sm font-medium text-rose-600">
          <XCircle size={16} /> Not configured
        </span>
      )}
    </div>
  );
}

function ProfileForm() {
  const [name, setName] = useStoredValue(USER_NAME_KEY, "Team Member");
  const [draft, setDraft] = useState<string | null>(null);
  const value = draft ?? name;

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    setName(value.trim().slice(0, 60) || null);
    setDraft(null);
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-wrap items-end gap-3 px-5 py-4">
      <label className="flex-1">
        <span className="text-sm font-medium text-ink">Display name</span>
        <span className="block text-xs text-muted">Shown in the top bar on this browser only.</span>
        <input
          value={value}
          onChange={(event) => setDraft(event.target.value)}
          maxLength={60}
          className="mt-2 h-9 w-full max-w-sm rounded-lg border border-line px-3 text-sm outline-none focus:border-accent focus:ring-2 focus:ring-indigo-100"
        />
      </label>
      <Button type="submit" variant="primary" disabled={draft === null}>
        Save
      </Button>
    </form>
  );
}

export default function SettingsPage() {
  const { data: config, error, reload } = useConfigStatus();

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">Settings</h1>
        <p className="text-sm text-muted">Integration status and processing configuration. Secrets are managed only in the backend environment.</p>
      </div>

      {error && <ErrorBanner message={error} onRetry={reload} />}

      {config?.warnings.map((warning) => (
        <div key={warning} role="alert" className="flex gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <AlertTriangle size={18} className="mt-0.5 shrink-0" />
          <span>{warning}</span>
        </div>
      ))}

      <Card>
        <h2 className="border-b border-line px-5 py-3 text-sm font-semibold text-ink">Integrations</h2>
        {config ? (
          <div className="divide-y divide-line">
            <Integration
              icon={<PlatformIcon platform="youtube" size={22} />}
              name={`YouTube Data API v3${config.youtube_key_count > 1 ? ` (${config.youtube_key_count} keys)` : ""}`}
              configured={config.youtube_configured}
              detail="Channel statistics, recent uploads and video metrics."
              envVar="YOUTUBE_API_KEY"
            />
            {config.youtube_configured && <YouTubeQuotaCard />}
            <Integration
              icon={<PlatformIcon platform="instagram" size={22} />}
              name={`Instagram Graph API (${config.meta_api_version})`}
              configured={config.instagram_configured && config.warnings.length === 0}
              detail="Business Discovery for Instagram Professional (Business/Creator) accounts."
              envVar="META_ACCESS_TOKEN"
            />
            {config.instagram_configured && <TokenStatusCard />}
            <Integration
              icon={<Cpu size={22} className="text-slate-500" />}
              name={`AI content analysis - Groq (${config.ai_model})`}
              configured={config.ai_configured}
              detail="Genre, language and sentiment classification with structured output."
              envVar="GROQ_API_KEY"
            />
          </div>
        ) : (
          !error && <div className="h-40 animate-pulse" />
        )}
      </Card>

      {config && (
        <Card>
          <h2 className="flex items-center gap-2 border-b border-line px-5 py-3 text-sm font-semibold text-ink">
            <SlidersHorizontal size={15} /> Processing
          </h2>
          <dl className="grid grid-cols-1 gap-x-6 gap-y-3 px-5 py-4 text-sm sm:grid-cols-2">
            {[
              ["Average views sample", `Latest ${config.average_views_sample_size} videos / reels`],
              ["Recent content fetched", `${config.recent_content_fetch_limit} items per creator`],
              ["Cache reuse window", config.cache_ttl_hours ? `${config.cache_ttl_hours} hours` : "Disabled"],
              ["Max upload size", `${config.max_upload_size_mb} MB`],
            ].map(([label, value]) => (
              <div key={label}>
                <dt className="text-muted">{label}</dt>
                <dd className="font-medium text-ink">{value}</dd>
              </div>
            ))}
            <div>
              <dt className="text-muted">Database</dt>
              <dd className="flex items-center gap-1.5 font-medium text-ink">
                <Database size={14} /> {config.database}
              </dd>
            </div>
            <div>
              <dt className="text-muted">Backend</dt>
              <dd className="truncate font-medium text-ink">{API_URL}</dd>
            </div>
          </dl>
        </Card>
      )}

      <Card>
        <h2 className="border-b border-line px-5 py-3 text-sm font-semibold text-ink">Profile</h2>
        <ProfileForm />
      </Card>
    </div>
  );
}
