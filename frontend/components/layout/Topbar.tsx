"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Search } from "lucide-react";

import { useStoredValue } from "@/hooks/useStoredValue";
import { initials } from "@/lib/format";

export const USER_NAME_KEY = "creatorintel:user-name";

export function Topbar() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [name] = useStoredValue(USER_NAME_KEY, "Team Member");

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    const q = query.trim();
    router.push(q ? `/creators?q=${encodeURIComponent(q)}` : "/creators");
  }

  return (
    <header className="sticky top-0 z-30 flex h-16 items-center justify-between gap-4 border-b border-line bg-white/90 px-4 backdrop-blur sm:px-6">
      <form onSubmit={onSubmit} role="search" className="relative w-full max-w-md">
        <Search size={17} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search creators, platform, genre, language…"
          aria-label="Search creators"
          maxLength={200}
          className="h-10 w-full rounded-lg border border-line bg-white pl-10 pr-3 text-sm outline-none placeholder:text-slate-400 focus:border-accent focus:ring-2 focus:ring-indigo-100"
        />
      </form>

      <Link href="/settings" className="flex shrink-0 items-center gap-3 rounded-lg px-2 py-1.5 hover:bg-slate-50" title="Profile & settings">
        <span className="flex h-9 w-9 items-center justify-center rounded-full bg-accent text-sm font-semibold text-white">
          {initials(name)}
        </span>
        <span className="hidden text-sm font-medium text-ink sm:block">{name}</span>
      </Link>
    </header>
  );
}
