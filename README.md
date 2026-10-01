# CreatorIntel

Internal tool for a small team. Upload an Excel/CSV list of creators (Channel Name + Channel Link); the app enriches each creator from the **official YouTube Data API v3** and **Instagram Graph API**, runs **Groq LLM** content analysis (genre, language, sentiment), and shows everything in one table you can filter, sort and export.

```text
CreatorIntel/
├── backend/    FastAPI + SQLAlchemy (Neon PostgreSQL or local SQLite), enrichment pipeline
└── frontend/   Next.js + TypeScript + Tailwind CSS dashboard
```

No value is ever invented. If an API does not provide a number it is shown as **N/A**; if the AI is not confident, the field is **Unknown** / **Needs review**.

---

## 1. Installation

Requirements: Python 3.11+, Node.js 20+.

```bash
# backend
cd backend
pip install -r requirements.txt
cp .env.example .env        # then fill in the values (see below)

# frontend
cd frontend
npm install
cp .env.example .env.local
```

## 2. Environment variables

### `backend/.env`

| Variable | Required | Purpose |
| --- | --- | --- |
| `YOUTUBE_API_KEY` | for YouTube rows | YouTube Data API v3 key (several allowed, comma-separated) |
| `META_APP_ID` | optional | Your Meta app ID (reference) |
| `META_APP_SECRET` | recommended | Used to sign requests (`appsecret_proof`) |
| `META_ACCESS_TOKEN` | for Instagram rows | **Facebook Login** user token (starts with `EAA`) |
| `META_API_VERSION` | optional | Graph API version, default `v21.0` |
| `META_IG_BUSINESS_ACCOUNT_ID` | optional | Your own Instagram Professional account ID (auto-discovered if empty) |
| `GROQ_API_KEY` | for AI fields | Groq API key |
| `GROQ_MODEL` | optional | Default `openai/gpt-oss-20b` (must support strict JSON schema) |
| `DATABASE_URL` | recommended | Neon/PostgreSQL URL. Empty = local SQLite file |
| `AVERAGE_VIEWS_SAMPLE_SIZE` | optional | Videos/reels used for average views (default 10) |
| `CACHE_TTL_HOURS` | optional | Reuse data for a creator re-uploaded within this window (default 24) |

### `frontend/.env.local`

```env
NEXT_PUBLIC_API_URL=http://localhost:8000
```

Only this public URL lives in the frontend. All keys stay in `backend/.env`, which is git-ignored. The **Settings** page shows which integrations are configured, without exposing any values.

> **Restart the backend after editing `backend/.env`.** `uvicorn --reload` only watches `.py` files. To also reload on `.env` changes, run `uvicorn app.main:app --reload --reload-include .env`. Rows processed before the restart keep their old result; use **Retry failed & partial** on the upload card (or **⋮ → Retry** per row).

## 3. YouTube API setup

1. Open [Google Cloud Console](https://console.cloud.google.com/) and create (or select) a project.
2. **APIs & Services → Library → YouTube Data API v3 → Enable**.
3. **APIs & Services → Credentials → Create credentials → API key**. Restrict it to the YouTube Data API v3.
4. Put it in `YOUTUBE_API_KEY` — the bare key (39 characters, starts with `AIza`). Several keys may be listed comma-separated (`AIza…,AIza…`); when one is invalid or out of quota the next is used. Quota is per Google Cloud project, so extra quota only helps if the keys come from different projects.

The pipeline calls `channels.list` (`forHandle` / `id` / `forUsername`), then `playlistItems.list` on the channel's uploads playlist, then `videos.list` in one batch. `search.list` is never used, so each creator costs about 3–4 quota units out of the default 10,000 per day.

## 4. Meta (Instagram) API setup

Instagram data comes from **Business Discovery**, which returns public data for **Professional (Business or Creator) accounts only**.

Business Discovery works **only with "Instagram API with Facebook Login"**. A token from "Instagram API with Instagram Login" (it starts with `IGAA`) cannot use Business Discovery; the API answers "nonexisting field (business_discovery)".

1. Make your own Instagram account a Professional (Business or Creator) account.
2. Create a Facebook Page and link the Instagram account to it (Instagram app → Settings → Accounts Center, or Page settings → Linked accounts).
3. At [developers.facebook.com](https://developers.facebook.com/) open your app (type *Business*) and add the **Facebook Login for Business** and **Instagram Graph API** products.
4. In the [Graph API Explorer](https://developers.facebook.com/tools/explorer/) select your app, choose **User Token**, and add `instagram_basic`, `instagram_manage_insights`, `pages_show_list` and `pages_read_engagement`. Click **Generate Access Token** and select your Page and Instagram account. The token starts with `EAA`.
5. Make it long-lived (~60 days) with the [Access Token Debugger](https://developers.facebook.com/tools/debug/accesstoken/) → **Extend Access Token**. For a token that never expires, use a Business Manager system user.
6. Set `META_ACCESS_TOKEN` (the `EAA…` token), `META_APP_SECRET`, and optionally `META_IG_BUSINESS_ACCOUNT_ID`. Leave it empty and it is discovered from your Page. Restart the backend; the **Settings** page warns if the token type is wrong.

Personal/consumer and private accounts **cannot** be read through the official API. The app does not scrape: those rows are marked **Failed – "Instagram data unavailable"**, and the rest of the batch continues. If the API version does not return reel `view_count`, average views show N/A and engagement falls back to the clearly labelled follower-based formula.

## 5. LLM setup (Groq)

1. Create a key at [console.groq.com/keys](https://console.groq.com/keys).
2. Set `GROQ_API_KEY`. Keep `GROQ_MODEL=openai/gpt-oss-20b`, or use `openai/gpt-oss-120b`. The model must support **strict JSON-schema** output.

The LLM sees only a bounded sample (bio, recent titles/captions, tags). It returns genre, sub-genre, language, secondary language, sentiment and confidences against a fixed taxonomy, and the result is validated with Pydantic. Invalid output is retried a bounded number of times, then marked unavailable. Counts, views and URLs never come from the LLM.

## 6. Database setup

- **Neon / PostgreSQL (recommended):** paste the Neon connection string into `DATABASE_URL`, e.g. `postgresql://user:pass@ep-xxxx-pooler.region.aws.neon.tech/neondb?sslmode=require`. The app switches the driver to `psycopg` v3 automatically and creates tables on first start.
- **SQLite (local):** leave `DATABASE_URL` empty; data is stored in `backend/creatorintel.db`.

## 7. Running the backend

```bash
cd backend
uvicorn app.main:app --reload
```

The API runs at http://localhost:8000 (interactive docs at `/docs`).

## 8. Running the frontend

```bash
cd frontend
npm run dev
```

Open http://localhost:3000.

## 9. Excel / CSV input format

| Channel Name | Channel Link |
| --- | --- |
| Trading Tech | https://www.instagram.com/tradingtech31/ |
| Nitin Nitro | https://www.youtube.com/@nitinnitro |

- Formats: `.xlsx`, `.xls`, `.csv`, max 10 MB, up to 1,000 rows.
- Header variants accepted: `Channel Name` / `channel_name` / `Creator Name`, and `Channel Link` / `channel_link` / `Creator Link` / `URL`.
- Links: Instagram profile URLs (query strings, `/reels/` etc. are stripped), and YouTube `@handle`, `/channel/UC…`, `/c/…` and `/user/…` URLs.
- Duplicates (same platform + normalised handle) are removed. Invalid or unsupported links are kept as Failed rows with a reason.
- `backend/samples/sample_creators.xlsx` is an example covering valid, duplicate and invalid rows.

## Managing data (add / edit / delete)

| Where | What you can do |
| --- | --- |
| Upload & Analyze, Creator List | **Add creator** (name + link, no Excel needed; on the dashboard it joins the current upload) |
| Row **⋮** menu / details popup | **Edit** name or link (a new link clears old data and re-fetches it), **Delete**, Retry, Re-analyze |
| Table checkboxes | **Delete selected** (bulk) |
| History | **Delete upload**, optionally together with creators that belong to no other upload |

Metrics (followers, views, engagement) and AI fields cannot be edited by hand. They always come from the platform APIs and the AI, so use **Refresh Data** or **Re-analyze** instead.

## Video Performance (independent module)

Sidebar → **Video Performance** → **Dashboard** / **Tracking Library**. It tracks individual videos/reels every day and auto-detects new videos of tracked creators. It has its own tables (`video_tracking_*`), API (`/api/video-performance/*`), services and jobs (`backend/app/video_performance/`), and shares only the `.env` keys, HTTP/LLM clients and database connection with Creator Analytics.

- **Tracking Library:** upload an Excel/CSV (`Video Link` required; `Creator Name`, `Platform`, and `Username` for Instagram reels optional), add a video, or track a creator/channel. Edit, pause/resume, refresh, retry, view history, bulk actions and delete are all available there. Example file: `backend/samples/sample_videos.xlsx`.
- **Daily jobs** run inside the backend (no browser needed):
  - `VIDEO_TRACKING_DISCOVERY_TIME`: new-video discovery for tracked creators
  - `VIDEO_TRACKING_REFRESH_TIME`: view refresh of every tracked video

  Both times are in `VIDEO_TRACKING_TIMEZONE`. A job missed while the server was off runs once at startup. Both jobs can also be started from the Dashboard (**Run now**).
- **Daily numbers:** each check stores a snapshot. *Previous views* is the latest snapshot from an earlier day, so *Views gained* and *Growth %* are day-over-day. Engagement is `(likes + comments) / views × 100`, or N/A when a value is missing.
- **Instagram limits (official API):** a reel can only be read through its owner's **Professional** account (Business Discovery), so the owner's username is needed, either in the link (`instagram.com/<user>/reel/<code>`) or in a `Username` column. Only roughly the latest 200 posts of an account are searchable (`VIDEO_TRACKING_INSTAGRAM_SCAN_PAGES`). Personal/private accounts show **Unsupported** with the reason.

## Database migrations

The schema is managed with **Alembic** (`backend/migrations/`). Migrations run automatically when the backend starts. A database created before migrations existed is detected and upgraded in place, with no data loss. After changing a model, create a migration:

```bash
cd backend
alembic revision --autogenerate -m "describe the change"
```

A test (`test_models_and_migrations_are_in_sync`) fails if a model change has no migration.

## Uploaded files, logs and token health

- **Uploaded files** are deleted after `UPLOAD_FILE_RETENTION_DAYS` (default 30). Every row is saved in the database first, and stays visible under **History → View file data** and **Video Performance → Tracking Library → Uploads**, with Excel/CSV download.
- **Logs** are written to `backend/logs/creatorintel.log`, rotated daily and kept for `LOG_RETENTION_DAYS`. API keys, tokens and database passwords are redacted.
- **Meta token health**: **Settings** shows whether the Instagram token is valid, when it expires, and any missing permissions. A banner appears across the app when it is expired, invalid, or expires within 7 days.

## Speed with a remote database

Every database round trip from your machine to the database server adds latency (about 300 ms from India to Neon's us-east-2). The app keeps that to one round trip per request where possible. It also keeps a pool of open connections, keeps Neon awake during `DB_KEEP_WARM_HOURS`, and shows previously loaded data immediately when you revisit a page.

**Fastest option:** create the Neon project in the region closest to you (e.g. **AWS Asia Pacific – Singapore** for India). Each round trip drops to well under 100 ms. Point `DATABASE_URL` at the new project; tables are created automatically on startup.

## 10. Troubleshooting

| Symptom | Fix |
| --- | --- |
| Every row fails with "… is not configured" after adding keys | The backend was not restarted. Restart it, then click **Retry failed & partial**. |
| "YouTube API key is invalid" | The value must be the bare `AIza…` key(s), comma-separated if several, with no quotes or extra text. |
| Instagram: "META_ACCESS_TOKEN is an Instagram Login token" | Generate a Facebook Login `EAA…` token as described in section 4. |
| "Cannot reach the backend at http://localhost:8000" | Start the backend; check `NEXT_PUBLIC_API_URL`, then restart `npm run dev`. |
| CORS error in the browser | Add your frontend origin to `CORS_ORIGINS` in `backend/.env`. |
| `ModuleNotFoundError: psycopg` | Run `pip install -r requirements.txt` (includes `psycopg[binary]`). |
| "YouTube API daily quota exceeded" | Wait for the daily reset (Pacific time) or request more quota. |
| Instagram "account is not a Professional account…" | Expected for personal/private accounts; the official API cannot read them. |
| "Meta access token is invalid or expired" | Generate a new long-lived token. |
| Genre/language/sentiment show N/A | `GROQ_API_KEY` missing or invalid, or the creator has no public text. Use **Re-analyze** after fixing. |
| A row failed temporarily (rate limit) | Use **⋮ → Retry** on that row; only that creator is re-processed. |

## Tests

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest -q
# run the suite against PostgreSQL instead of a temp SQLite file:
CREATORINTEL_TEST_DATABASE_URL=postgresql://... python -m pytest -q
```

The tests stub external HTTP (YouTube, Graph API, Groq) with `httpx.MockTransport`; the application code itself contains no mock or demo data.
