"use client";

import { useState, type FormEvent, type ReactNode } from "react";
import { AlertTriangle, CheckCircle2, Cpu, Loader2, Sparkles, XCircle } from "lucide-react";

import { ApifyUsageCard, useApifyUsage } from "@/components/layout/ApifyUsageCard";
import { GroqUsageCard, InstagramUsageCard, tokenSummary, useInstagramToken, YouTubeQuotaCard } from "@/components/layout/TokenHealth";
import { USER_NAME_KEY } from "@/components/layout/Topbar";
import { Button, Card, ErrorBanner } from "@/components/ui/controls";
import { PlatformIcon } from "@/components/ui/PlatformIcon";
import { useConfigStatus } from "@/hooks/useData";
import { useStoredValue } from "@/hooks/useStoredValue";

type Tone = "ok" | "warn" | "off" | "checking";

const TONES: Record<Tone, { label: string; cls: string; Icon: typeof CheckCircle2 }> = {
  ok: { label: "Connected", cls: "bg-emerald-50 text-emerald-700 ring-emerald-200", Icon: CheckCircle2 },
  warn: { label: "Needs attention", cls: "bg-amber-50 text-amber-800 ring-amber-200", Icon: AlertTriangle },
  off: { label: "Not set up", cls: "bg-slate-100 text-slate-600 ring-slate-200", Icon: XCircle },
  checking: { label: "Checking…", cls: "bg-slate-50 text-slate-500 ring-slate-200", Icon: Loader2 },
};

// Usage columns on wide screens follow the number of connected services, so no row has an empty cell.
const USAGE_COLUMNS = ["xl:grid-cols-1", "xl:grid-cols-1", "xl:grid-cols-2", "xl:grid-cols-3", "xl:grid-cols-4"];

function Section({ title, aside, className = "", children }: { title: string; aside?: string; className?: string; children: ReactNode }) {
  return (
    <Card className={`overflow-hidden ${className}`}>
      <div className="flex items-baseline justify-between gap-3 border-b border-line px-5 py-3">
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
        {aside && <p className="text-xs text-muted">{aside}</p>}
      </div>
      {children}
    </Card>
  );
}

/** One service: icon, name, status pill and one short line (plus where to configure it when it is not set up). */
function Connection({ icon, name, tone, detail, envVar }: { icon: ReactNode; name: string; tone: Tone; detail: string; envVar?: string }) {
  const { label, cls, Icon } = TONES[tone];
  return (
    <div className="flex flex-col items-start gap-2 rounded-lg border border-line px-4 py-3">
      <span className="flex items-center gap-2.5">
        <span className="shrink-0">{icon}</span>
        <span className="font-medium text-ink">{name}</span>
      </span>
      <span className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset ${cls}`}>
        <Icon size={12} className={tone === "checking" ? "animate-spin" : undefined} aria-hidden="true" />
        {label}
      </span>
      <p className="text-xs text-muted">{detail}</p>
      {tone === "off" && envVar && (
        <p className="text-xs text-slate-500">
          Set <code className="rounded bg-slate-100 px-1 py-0.5">{envVar}</code> in backend/.env and restart the backend.
        </p>
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
    <form onSubmit={onSubmit} className="px-5 py-4">
      <label htmlFor="display-name" className="text-sm font-medium text-ink">
        Display name
      </label>
      <p className="text-xs text-muted">Shown in the top bar on this browser only.</p>
      <div className="mt-2 flex gap-2">
        <input
          id="display-name"
          value={value}
          onChange={(event) => setDraft(event.target.value)}
          maxLength={60}
          className="h-9 min-w-0 flex-1 rounded-lg border border-line px-3 text-sm outline-none focus:border-accent focus:ring-2 focus:ring-indigo-100"
        />
        <Button type="submit" variant="primary" disabled={draft === null}>
          Save
        </Button>
      </div>
    </form>
  );
}

export default function SettingsPage() {
  const { data: config, error, reload } = useConfigStatus();
  const { data: token } = useInstagramToken(Boolean(config?.instagram_configured));
  const { data: apify } = useApifyUsage();

  const tokenProblem = token && token.state !== "ok" && token.state !== "unknown";
  const missingPermissions = token?.missing_permissions.length ? `Missing permissions: ${token.missing_permissions.join(", ")}` : null;
  const instagramTone: Tone = !config?.instagram_configured ? "off" : tokenProblem || missingPermissions || config.warnings.length ? "warn" : "ok";
  const usageCount = config
    ? [config.youtube_configured, config.instagram_configured, Boolean(apify?.configured), config.ai_configured].filter(Boolean).length
    : 0;

  return (
    <div className="mx-auto max-w-[1600px] space-y-5">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">Settings</h1>
        <p className="text-sm text-muted">Connected services and how much of their limits is used. Keys are set only in backend/.env.</p>
      </div>

      {error && <ErrorBanner message={error} onRetry={reload} />}

      {config?.warnings.map((warning) => (
        <div key={warning} role="alert" className="flex gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <AlertTriangle size={18} className="mt-0.5 shrink-0" />
          <span>{warning}</span>
        </div>
      ))}

      <Section title="Connections">
        {config ? (
          <div className="grid grid-cols-1 gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
            <Connection
              icon={<PlatformIcon platform="youtube" size={20} />}
              name="YouTube"
              tone={config.youtube_configured ? "ok" : "off"}
              detail={config.youtube_configured ? `${config.youtube_key_count} API key${config.youtube_key_count === 1 ? "" : "s"} · channel and video statistics` : "Channel and video statistics"}
              envVar="YOUTUBE_API_KEY"
            />
            <Connection
              icon={<PlatformIcon platform="instagram" size={20} />}
              name="Instagram"
              tone={instagramTone}
              detail={!config.instagram_configured ? "Business Discovery for Professional accounts" : missingPermissions ?? (token ? tokenSummary(token) : "Checking the Meta token…")}
              envVar="META_ACCESS_TOKEN"
            />
            <Connection
              icon={<PlatformIcon platform="facebook" size={20} />}
              name="Facebook"
              tone={config.facebook_configured ? "ok" : "off"}
              detail="Videos and reels on your own Facebook Page"
              envVar="META_FACEBOOK_PAGE_ID"
            />
            <Connection
              icon={<Sparkles size={20} className="text-pink-500" />}
              name="Apify"
              tone={!apify ? "checking" : apify.configured ? "ok" : "off"}
              detail="Public Instagram reel metrics"
              envVar="APIFY_API_TOKEN"
            />
            <Connection
              icon={<Cpu size={20} className="text-slate-500" />}
              name="Groq AI"
              tone={config.ai_configured ? "ok" : "off"}
              detail={`Genre, language and sentiment · ${config.ai_model}`}
              envVar="GROQ_API_KEY_1"
            />
          </div>
        ) : (
          !error && <div className="m-4 h-20 animate-pulse rounded-lg bg-slate-100" />
        )}
      </Section>

      {config && usageCount > 0 && (
        <Section title="Usage" aside="Updates automatically">
          {/* Two columns on tablets (a last odd item spans both), one column per service on wide screens. */}
          <div
            className={`grid grid-cols-1 gap-px bg-line sm:grid-cols-2 ${USAGE_COLUMNS[usageCount]} [&>*]:bg-white sm:[&>*:last-child:nth-child(odd)]:col-span-2 xl:[&>*:last-child:nth-child(odd)]:col-span-1`}
          >
            {config.youtube_configured && <YouTubeQuotaCard />}
            {config.instagram_configured && <InstagramUsageCard />}
            <ApifyUsageCard />
            {config.ai_configured && <GroqUsageCard />}
          </div>
        </Section>
      )}

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <Section title="Profile" className="h-full">
          <ProfileForm />
        </Section>

        {config && (
          <Section title="Processing limits" className="h-full">
            <dl className="grid grid-cols-2 gap-x-6 gap-y-3 px-5 py-4 text-sm sm:grid-cols-3">
              {[
                ["Average views sample", `Latest ${config.average_views_sample_size} videos / reels`],
                ["Recent content fetched", `${config.recent_content_fetch_limit} items per creator`],
                ["Cache reuse window", config.cache_ttl_hours ? `${config.cache_ttl_hours} hours` : "Disabled"],
                ["Max upload size", `${config.max_upload_size_mb} MB`],
                ["Meta Graph API", config.meta_api_version],
              ].map(([label, value]) => (
                <div key={label}>
                  <dt className="text-xs text-muted">{label}</dt>
                  <dd className="font-medium text-ink">{value}</dd>
                </div>
              ))}
            </dl>
          </Section>
        )}
      </div>
    </div>
  );
}
