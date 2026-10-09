# CreatorIntel

Internal tool for a small team, with two independent modules:

- **Creator Analytics**: upload an Excel/CSV list of creators (Channel Name + Channel Link). Each creator is enriched from the **YouTube Data API v3** and the **Instagram Graph API**, analysed by **Groq AI** (genre, language, sentiment), and shown in one table you can filter, sort and export.
- **Video Performance**: track individual YouTube videos, Instagram reels and videos/reels of **your own Facebook Page**. Views are checked automatically every day, and each video gets an AI sentiment rating.

```text
CreatorIntel/
├── backend/    FastAPI + SQLAlchemy + Alembic (Neon PostgreSQL or local SQLite)
└── frontend/   Next.js 16 + React 19 + TypeScript + Tailwind CSS 4
```

**No value is ever invented.** If an API does not return a number it is shown as **N/A**. If the AI is not confident, the field is **Unknown** or **Needs review**. Only official APIs are used: no scraping, no cookies, no private endpoints.

---

## 1. Installation

Requirements: Python 3.11+, Node.js 20+.

```bash
# backend
cd backend
python -m venv .venv
.venv\Scripts\activate            # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
cp .env.example .env              # then fill in the values (section 2)

# frontend
cd frontend
npm install
cp .env.example .env.local
```

## 2. Environment variables

All secrets live in `backend/.env`, which is git-ignored and never sent to the browser. The **Settings** page shows which integrations are configured, without showing any values.

### `backend/.env`

| Variable | Required | Purpose |
| --- | --- | --- |
| `YOUTUBE_API_KEY` | for YouTube | YouTube Data API v3 key. Several allowed, comma-separated |
| `YOUTUBE_DAILY_QUOTA` | optional | Units per key per day, for the Settings usage bar (default 10000) |
| `META_ACCESS_TOKEN` | for Instagram / Facebook | Facebook Login user token or **System User** token (starts with `EAA`) |
| `META_APP_SECRET` | recommended | Signs Graph API requests (`appsecret_proof`) |
| `META_APP_ID` | optional | Your Meta app ID (reference only) |
| `META_API_VERSION` | optional | Graph API version, default `v21.0` |
| `META_IG_BUSINESS_ACCOUNT_ID` | optional | Your own Instagram Professional account ID (auto-discovered if empty) |
| `META_FACEBOOK_PAGE_ID` | for Facebook | The Facebook Page whose videos/reels are tracked |
| `GROQ_API_KEY_1` … `_3` | for AI | Groq keys, used in order (see section 5). `GROQ_API_KEY` also works, alone or comma-separated |
| `GROQ_MODEL` | optional | `openai/gpt-oss-20b`, the only model used |
| `GROQ_REQUESTS_PER_MINUTE` / `GROQ_TOKENS_PER_MINUTE` | optional | Per-minute limits that requests are paced under (free plan: 30 / 8000) |
| `AI_SENTIMENT_BATCH_SIZE` | optional | Videos rated per AI request in Video Performance (default 10) |
| `AI_MIN_CONFIDENCE` | optional | Below this confidence an AI field becomes Unknown / Needs review (default 0.5) |
| `DATABASE_URL` | recommended | Neon/PostgreSQL URL. Empty = local SQLite file |
| `AVERAGE_VIEWS_SAMPLE_SIZE` | optional | Latest videos/reels used for average views and engagement (default 10) |
| `RECENT_CONTENT_FETCH_LIMIT` | optional | Latest videos/reels fetched per creator (default 20) |
| `CACHE_TTL_HOURS` | optional | A creator re-uploaded within this window reuses its data (default 24) |
| `UPLOAD_FILE_RETENTION_DAYS` | optional | Uploaded files are deleted after N days; their rows stay (default 30, 0 = keep) |
| `VIDEO_TRACKING_TIMEZONE` | optional | Timezone for the daily jobs and "today" (default `Asia/Kolkata`) |
| `VIDEO_TRACKING_REFRESH_TIME` | optional | Daily view refresh time, `HH:MM` (default `06:30`) |
| `VIDEO_TRACKING_MAX_DAYS` | optional | Stop tracking a video after N days (default 0 = forever) |
| `VIDEO_TRACKING_DOWN_RECHECK_DAYS` | optional | "Video Down" videos are re-checked daily for N days, then only on Retry (default 14) |
| `VIDEO_TRACKING_SCHEDULER_ENABLED` | optional | `false` turns the daily jobs off |
| `CORS_ORIGINS` | optional | Allowed frontend origins (default `http://localhost:3000,http://127.0.0.1:3000`) |

`backend/.env.example` lists every setting with comments.

### `frontend/.env.local`

```env
NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
```

This public URL is the only setting the frontend has. Never put keys here.

> **Restart the backend after editing `backend/.env`.** It is read only at startup. Rows processed before the restart keep their old result; use **Retry failed & partial** on the upload card, or **⋮ → Retry** on a row.

## 3. YouTube API setup

1. In [Google Cloud Console](https://console.cloud.google.com/), create or select a project.
2. **APIs & Services → Library → YouTube Data API v3 → Enable**.
3. **APIs & Services → Credentials → Create credentials → API key**, restricted to the YouTube Data API v3.
4. Put the bare key (39 characters, starts with `AIza`) in `YOUTUBE_API_KEY`. Several keys can be comma-separated.

**How several keys are used:** keys are always tried in order, starting with the first.
- A key that is out of daily quota is skipped until Google's reset at midnight Pacific Time, then used again.
- An invalid or blocked key is skipped for one hour, then retried.
- A 403 about a specific channel (for example a private uploads list) is not treated as a key problem.

Quota is per Google Cloud project, so extra keys only add quota if they come from different projects.

Creator Analytics calls `channels.list`, then `playlistItems.list` on the uploads playlist, then `videos.list` in one batch. It never uses `search.list`, so a creator costs about 3–4 of the 10,000 daily units. **Settings** shows the units used today per key.

## 4. Meta (Instagram and Facebook) setup

**Instagram** data comes from **Business Discovery**, which returns public data for **Professional (Business or Creator) accounts only**. It needs a token from **Instagram API with Facebook Login** (starts with `EAA`). A token from "Instagram API with Instagram Login" (starts with `IGAA`) cannot use Business Discovery.

1. Make your own Instagram account a Professional account and link it to a Facebook Page.
2. At [developers.facebook.com](https://developers.facebook.com/), open your app (type *Business*) and add **Facebook Login for Business** and the **Instagram Graph API**.
3. Create a token with `instagram_basic`, `instagram_manage_insights`, `pages_show_list`, `pages_read_engagement` and `read_insights`. For a token that does not expire, use a **Business Manager System User** and assign your Page and Instagram account to it.
4. Set `META_ACCESS_TOKEN`, `META_APP_SECRET`, and optionally `META_IG_BUSINESS_ACCOUNT_ID`. If you leave the ID empty, it is discovered from your Page.

**Facebook** (Video Performance only): set `META_FACEBOOK_PAGE_ID` and use a System User token with that Page assigned. The Page token is derived at runtime.
- Only videos and reels of **this Page** can be tracked.
- Share links (`facebook.com/share/r/…`) are resolved to the real video link.

**Limits of the official API:**
- Personal, private and other people's Facebook videos cannot be read.
- Such Instagram creators are marked **Failed – Instagram data unavailable**.
- Such videos are marked **Unsupported**, with the reason.

**Settings** shows the token's health (valid, expiry, missing permissions). A banner appears across the app when the token is expired, invalid, or expires within 7 days.

## 5. Groq AI setup

1. Create keys at [console.groq.com/keys](https://console.groq.com/keys).
2. Put them in `GROQ_API_KEY_1`, `GROQ_API_KEY_2`, `GROQ_API_KEY_3`, or comma-separated in `GROQ_API_KEY`.
3. Keep `GROQ_MODEL=openai/gpt-oss-20b`. It is the only model used.

The keys are used in a way that follows Groq's terms and rate limits:
- **One key is active at a time.** The next key is used only when the active one is unusable (invalid/revoked key, or restricted account). An unusable key is retried after 30 minutes. Keys are never rotated to get around rate or daily limits.
- **Per-minute limits:** requests are paced to stay under them. If Groq still answers "rate limited", the app waits as Groq asks, up to 2 minutes per request in total.
- **Daily limit:** when it is reached, the work is marked **AI analysis pending**. A background loop retries pending creators every 15 minutes once quota is available. Video sentiment is retried on the next refresh. Nothing needs to be done by hand.
- **Settings → Groq AI usage today** shows tokens and requests used, each key's state (active / standby / not working / daily limit), and how much work is waiting for AI.

Keys are never logged or shown, only their slot number (#1, #2, #3). The AI never produces counts, views or URLs; those always come from the platform APIs.

## 6. Database setup

- **Neon / PostgreSQL (recommended):** paste the connection string into `DATABASE_URL`, e.g. `postgresql://user:pass@ep-xxxx-pooler.region.aws.neon.tech/neondb?sslmode=require`. The driver is switched to `psycopg` v3 automatically.
- **SQLite (local):** leave `DATABASE_URL` empty; data is stored in `backend/creatorintel.db`.

The schema is managed with **Alembic** (`backend/migrations/`) and migrated automatically when the backend starts. After changing a model, create a migration:

```bash
cd backend
alembic revision --autogenerate -m "describe the change"
```

The test `test_models_and_migrations_are_in_sync` fails if a model change has no migration.

## 7. Running the backend

```bash
cd backend
uvicorn app.main:app --reload            # development
uvicorn app.main:app --host 127.0.0.1 --port 8000   # without auto-reload
```

The API runs at http://localhost:8000, with interactive docs at `/docs`. The backend also runs the background work, so it must stay running for daily tracking:
- the daily Video Performance refresh;
- the AI-pending retry loop;
- file clean-up;
- keeping the database warm.

Backend layout:

| Folder | Contents |
| --- | --- |
| `app/api/` | Creator Analytics REST endpoints (`/api/uploads`, `/api/creators`, `/api/exports`, `/api/config`) |
| `app/agents/` | Pipeline steps: file ingestion, YouTube / Instagram collectors, metrics, AI content analysis, validation |
| `app/services/` | Orchestrator (concurrency, retries, AI-pending loop), Groq client, API usage, YouTube key health, Meta token health, uploads, exports, housekeeping |
| `app/video_performance/` | Video Performance module: models, API (`/api/video-performance/*`), platform clients (YouTube / Instagram / Facebook), tracker, daily scheduler, sentiment, share-link resolver |
| `app/models/`, `migrations/` | SQLAlchemy models and Alembic migrations |
| `app/utils/` | URL parsing, spreadsheet export, logging with secret redaction, time helpers |

## 8. Running the frontend

```bash
cd frontend
npm run dev                 # development, http://localhost:3000
npm run build && npm start  # production
```

| Page | What it does |
| --- | --- |
| **Upload & Analyze** (`/`) | Upload an Excel/CSV of creators and follow its progress |
| **Creator List** (`/creators`) | All analysed creators: search, filter, sort, details popup, add/edit/delete, retry, re-analyse, export |
| **History** (`/history`) | Past uploads, their file data, download, delete |
| **Video Performance → Dashboard** | Totals, daily views trend, sentiment breakdown, top / most engaging videos, job status with **Run now** |
| **Video Performance → Tracking Library** | **Tracked videos** (table, filters, history, bulk actions) and **Uploads** (each uploaded video list) |
| **Settings** | Integration status, YouTube quota per key, Meta usage (Instagram + Facebook), Groq AI usage per key, Meta token health |

The frontend calls only the backend (`NEXT_PUBLIC_API_URL`) and holds no secrets.

## 9. Creator Analytics: Excel / CSV input

| Channel Name | Channel Link |
| --- | --- |
| Trading Tech | https://www.instagram.com/tradingtech31/ |
| Nitin Nitro | https://www.youtube.com/@nitinnitro |

- **Files:** `.xlsx`, `.xls`, `.csv`, max 10 MB, up to 1,000 rows.
- **Headers:** `Channel Name` / `Creator Name`, and `Channel Link` / `Creator Link` / `URL`.
- **Links:**
  - Instagram profile URLs;
  - YouTube `@handle` (any language, e.g. Hindi handles), `/channel/UC…`, `/c/…`, `/user/…`;
  - YouTube video links, where the video's channel is used.
- **Duplicates and bad links:** duplicate rows are removed. Invalid links are kept as Failed rows with a reason.
- **Example:** `backend/samples/sample_creators.xlsx`.

| Where | What you can do |
| --- | --- |
| Upload & Analyze, Creator List | **Add creator** without an Excel file |
| Row **⋮** menu / details popup | **Edit** name or link, **Delete**, **Retry**, **Re-analyze** |
| Table checkboxes | **Delete selected** |
| History | **Delete upload**, optionally with its creators that belong to no other upload |

Metrics and AI fields cannot be edited by hand. Use **Refresh Data** or **Re-analyze** instead.

## 10. Video Performance

**Tracking Library → Upload Excel**:
- **Columns:** `Video Link` is required. `Creator Name`, `Platform` and `Username` are optional. `Username` is needed for an Instagram reel when the link does not contain it.
- **Example:** `backend/samples/sample_videos.xlsx`.
- **Single videos:** use **Add video** instead.

**Supported links:**
- YouTube videos and Shorts;
- Instagram reels/posts;
- your Page's Facebook videos and reels, including share links.

**Daily refresh:** runs at `VIDEO_TRACKING_REFRESH_TIME` in `VIDEO_TRACKING_TIMEZONE`, inside the backend; no browser is needed.
- If the backend was off at that time, the refresh runs as soon as it starts.
- A failed run is retried after 30 minutes.
- The Dashboard's **Run now** starts it by hand.

**Statuses:**
- **Tracking:** checked daily.
- **Video Down:** a video that was seen before is now missing. It is re-checked daily for `VIDEO_TRACKING_DOWN_RECHECK_DAYS`, then only on Retry.
- **Unsupported:** the official API cannot read the video, with the reason.
- **Failed:** a temporary error; it is retried.
- **Paused:** you stopped tracking it.

**Instagram limits:**
- A reel can only be read through its owner's **Professional** account.
- Only about the latest 200 posts of an account are searchable (`VIDEO_TRACKING_INSTAGRAM_SCAN_PAGES`).
- A collab reel is listed under the account that posted it.

## 11. How the numbers are calculated

Counts (followers, views, likes, comments) always come straight from the platform APIs. The app only does arithmetic on them.

### Creator Analytics

The latest `RECENT_CONTENT_FETCH_LIMIT` (20) videos/reels are fetched. Live and upcoming streams are ignored. A view count of 0 alongside likes is treated as "not reported".

| Field | Calculation |
| --- | --- |
| **Followers / Subscribers** | As reported by the API |
| **Average Views** | Mean views of the latest `AVERAGE_VIEWS_SAMPLE_SIZE` (10) videos/reels that have a view count |
| **Median Views** | Median of the same sample |
| **Long-form / Shorts average** (YouTube) | Same average, split by duration: over 3 minutes vs. 3 minutes or less |
| **Top Performing Video** | The video/reel with the most views among those fetched |
| **Engagement Rate** | For each of the latest 10 videos with views, likes and comments: `(likes + comments) / views × 100`. The rate shown is the **average of these per-video rates** |
| **Engagement Rate (Instagram fallback)** | Only when no reel has a view count: `(likes + comments) / followers × 100` per post, averaged. Labelled "follower-based"; the two bases are never mixed |
| **Genre, Language, Sentiment** | Groq AI (see below) |

**AI analysis (genre, sub-genre, language, sentiment):**
- **Input:** a bounded sample of the creator's public text: bio, plus up to 15 recent titles/captions and 25 tags/hashtags, max ~9,000 characters.
- **Output:** the AI answers in a strict JSON schema with a fixed list of genres and languages. Each field comes with a confidence from 0 to 1.
- **Sentiment:** the overall tone of that content: Positive, Neutral or Negative. It describes the content, never the person. Informational or educational content is usually Neutral.
- **Low confidence:** below `AI_MIN_CONFIDENCE` (0.5), language and sentiment become **Unknown**, and genre is flagged **Needs review**. Genre "Other" is also flagged for review.
- **Validation:** answers are checked against the allowed values; invalid output is retried a bounded number of times.

### Video Performance

Each check stores a snapshot with views, likes and comments.

| Field | Calculation |
| --- | --- |
| **Current Views** | Views from the API at the latest check. Facebook: `total_video_views`, or the Reels play count for reels |
| **Previous Views** | Views of the latest snapshot taken **before today** (in `VIDEO_TRACKING_TIMEZONE`) |
| **Views Gained** | `current views − previous views` (day-over-day) |
| **Growth %** | `views gained / previous views × 100` |
| **Engagement Rate** | `(likes + comments) / views × 100` for that video; N/A when a value is missing |
| **Dashboard engagement** (trend) | Weighted across videos: `total (likes + comments) / total views × 100` |
| **Views gained (trend)** | Per day, the sum of each checked video's gain. A video's first ever check is not counted as a gain |
| **Sentiment** | Groq AI rates each video's own title + caption/description as Positive, Neutral or Negative, with a confidence (videos are sent in batches of `AI_SENTIMENT_BATCH_SIZE`) |

Sentiment details:
- Text shorter than 15 characters is not sent to the AI ("Not enough text content").
- A confidence below 0.5 is shown as not analysed ("Low confidence").
- Each video is rated once. If the AI is busy or at its daily limit, the rating is retried on the next refresh.

## 12. Files, logs and speed

- **Uploaded files** are deleted after `UPLOAD_FILE_RETENTION_DAYS`. Their rows stay in the database and remain visible and downloadable, under **History → View file data** and **Tracking Library → Uploads**.
- **Logs** go to `backend/logs/creatorintel.log`, rotated daily and kept for `LOG_RETENTION_DAYS`. Keys, tokens and database passwords are redacted.
- **Remote database speed:**
  - Each round trip to Neon adds latency, about 300 ms from India to us-east-2.
  - The app keeps a connection pool, keeps Neon awake during `DB_KEEP_WARM_HOURS`, and shows previously loaded data instantly.
  - For the best speed, create the Neon project in **AWS Asia Pacific – Singapore**.

## 13. Troubleshooting

| Symptom | Fix |
| --- | --- |
| Rows fail with "… is not configured" after adding keys | Restart the backend, then **Retry failed & partial** |
| "YouTube API key is invalid" | Use the bare `AIza…` key(s), comma-separated, with no quotes or extra text |
| "YouTube API daily quota exceeded" | All keys are out of quota. They are used again after midnight Pacific Time |
| Settings shows a Groq key as **Not working** | The key is invalid or revoked. Fix it in `backend/.env` and restart. A key is never sent as part of a longer string |
| "AI analysis pending" | A Groq limit was reached. It is retried automatically; nothing to do |
| Genre/language/sentiment show N/A | No Groq key, or the creator has no public text. Use **Re-analyze** after fixing |
| Instagram: "Instagram Login token" | Generate a Facebook Login / System User `EAA…` token (section 4) |
| Instagram: "cannot access your Instagram Business account" | The token is not linked to `META_IG_BUSINESS_ACCOUNT_ID`. Check the ID and the token's assets |
| Instagram: "not a Professional account" | Expected for personal/private accounts; the official API cannot read them |
| Facebook video **Unsupported** | Only videos of `META_FACEBOOK_PAGE_ID` can be read |
| "Cannot reach the backend" | Start the backend and check `NEXT_PUBLIC_API_URL`, then restart the frontend |
| CORS error in the browser | Add the frontend origin to `CORS_ORIGINS` |
| A row failed temporarily | **⋮ → Retry** on that row |

## 14. Tests and checks

```bash
# backend
cd backend
pip install -r requirements-dev.txt
python -m pytest -q
# run the suite against PostgreSQL instead of a temporary SQLite file:
CREATORINTEL_TEST_DATABASE_URL=postgresql://... python -m pytest -q

# frontend
cd frontend
npx tsc --noEmit
npm run lint
```

The tests replace every external API (YouTube, Graph API, Groq) with `httpx.MockTransport` fakes. The application code contains no mock or demo data.
