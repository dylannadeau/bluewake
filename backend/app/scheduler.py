"""Scheduled re-screening of watchlisted vessels.

An APScheduler interval job calls rescreen_due() which finds active watchlist
entries whose latest screening is older than their rescreen interval, runs a
fresh screening (trigger='watchlist'), and raises an email alert when the
outcome changed (see alerts.should_alert). Every alert attempt is recorded in
watchlist_alerts regardless of delivery outcome.
"""

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.alerts import send_alert, should_alert
from app.config import settings
from app.db import SessionLocal
from app.models import Screening, WatchlistAlert, WatchlistEntry
from app.screening import run_screening

logger = logging.getLogger("bluewake.scheduler")


async def rescreen_due() -> list[dict]:
    """One scheduler tick. Returns a summary of actions (useful in tests/logs)."""
    actions: list[dict] = []
    async with SessionLocal() as session:
        entries = list(
            await session.scalars(
                select(WatchlistEntry).where(WatchlistEntry.active.is_(True))
            )
        )

    now = datetime.now(timezone.utc)
    for entry in entries:
        async with SessionLocal() as session:
            previous = (
                await session.execute(
                    select(Screening.screened_at, Screening.overall_status)
                    .where(Screening.imo == entry.imo)
                    .order_by(Screening.screened_at.desc())
                    .limit(1)
                )
            ).first()
            previous_status = previous.overall_status if previous else None
            due = previous is None or previous.screened_at <= now - timedelta(
                hours=entry.rescreen_interval_hours
            )
            if not due:
                continue

            try:
                screening = await run_screening(
                    session, entry.imo, trigger="watchlist",
                    requested_by=f"watchlist:{entry.id}",
                )
            except Exception:
                logger.exception("re-screening failed for IMO %s", entry.imo)
                actions.append({"imo": entry.imo, "action": "error"})
                continue

            action = {"imo": entry.imo, "action": "screened",
                      "status": screening.overall_status, "alerted": False}
            if should_alert(previous_status, screening.overall_status):
                status = await send_alert(entry, screening, previous_status)
                session.add(
                    WatchlistAlert(
                        watchlist_id=entry.id,
                        screening_id=screening.id,
                        channel="email",
                        sent_at=datetime.now(timezone.utc) if status == "sent" else None,
                        status=status,
                    )
                )
                await session.commit()
                action["alerted"] = True
                action["alert_status"] = status
            actions.append(action)

    if actions:
        logger.info("watchlist tick: %s", actions)
    return actions


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        rescreen_due,
        "interval",
        minutes=settings.watchlist_check_interval_minutes,
        id="watchlist_rescreen",
        max_instances=1,
        coalesce=True,
    )
    return scheduler
