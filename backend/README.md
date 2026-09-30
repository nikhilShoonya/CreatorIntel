# CreatorIntel – Backend

FastAPI service that ingests creator spreadsheets, enriches each creator from official platform APIs, runs Groq LLM content analysis and stores the results in PostgreSQL (Neon) or SQLite.

Setup, API keys and troubleshooting are in the [project README](../README.md).

```bash
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload     # http://localhost:8000, docs at /docs
python -m pytest -q               # after: pip install -r requirements-dev.txt
```

## Pipeline

```text
POST /api/uploads
  └─ FileIngestionAgent     validate type/size/magic bytes, map headers, parse rows, de-duplicate
      └─ PlatformResolver   URL → youtube | instagram | unsupported | invalid (never from the name)
          └─ EnrichmentOrchestrator (background, per creator, bounded concurrency)
              ├─ YouTubeCollector     channels.list → uploads playlist → videos.list
              ├─ InstagramCollector   Graph API Business Discovery (Professional accounts only)
              ├─ MetricsProcessor     average/median views, top video, engagement rate
              ├─ ContentAnalyzer      one Groq structured-output request → genre / language / sentiment
              └─ ResultValidator      taxonomy, ranges, URLs; invalid → unavailable (never repaired)
```

Each creator gets a status of `Pending → Processing → Completed | Partial | Failed`, plus an error message, a list of issues, and provenance for every field (for example `average_views → calculated_from_10_videos`, `genre → llm_analysis:groq:openai/gpt-oss-20b`). One failure never stops the batch. After a restart, interrupted uploads resume automatically.

## Layout

```text
app/
├── main.py                 FastAPI app, CORS, lifespan (DB init, resume jobs)
├── api/                    uploads, creators (list/detail/retry/reanalyze/facets), exports, system
├── agents/                 ingestion, platform, youtube, instagram, metrics, content_analysis, validation
├── services/               orchestrator, http_client (timeouts/retries/backoff), llm_client (Groq), exports, queries
├── models/                 SQLAlchemy engine + Creator / Upload / UploadItem
├── schemas/                API models, AI output contract, taxonomy, platform dataclasses
├── utils/                  URL parser, text helpers, redacting logger, time
└── config/settings.py      environment configuration
tests/                      unit + end-to-end API tests (external HTTP stubbed)
samples/                    example input spreadsheet
```

## API

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/uploads` | Upload .xlsx/.xls/.csv (multipart `file`) and start processing |
| GET | `/api/uploads` | Upload history |
| GET | `/api/uploads/{id}` | Progress and per-creator status |
| GET | `/api/creators` | Paginated list: `q, platform, genre, language, sentiment, status, upload_id, sort_by, sort_dir, page, page_size` |
| GET | `/api/creators/facets` | Distinct filter values |
| GET | `/api/creators/{id}` | Full detail, including confidences and provenance |
| POST | `/api/creators/{id}/retry` | Re-run the full pipeline for this creator only |
| POST | `/api/creators/{id}/reanalyze` | Re-run only the AI analysis, using the stored content sample |
| GET | `/api/exports/excel`, `/api/exports/csv` | Export with the same filters as the list |
| GET | `/api/config/status` | Which integrations are configured (booleans only) |

## Metric definitions

- **Average views**: mean view count of the latest N (default 10) videos/reels that have a view count. Live/upcoming streams are excluded. N/A if none.
- **Top performing video**: highest view count among the recent videos/reels fetched (default 20).
- **Engagement rate**: mean of per-item `(likes + comments) / views × 100` over the same sample. For Instagram only, when no view counts exist, it uses `(likes + comments) / followers × 100` and stores `engagement_rate_basis = "followers"`. The two bases are never mixed.
