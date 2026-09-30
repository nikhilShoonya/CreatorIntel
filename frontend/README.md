# CreatorIntel – Frontend

Next.js (App Router) + TypeScript + Tailwind CSS dashboard for the CreatorIntel backend. Full setup is in the [project README](../README.md).

```bash
npm install
cp .env.example .env.local     # NEXT_PUBLIC_API_URL=http://localhost:8000
npm run dev                    # http://localhost:3000
npm run lint && npm run build  # checks
```

## Pages

| Route | Purpose |
| --- | --- |
| `/` | Upload & Analyze: upload card, live processing progress, results table for the latest upload |
| `/creators` | Every creator; `?q=` search and `?upload=<id>` scope |
| `/history` | Previous uploads with counts |
| `/settings` | Integration status (booleans from the backend), processing config, display name |

## Structure

```text
app/            routes (layout with sidebar + top bar)
components/
  layout/       Sidebar, Topbar
  upload/       UploadCard, UploadStatusCard, ProcessingList
  creators/     CreatorsTable, cells, RowActions, ExportMenu, Pagination, CreatorDetailsPanel
  ui/           Buttons, Select, Card, Badges, Menu, PlatformIcon
hooks/          useApi (fetch + polling), data hooks, useDebounce, useStoredValue
lib/            api client, formatting and safe-URL helpers
types/          API types
```

The frontend holds no secrets and no demo data. Everything shown comes from the backend. Only `http(s)` URLs are rendered as links.
