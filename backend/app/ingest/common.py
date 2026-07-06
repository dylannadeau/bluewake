"""Shared ingestion plumbing: every source logs a source_ingest_runs row so
screenings can prove which data (and how fresh) they were based on."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone

from app.db import SessionLocal
from app.models import SourceIngestRun


@asynccontextmanager
async def ingest_run(source: str):
    """Bookkeeping context: opens a run row up front, closes it with ok/error.

    Uses its own session so a failed ingest transaction can't roll back the
    audit record. The caller mutates run.record_count / run.source_version /
    run.details before exiting.
    """
    async with SessionLocal() as session:
        run = SourceIngestRun(source=source, status="running")
        session.add(run)
        await session.commit()
        run_id = run.id

    run = SourceIngestRun(source=source)  # detached carrier for caller updates
    run.id = run_id
    try:
        yield run
        status = "ok"
    except Exception as exc:
        status = "error"
        run.details = {**(run.details or {}), "error": repr(exc)}
        raise
    finally:
        async with SessionLocal() as session:
            db_run = await session.get(SourceIngestRun, run_id)
            db_run.finished_at = datetime.now(timezone.utc)
            db_run.status = status
            db_run.record_count = run.record_count
            db_run.source_version = run.source_version
            if run.details:
                db_run.details = run.details
            await session.commit()
