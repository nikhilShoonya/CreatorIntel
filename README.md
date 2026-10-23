# CreatorIntel

Internal tool for a small team, with two independent modules:

- **Creator Analytics**: upload an Excel/CSV list of creators (Channel Name + Channel Link). Each creator is enriched from the **YouTube Data API v3** and the **Instagram Graph API**, analysed by **Groq AI** (genre, language, sentiment), and shown in one table you can filter, sort and export.
- **Video Performance**: track individual YouTube videos, Instagram reels and videos/reels of **your own Facebook Page**. Views are checked automatically every day, and each video gets an AI sentiment rating.

```text
CreatorIntel/
├── backend/    FastAPI + SQLAlchemy + Alembic (Neon PostgreSQL or local SQLite)
└── frontend/   Next.js 16 + React 19 + TypeScript + Tailwind CSS 4
```

**No value is ever invented.** Missing numbers are shown as **N/A**, or a previously saved value is retained with a failure/Partial note. If the AI is not confident, the field is **Unknown** or **Needs review**. Creator Analytics, YouTube and Facebook keep their official API integrations. Video Performance can optionally use Apify to scrape public Instagram Reel metrics; this is not Meta's official API or private Instagram Insights. The app does not request Instagram login cookies.

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
| `VIDEO_TRACKING_AUTO_REFRESH` | optional | Automatic daily refresh of every tracked video (default `false`: on demand only) |
| `UPLOAD_FILE_RETENTION_DAYS` | optional | Default 0 = uploaded files are kept forever. N > 0 deletes the stored file after N days (its rows always stay) |
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
| **Upload & Analyze** (`/`, no longer in the sidebar) | The previous upload page; still works at `/`. Creator uploads are now done from the Creator List (**Upload Creators**) |
| **Creator Analytics → Dashboard** (`/creator-analytics`) | Totals and distributions over all stored creators (platform, status, genre, language, sentiment, average engagement), recently analysed creators and those needing attention. Read-only: opening it never calls YouTube, Instagram or the AI. Clicking a number opens the Creator List with that filter |
| **Creator Analytics → Creator List** (`/creators`) | All analysed creators: search, filter, sort, details popup, add/edit/delete, retry, re-analyse, export. Filters can be set in the link, e.g. `/creators?status=Failed` |
| **History** (`/history`) | Two tabs: **Creator Analytics History** (creator uploads: file data, view creators, delete) and **Video Tracking History** (video lists: view data, rename, re-check, download, delete), with a file-name search |
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
| Creator List | **Upload Creators** (drag & drop or browse an Excel/CSV; existing creators reuse their data, only new ones are fetched) and **Add creator** without a file |
| Row **⋮** menu / details popup | **Edit** name or link, **Delete**, **Retry**, **Re-analyze** |
| Table checkboxes | **Delete selected** |
| History | **Delete upload**, optionally with its creators that belong to no other upload |

**Uploading creators that are already in the list:** their stored data is reused as it is, however old, with no YouTube, Instagram or AI calls. Failed and partial creators are not retried by an upload. Only new creators are fetched and analysed, and duplicate rows within a file are still removed.

**Updating one creator:** open its details.
- **Refresh Profile** fetches the latest YouTube / Instagram data. It does not re-run the AI.
- **Re-analyze** re-runs only the AI.
- **Retry** (failed creators) re-runs both.

Metrics and AI fields cannot be edited by hand.

## 10. Video Performance

### Optional Apify public Reel metrics

This provider extends the existing Tracking Library, uploads, video records, history, exports, dashboard and Groq sentiment. It does not create another tracker. Creator Analytics, Instagram posts/IGTV, creator discovery, YouTube and Facebook retain their current providers. Apify reads public Reel information and does **not** provide private Instagram Insights such as account reach or audience demographics.

Install backend requirements, then obtain a token from [Apify Console > Settings > API & Integrations](https://console.apify.com/settings/integrations). Set these in **backend/.env only**:

```env
APIFY_API_TOKEN=your_token_here
APIFY_INSTAGRAM_REEL_ACTOR_ID=apify/instagram-reel-scraper
APIFY_INSTAGRAM_REEL_ENABLED=true
APIFY_INCLUDE_SHARES=false
APIFY_INSTAGRAM_REEL_BATCH_SIZE=25
APIFY_INSTAGRAM_REEL_MONTHLY_RESULT_LIMIT=1500
```

The provider defaults to disabled and requires both an enabled flag and a nonempty token. Batch size is validated from 1 to 100. The monthly guard accepts 0 to 1,000,000 units; **0 blocks new runs**. Never put the token in frontend configuration. Shares require explicit opt-in and may cost extra; transcripts and downloaded videos are always disabled. The official [`apify-client` Python SDK](https://docs.apify.com/api/client/python/) starts asynchronous Actor runs from the existing background tracker. The [Actor input schema](https://apify.com/apify/instagram-reel-scraper/input-schema) calls its URL array `username`, but accepts direct Reel URLs. The [Actor output samples](https://apify.com/apify/instagram-reel-scraper) document the source fields used below.

Restart the backend to run migration `0006_apify_reels`. It adds nullable source/provenance fields to existing videos and snapshots, plus provider-job/reservation tables. It preserves existing records and history. Legacy URLs were canonicalized identically for `/p/` and `/reel/`; the migration derives media kind only from unambiguous original upload URLs. Legacy manual records without this evidence stay on Meta until their original link is supplied through Edit or re-upload. Existing uploaded posts remain on Meta. Newly identified `/p/` and `/tv/` links also stay on Meta even though the existing canonical display link uses `/reel/`.

**Upload through Tracking Library > Upload Excel.** A `Video Link` column can contain `https://www.instagram.com/reel/<actual-code>/`, `/reels/<actual-code>/`, or a supported username-bearing Reel URL. No username column is required for Apify. A bare, case-sensitive shortcode is accepted only with `Platform=Instagram`/`ig`, or an explicit `Instagram Reel URL`, `Instagram Reel Link`, `IG Reel URL`, `IG Reel Link` or `Instagram Reel Shortcode` header. Generic `Reel URL` does not establish the platform for a bare code. Original values, hyperlink targets and literal HYPERLINK formulas retain the existing safe parser behaviour; formulas are never executed. Mixed-platform files work as before. Duplicate rows share one tracked video and one Actor input; upload membership/history stay intact. Re-uploading an already tracked video does not itself force a paid refresh: use Retry or Refresh when needed.

**Selection and fallback:** when configured, explicitly identified direct Reels use Apify without Meta owner discovery. Other paths use the existing providers. Missing token/disabled flag leaves Meta available. An Apify failure, usage limit, absent result, auth error or rate limit does **not** trigger additional Meta searches or immediate paid resubmissions. Normal per-video Refresh/Retry, bulk actions and Dashboard Run now use this same tracker. `VIDEO_TRACKING_AUTO_REFRESH` remains **false by default**; enabling Apify does not enable recurring refresh. Explicitly enabling daily metrics refresh will consume units per Reel per run. Paused/completed records retain the existing scheduling rules, including explicit manual refresh of paused videos.

**Metric mapping and history:** exact `shortCode`, `url` and `inputUrl` identities must agree. Results are never matched by position, captions, owner name or a lowercased shortcode. Duplicate/conflicting outputs are rejected. Each valid item is saved independently, including valid items from partial/failed Actor datasets.

| Actor field | Saved behaviour |
| --- | --- |
| `videoPlayCount` | Preferred Current Views (including a genuine 0) |
| `igPlayCount` | Fallback only if videoPlayCount is unavailable; labelled in the note/provenance |
| `videoViewCount` | Last fallback, explicitly labelled as a different source metric |
| `likesCount`, `commentsCount` | Likes/comments; missing, invalid or hidden counts remain null |
| `ownerUsername` | Validated posting handle; cannot replace a verified conflicting owner |
| `caption`, `timestamp` | Existing caption/publication fields; caption continues through Groq sentiment |
| `sharesCount` | Source metrics only when explicitly enabled and available |
| `videoDuration` | Nullable source metrics, in seconds |
| `error`, `errorDescription` | Controlled item-level failure message; raw provider errors are not exposed |

Separate play/view values and duration are preserved in `source_metrics` on the existing video and snapshot. API/history responses include `metrics_source`, `view_metric` and these raw values; existing visible table columns and export layouts are unchanged. Missing required counts produce Partial. A result with no valid count is a failure, not a successful scrape. Missing counts never fabricate zero or erase stored valid numbers. Engagement is calculated from the current returned likes/comments and canonical count only; missing inputs yield N/A. Previous Views follows the existing local-day snapshot rule **within the same provider/metric basis**. Switching from Meta to Apify or from plays to views establishes a new baseline, with N/A growth until a comparable previous-day snapshot exists. History and dashboard trends do not treat a change in metric basis as gained/lost views. A transient provider failure preserves values and Last Checked and never establishes Video Down. Last Checked advances after a valid matched metrics retrieval, including Partial retrievals.

**Usage guard and recovery:** Settings shows a read-only Apify integration/usage indicator, also available at `GET /api/config/apify-usage`. Before any Actor POST, a cross-process database lock serializes reservations. One unit is reserved per unique submitted URL. Reservations, Actor IDs, remote run IDs, safe diagnostics, returned-item counts and available reported charge events/costs are persistent. The guard counts the submission month's units plus active reservations from earlier months (UTC month boundaries). Settled runs consume at least their reserved units, increased if more dataset items were returned. Failed or uncertain submissions deliberately keep their conservative units. Lowering the limit blocks new work without cancelling existing runs. The default 1,500 units is **not** a promise of fitting any Apify Free-plan dollar credit.

This is an **application estimate, not authoritative billing or a hard account spending cap**. Other Actors, other applications, paid extras, platform/compute charges, changing prices and provider-side billing events can consume shared Apify credits. Reported run costs/events are stored where available but are not integrated account billing. Dataset result counts are not assumed equal to billable events. The Actor receives `max_items` and a 600-second timeout; `max_items` does not guarantee a monetary cap for pay-per-event pricing. Check Apify account billing/limits separately.

One active provider reservation per video prevents duplicate billable jobs across refreshes/processes/restarts. The SDK uses **zero automatic HTTP retries**, especially for the billable start POST. Each batch has a bounded 660-second worker window; known remote runs are resumed on Retry/restart instead of restarted. An uncertain POST response without a saved run ID blocks new submissions for those videos. Settings displays its local job ID. An operator must first inspect Apify Console and either attach the actual remote run ID or confirm no remote run remains active, using `POST /api/video-performance/apify-runs/<local-job-id>/reconcile`:

```json
{"confirmed_remote_state": true, "remote_run_id": "actual_existing_run_id"}
```

Alternatively, use `remote_run_id: null` **only after verifying no remote run is active** to release duplicate suppression. This does not refund usage units or start a scrape. Then Retry the affected records. Normal users do not need to use Apify Console; this is exceptional operator recovery for an ambiguous submission. Failed/unknown jobs do not enter Meta's automatic rate-limit queue. Private/deleted/restricted Reels, hidden counts, Actor schema changes and Apify access/credit limits can still prevent retrieval.

**Optional live smoke test (consumes Apify credits):** after installing requirements and restarting/migrating, check Settings, then Add video with one real public Reel URL (for example your supplied `https://www.instagram.com/reel/Dd9E0uFRFW4/`) and leave username empty. If it already exists, Edit its original link if necessary and use Retry/Refresh instead of adding a duplicate. Watch the existing row progress to Tracking/Partial or an actionable error. Verify Current Views, Last Checked, source metric and History. A second same-day refresh creates another check but does not invent a previous-day value. Inspect the saved remote run ID and Settings usage. No live request is required or performed by automated tests; mock test success does not prove that this real Reel is accessible.

Validation from `backend`: `python -m pytest -q`; the suite includes `tests/test_apify_reels.py` and `tests/test_bug_fixes.py::test_models_and_migrations_are_in_sync`. To apply manually: `python -m alembic upgrade head`. From `frontend`: `npx tsc --noEmit` and `npm run lint` (on Windows with restricted PowerShell scripts, use `npx.cmd`/`npm.cmd`). All Apify tests use synthetic responses via mocked HTTP transports and never consume real credits.

The remaining owner-resolution instructions in this section describe the **Meta provider**. They are not prerequisites for Apify-enabled direct Reel tracking.

**Tracking Library → Upload Excel**:
- **Columns:** `Video Link` is required. `Creator Name`, `Platform` and `Username` are optional. Apify-enabled direct Instagram Reels need no username. The Meta path needs a known posting account when automatic owner resolution cannot find it.
- **Example:** `backend/samples/sample_videos.xlsx`.
- **Single videos:** use **Add video** instead.

**Tracking Library → Uploads** lists every uploaded video list. Each one can be:
- **viewed**: its tracked videos, plus the invalid rows and why they were skipped;
- **renamed**;
- **re-checked**: views of all its videos are fetched now;
- **downloaded**: the original file, or its rows as Excel;
- **deleted**: its videos keep being tracked, unless you tick "also delete the videos this file added". Videos tracked before the file are never touched.

**Supported links:**
- YouTube videos and Shorts;
- Instagram reels/posts;
- your Page's Facebook videos and reels, including share links.

**Refreshing numbers:** views are fetched when a video is first added. After that they change only when you ask:
- **Refresh** on a video in the Tracking Library updates that video and its history;
- the Dashboard's **Run now** refreshes every tracked video.

Uploading a file again normally keeps already-tracked videos unchanged. Exception: validated owner metadata fills a missing Instagram owner, and eligible failed/unsupported/partial videos are queued for recovery. Paused/completed videos remain stopped. Original upload membership and history stay intact. The automatic daily refresh is **off**. Setting `VIDEO_TRACKING_AUTO_REFRESH=true` runs it every day at `VIDEO_TRACKING_REFRESH_TIME`, catches up if the backend was off, and retries a failed run after 30 minutes.

**Statuses:**
- **Tracking:** checked daily.
- **Video Down:** a video that was seen before is now missing. It is re-checked daily for `VIDEO_TRACKING_DOWN_RECHECK_DAYS`, then only on Retry.
- **Unsupported:** the official API cannot read the video, with the reason.
- **Failed:** a temporary error, unresolved Instagram owner, or conflicting owner metadata. The reason distinguishes these cases. Owner failures need a valid posting username and Retry; they do not establish that the Reel is unsupported.
- **Paused:** you stopped tracking it.

**Finding a reel's owner:** a bare link (`instagram.com/reel/<code>/`) does not contain an owner. This integration has no supported arbitrary URL/shortcode-to-owner lookup. Business Discovery requires a target username; the app does not use Instagram oEmbed metadata for analytics, private endpoints, or scraping. See Meta's [Business Discovery reference](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/business_discovery/) and [oEmbed limitations](https://developers.facebook.com/docs/instagram-platform/oembed/). Direct re-verification of these documentation pages on 21 October 2026 was blocked by Meta HTTP 429 responses; no API version, permissions, or token settings were changed as part of the owner-resolution fix.

The resolution order is:
1. a valid stored owner with previously retrieved metrics (the existing schema's evidence of verification);
2. validated source metadata: a username-bearing Reel URL, `Username`/`Instagram Username`/`Owner Username`, a recognized profile-link column, or an explicit `@handle`/Instagram profile in `Creator Name`;
3. the linked Video Performance Creator's Instagram username/profile URL;
4. stored upload rows associated with the **exact** same Reel shortcode;
5. bounded Business Discovery scans of known readable Instagram Professional accounts.

Excel imports inspect cell values, hyperlink targets, and literal `HYPERLINK("URL", "label")` formulas without executing them. Display names are not treated as usernames. Shortcodes retain their case; usernames are normalized independently. Conflicting import sources are retained as invalid rows with diagnostics. Conflicts with a verified stored owner retain that owner and are logged/shown; conflicting unverified sources fail before making API calls. An explicit edit can correct unverified metadata; original upload values remain available with a superseded note.

Discovery reuses media already read in the batch, follows supported `media.after(cursor)` pagination even when `paging.next` is absent, and stops once every requested shortcode is found. It uses up to `VIDEO_TRACKING_INSTAGRAM_SCAN_PAGES` pages per account (default 4, 50 items each), at most 30 additional accounts and 30 additional media requests per run, all within the shared Meta budget. Retries rotate candidate batches; this rotation is in memory and starts over after a backend restart. Rate-limited work remains in the existing persistent Meta queue.

Only exhausted media lists are remembered as negative owner matches. Scans stopped by page/request limits or after finding other reels remain eligible on Retry. The corrected cache uses `vt_owner_search_v2`, ignoring legacy entries that may have recorded partial scans as complete. After restarting, a Retry can therefore recheck previously cached accounts within the same request budgets. Increase the configured scan window if the reel is older than the posts currently checked.

Results distinguish an exhausted known-account scan from account/page/request limits or API errors. An unresolved owner is **Failed**, with the missing username/remediation in its reason. Authentication and permission errors retain their own reasons; rate limits remain **Pending**. A matching non-video post is **Unsupported**. A previously verified Reel missing after an exhausted owner scan is **Video Down**; a truncated scan is inconclusive. A successful HTTP bulk response confirms work was queued; consult each video's status and saved metrics to confirm retrieval. Missing API view counts remain **Partial**.

A wrong unverified username (for example, a collab participant instead of the original poster) can be corrected only by an exact shortcode match. A Reel outside the known accounts or scan window still needs the posting Professional account's username, or a larger bounded `VIDEO_TRACKING_INSTAGRAM_SCAN_PAGES` window (maximum 10). Global owner discovery is not guaranteed. No database migration or new configuration is required.

To recover upload `68f49e47ea214a6e962a33ef5b43ecb0`, restart the backend, open its videos from Tracking Library > Uploads, and supply each posting account's username for videos **193-202** using Edit, or re-upload the same links with a `Username` column. Ownerless eligible records are enriched and queued without duplicating videos or moving them out of the original upload. If metadata is already available through linked creators, Retry each affected video or select them and Refresh. Check for **Tracking** plus saved views, **Partial** when views are unavailable, or an actionable failure reason. Re-importing alone cannot resolve a bare Reel whose owner remains unknown.

**Meta's hourly limit (large uploads):** Meta allows a limited number of calls per rolling hour for Instagram and Facebook together. Every Graph call, in both modules, first checks three things:
- the app's own count of Meta calls in the last hour, against `META_HOURLY_CALL_BUDGET` (default 180);
- Meta's own usage report from its response headers, against `META_USAGE_STOP_PERCENT` (default 80%);
- whether Meta recently answered "rate limit reached".

When a check fails, no call is sent. The video or creator becomes **Pending – waiting for Meta's hourly limit**, keeps any stored numbers, and continues automatically in the next hour, oldest first. This also works after a restart, because the call log and the queue are kept in the database.

Other rules that apply here:
- Each owner's posts are read once per run, 50 at a time; older pages are read only while a reel is still missing.
- A rate-limited call is never retried immediately.
- YouTube is not affected: videos are still fetched in batches of 50.
- The Tracking Library and Upload & Analyze pages show how much is waiting and when it continues. Each upload shows "X of Y Instagram reels fetched, rest continue automatically after …".

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

**Outdated counts:** the platform APIs answer from several servers that can hold different copies of a count. Seconds apart, YouTube can answer 84,210, then 84,288, then 84,210 again for the same video. The app handles this in three ways:
- **Repeated reads:** YouTube view counts are read `YOUTUBE_VIEW_READS` (3) times per check and the highest is kept. Each extra read costs 1 unit per 50 videos.
- **12-hour rule:** a reading lower than one taken in the last 12 hours is not stored, on any platform; the higher real reading is kept. A real decrease, for example YouTube removing spam views, still shows once it persists beyond 12 hours.
- **Repair at startup:** videos whose current number came from such an outdated count get the higher real reading back. Saved readings are never changed. In **View history** they show greyed out as "ignored".

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

- **Uploaded files and their data are kept forever.** Nothing is deleted automatically: every upload, its rows, tracked videos and their daily history stay in the database and on the website until you delete them yourself. (Setting `UPLOAD_FILE_RETENTION_DAYS` to N > 0 would remove only the stored original file after N days; its rows would still stay.)
- **Logs** go to `backend/logs/creatorintel.log`, rotated daily and kept for `LOG_RETENTION_DAYS`. Keys, tokens and database passwords are redacted.
- **Remote database speed:**
  - Each round trip to Neon adds latency, about 300 ms from India to us-east-2.
  - The app keeps a connection pool, keeps Neon awake during `DB_KEEP_WARM_HOURS`, and shows previously loaded data instantly.
  - For the best speed, create the Neon project in **AWS Asia Pacific – Singapore**.

## 13. Troubleshooting

If Apify uploads fail immediately with a database-preparation error, run `python -m alembic upgrade head` from `backend`, then restart and Retry the failed records. Migration `0007_apify_work_finished_at` repairs installations that had already applied an earlier `0006` without the work table's `finished_at` column. It is safe when that column already exists and preserves videos, remote run IDs and usage reservations. A `201 Created` upload response only confirms the file was accepted, not that an Actor ran. Database errors now log the exception type without logging SQL, parameters or credentials.

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
