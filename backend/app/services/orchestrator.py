"""EnrichmentOrchestrator - runs the per-creator pipeline.

    URL resolver -> YouTube / Instagram collector -> MetricsProcessor
    -> ContentAnalyzer (genre / language / sentiment) -> ResultValidator -> DB

Every creator is processed independently: one failure never stops the batch.
"""

import asyncio
import logging
import time
from collections import Counter
from collections.abc import Coroutine
from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.agents.content_analysis_agent import ContentAnalyzer, build_content_sample, resolve_display_values
from app.agents.instagram_agent import InstagramCollector
from app.agents.metrics_agent import MetricsProcessor, MetricsResult
from app.agents.platform_agent import PlatformResolver
from app.agents.validation_agent import ResultValidator
from app.agents.youtube_agent import YouTubeCollector
from app.config.settings import Settings, get_settings
from app.models.db import session_scope
from app.models.entities import Creator, CreatorStatus, Upload, UploadItem, UploadStatus
from app.schemas.platform import ChannelProfile, CollectionError
from app.services.upload_service import upload_status_counts
from app.utils.logging import log_event
from app.utils.time import as_utc, utcnow

logger = logging.getLogger("creatorintel.orchestrator")

# Fields rewritten on every full enrichment (so stale values are never mixed with new ones)
_ENRICHED_FIELDS = (
    "platform_id", "platform_display_name", "account_access", "followers_count", "subscriber_count",
    "average_views", "average_views_sample_count", "median_views", "top_video_title", "top_video_url",
    "top_video_views", "engagement_rate", "engagement_rate_basis", "engagement_sample_count",
)
_AI_FIELDS = (
    "genre", "sub_genre", "genre_needs_review", "language", "secondary_language", "sentiment",
    "sentiment_score", "genre_confidence", "language_confidence", "sentiment_confidence", "evidence_topics",
)


def recompute_upload_counts(db, upload: Upload) -> None:
    counts = upload_status_counts(db, [upload.id]).get(upload.id, Counter())
    upload.total_rows = sum(counts.values())
    upload.partial_rows = counts[CreatorStatus.PARTIAL]
    upload.successful_rows = counts[CreatorStatus.COMPLETED] + upload.partial_rows
    upload.failed_rows = counts[CreatorStatus.FAILED]


def _status_for(values: dict, ai_ok: bool) -> str:
    audience = values.get("subscriber_count") if values.get("subscriber_count") is not None else values.get("followers_count")
    core_missing = audience is None or values.get("average_views") is None or values.get("engagement_rate") is None
    return CreatorStatus.PARTIAL if core_missing or not ai_ok else CreatorStatus.COMPLETED


class EnrichmentOrchestrator:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        youtube: YouTubeCollector | None = None,
        instagram: InstagramCollector | None = None,
        analyzer: ContentAnalyzer | None = None,
    ):
        self.settings = settings or get_settings()
        self.resolver = PlatformResolver()
        self.youtube = youtube or YouTubeCollector(self.settings)
        self.instagram = instagram or InstagramCollector(self.settings)
        self.metrics = MetricsProcessor(self.settings.average_views_sample_size)
        self.analyzer = analyzer or ContentAnalyzer(settings=self.settings)
        self.validator = ResultValidator()
        self._semaphore = asyncio.Semaphore(self.settings.max_concurrent_creators)
        self._locks: dict[int, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task] = set()

    # ---------------------------------------------------------------- scheduling
    def _schedule(self, coro: Coroutine[Any, Any, Any], name: str) -> None:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def start_upload(self, upload_id: str) -> None:
        self._schedule(self.process_upload(upload_id), f"upload-{upload_id}")

    def start_retry(self, creator_id: int) -> None:
        self._schedule(self._limited(self.enrich_creator(creator_id)), f"retry-{creator_id}")

    def start_reanalyze(self, creator_id: int) -> None:
        self._schedule(self._limited(self.reanalyze_creator(creator_id)), f"reanalyze-{creator_id}")

    async def _limited(self, coro: Coroutine[Any, Any, Any]) -> Any:
        async with self._semaphore:
            return await coro

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    def resume_incomplete(self) -> None:
        """After a restart, continue uploads/creators that were still in progress."""
        with session_scope() as db:
            upload_ids = list(db.scalars(select(Upload.id).where(Upload.status == UploadStatus.PROCESSING)))
            in_uploads = set(
                db.scalars(select(UploadItem.creator_id).where(UploadItem.upload_id.in_(upload_ids)))
            ) if upload_ids else set()
            orphan_ids = [
                cid for cid in db.scalars(select(Creator.id).where(Creator.status.in_(CreatorStatus.ACTIVE)))
                if cid not in in_uploads
            ]
        for upload_id in upload_ids:
            log_event(logger, logging.INFO, "resume_upload", upload_id=upload_id)
            self.start_upload(upload_id)
        for creator_id in orphan_ids:
            self.start_retry(creator_id)

    # ------------------------------------------------------------------- uploads
    async def process_upload(self, upload_id: str) -> None:
        started = time.perf_counter()
        # All DB work runs in worker threads so remote-database latency never blocks the event loop.
        def begin() -> tuple[datetime | None, list[int]] | None:
            with session_scope() as db:
                upload = db.get(Upload, upload_id)
                if upload is None:
                    return None
                upload.status = UploadStatus.PROCESSING
                creator_ids = list(
                    db.scalars(
                        select(UploadItem.creator_id)
                        .join(Creator, Creator.id == UploadItem.creator_id)
                        .where(UploadItem.upload_id == upload_id, Creator.status.in_(CreatorStatus.ACTIVE))
                        .order_by(UploadItem.position)
                    )
                )
                return as_utc(upload.created_at), creator_ids

        def finish(status: str, error: str | None = None) -> None:
            with session_scope() as db:
                upload = db.get(Upload, upload_id)
                if upload is None:
                    return
                recompute_upload_counts(db, upload)
                upload.status = status
                upload.error_message = error
                upload.completed_at = utcnow()
                log_event(
                    logger, logging.INFO, "upload_processing_finished",
                    upload_id=upload_id, status=status, total=upload.total_rows, successful=upload.successful_rows,
                    partial=upload.partial_rows, failed=upload.failed_rows,
                    duration_s=f"{time.perf_counter() - started:.1f}",
                )

        try:
            started_state = await asyncio.to_thread(begin)
            if started_state is None:
                return
            fresh_after, creator_ids = started_state
            log_event(logger, logging.INFO, "upload_processing_started", upload_id=upload_id, queued=len(creator_ids))

            async def run(creator_id: int) -> None:
                async with self._semaphore:
                    await self.enrich_creator(creator_id, upload_id=upload_id, fresh_after=fresh_after)

            await asyncio.gather(*(run(cid) for cid in creator_ids))
            await asyncio.to_thread(finish, UploadStatus.COMPLETED)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("upload_processing_crashed upload_id=%s", upload_id)
            await asyncio.to_thread(
                finish, UploadStatus.FAILED, "Processing stopped unexpectedly. Retry failed creators individually."
            )

    # ------------------------------------------------------------------ creators
    def _lock_for(self, creator_id: int) -> asyncio.Lock:
        return self._locks.setdefault(creator_id, asyncio.Lock())

    def _collector_for(self, platform: str):
        return self.youtube if platform == "youtube" else self.instagram

    async def enrich_creator(
        self, creator_id: int, *, upload_id: str | None = None, fresh_after: datetime | None = None
    ) -> None:
        def claim() -> tuple[str, str] | None:
            with session_scope() as db:
                creator = db.get(Creator, creator_id)
                if creator is None:
                    return None
                fetched = as_utc(creator.data_fetched_at)
                if fresh_after and fetched and fetched >= fresh_after and creator.status in CreatorStatus.FINAL:
                    return None  # already refreshed by a concurrent job for this upload
                if creator.platform not in ("youtube", "instagram"):
                    creator.status = CreatorStatus.FAILED
                    creator.error_message = creator.error_message or "Unsupported or invalid channel link"
                    return None
                creator.status = CreatorStatus.PROCESSING
                return creator.platform, creator.channel_url

        async with self._lock_for(creator_id):
            started = time.perf_counter()
            claimed = await asyncio.to_thread(claim)
            if claimed is None:
                return
            platform, channel_url = claimed

            ctx = {"upload_id": upload_id, "creator_id": creator_id, "platform": platform}
            try:
                await self._run_pipeline(creator_id, platform, channel_url, ctx)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("creator_pipeline_crashed creator_id=%s", creator_id)
                await asyncio.to_thread(self._save_failure, creator_id, "Unexpected error while processing this creator", None)
            finally:
                log_event(
                    logger, logging.INFO, "creator_finished", **ctx,
                    duration_s=f"{time.perf_counter() - started:.2f}",
                )

    async def _run_pipeline(self, creator_id: int, platform: str, channel_url: str, ctx: dict) -> None:
        parsed = self.resolver.resolve(channel_url)
        if not parsed.is_supported:
            await asyncio.to_thread(self._save_failure, creator_id, parsed.error or "Channel link is not valid", None)
            return

        log_event(logger, logging.INFO, "stage_collect", **ctx)
        try:
            profile = await self._collector_for(platform).collect(parsed)
        except CollectionError as exc:
            log_event(logger, logging.WARNING, "collect_failed", **ctx, code=exc.code, reason=exc.message)
            await asyncio.to_thread(self._save_failure, creator_id, exc.message, exc.account_access)
            return

        metrics = self.metrics.compute(profile)
        sample = build_content_sample(profile)

        log_event(logger, logging.INFO, "stage_ai_analysis", **ctx)
        analysis = await self.analyzer.analyze(sample, creator_id=creator_id)

        values = self._assemble(channel_url, profile, metrics)
        issues = [*profile.notes, *metrics.notes, *analysis.issues]
        provenance = dict(metrics.provenance)
        if analysis.result is not None:
            ai_values = resolve_display_values(analysis.result, self.settings.ai_min_confidence)
            values.update(ai_values)
            for key in ("genre", "sub_genre", "language", "sentiment"):
                provenance[key] = f"llm_analysis:groq:{self.settings.groq_model}"
        else:
            values.update({key: None for key in _AI_FIELDS})
            values["genre_needs_review"] = False
            issues.append(analysis.error or "AI analysis unavailable")

        outcome = self.validator.validate(platform, values)
        issues.extend(outcome.issues)
        final = outcome.values
        status = _status_for(final, analysis.result is not None)

        def save() -> None:
            with session_scope() as db:
                creator = db.get(Creator, creator_id)
                if creator is None:
                    return
                for key in (*_ENRICHED_FIELDS, *_AI_FIELDS):
                    setattr(creator, key, final.get(key))
                if profile.resolved_channel_url:
                    creator.channel_url = profile.resolved_channel_url
                creator.provenance = provenance
                creator.content_sample = sample
                creator.issues = issues or None
                creator.status = status
                creator.error_message = None if status == CreatorStatus.COMPLETED else "; ".join(issues[:3]) or None
                creator.data_fetched_at = utcnow()
                creator.analyzed_at = utcnow() if analysis.result is not None else None

        await asyncio.to_thread(save)
        log_event(logger, logging.INFO, "creator_saved", **ctx, status=status, issues=len(issues))

    def _assemble(self, channel_url: str, profile: ChannelProfile, metrics: MetricsResult) -> dict:
        top = metrics.top_video
        return {
            "channel_url": channel_url,
            "platform_id": profile.platform_id,
            "platform_display_name": profile.display_name,
            "account_access": profile.account_access,
            "subscriber_count": metrics.audience_count if profile.platform == "youtube" else None,
            "followers_count": metrics.audience_count if profile.platform == "instagram" else None,
            "average_views": metrics.average_views,
            "average_views_sample_count": metrics.average_views_sample_count,
            "median_views": metrics.median_views,
            "top_video_title": top.title if top else None,
            "top_video_url": top.url if top else None,
            "top_video_views": top.views if top else None,
            "engagement_rate": metrics.engagement_rate,
            "engagement_rate_basis": metrics.engagement_rate_basis,
            "engagement_sample_count": metrics.engagement_sample_count,
        }

    def _save_failure(self, creator_id: int, message: str, account_access: str | None) -> None:
        with session_scope() as db:
            creator = db.get(Creator, creator_id)
            if creator is None:
                return
            for key in (*_ENRICHED_FIELDS, *_AI_FIELDS):
                setattr(creator, key, None)
            creator.genre_needs_review = False
            creator.account_access = account_access
            creator.status = CreatorStatus.FAILED
            creator.error_message = message
            creator.issues = [message]
            creator.provenance = None
            creator.content_sample = None
            creator.data_fetched_at = utcnow()
            creator.analyzed_at = None

    # ----------------------------------------------------------------- reanalyze
    async def reanalyze_creator(self, creator_id: int) -> None:
        """Re-run only the AI analysis on the stored content sample."""
        def claim() -> tuple[dict | None, str] | None:
            with session_scope() as db:
                creator = db.get(Creator, creator_id)
                if creator is None:
                    return None
                previous = creator.status
                creator.status = CreatorStatus.PROCESSING
                return creator.content_sample, previous

        def restore_status(previous: str) -> None:
            with session_scope() as db:
                creator = db.get(Creator, creator_id)
                if creator is not None:
                    creator.status = previous

        async with self._lock_for(creator_id):
            claimed = await asyncio.to_thread(claim)
        if claimed is None:
            return
        sample, previous_status = claimed

        if not sample:
            # Nothing collected yet - a full enrichment is required.
            await asyncio.to_thread(restore_status, previous_status)
            await self.enrich_creator(creator_id)
            return

        started = time.perf_counter()
        try:
            analysis = await self.analyzer.analyze(sample, creator_id=creator_id)
        except Exception:
            logger.exception("reanalyze_crashed creator_id=%s", creator_id)
            analysis = None

        await asyncio.to_thread(self._save_reanalysis, creator_id, analysis)
        log_event(
            logger, logging.INFO, "creator_reanalyzed", creator_id=creator_id,
            duration_s=f"{time.perf_counter() - started:.2f}",
        )

    def _save_reanalysis(self, creator_id: int, analysis) -> None:
        with session_scope() as db:
            creator = db.get(Creator, creator_id)
            if creator is None:
                return
            issues = [i for i in (creator.issues or []) if not i.startswith(("AI analysis", "Language confidence", "Sentiment confidence", "Re-analysis"))]
            if analysis is not None and analysis.result is not None:
                ai_values = resolve_display_values(analysis.result, self.settings.ai_min_confidence)
                current = {key: getattr(creator, key) for key in _ENRICHED_FIELDS}
                outcome = self.validator.validate(creator.platform, {**current, "channel_url": creator.channel_url, **ai_values})
                for key in _AI_FIELDS:
                    setattr(creator, key, outcome.values.get(key))
                provenance = dict(creator.provenance or {})
                for key in ("genre", "sub_genre", "language", "sentiment"):
                    provenance[key] = f"llm_analysis:groq:{self.settings.groq_model}"
                creator.provenance = provenance
                creator.analyzed_at = utcnow()
                issues.extend(analysis.issues)
                ai_ok = True
            else:
                reason = analysis.error if analysis is not None else "unexpected error"
                issues.append(f"Re-analysis failed: {reason}")
                ai_ok = creator.analyzed_at is not None  # previous real AI values are kept
            values = {key: getattr(creator, key) for key in _ENRICHED_FIELDS}
            creator.status = _status_for(values, ai_ok)
            creator.issues = issues or None
            creator.error_message = None if creator.status == CreatorStatus.COMPLETED else "; ".join(issues[:3]) or None
