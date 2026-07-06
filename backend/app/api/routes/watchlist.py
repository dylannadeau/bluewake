"""Watchlist management: vessels re-screened on a schedule with email alerts."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Vessel, WatchlistAlert, WatchlistEntry
from app.resolution.resolver import is_valid_imo

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


class WatchlistCreate(BaseModel):
    imo: str
    alert_email: EmailStr
    label: str | None = None
    rescreen_interval_hours: int = Field(default=24, ge=1, le=24 * 30)


@router.get("")
async def list_watchlist(session: AsyncSession = Depends(get_session)):
    entries = list(
        await session.scalars(
            select(WatchlistEntry).where(WatchlistEntry.active.is_(True))
            .order_by(WatchlistEntry.created_at.desc())
        )
    )
    return [
        {
            "id": e.id, "imo": e.imo, "label": e.label, "alert_email": e.alert_email,
            "rescreen_interval_hours": e.rescreen_interval_hours,
            "created_at": e.created_at.isoformat(),
        }
        for e in entries
    ]


@router.post("", status_code=201)
async def add_entry(body: WatchlistCreate, session: AsyncSession = Depends(get_session)):
    if not is_valid_imo(body.imo):
        raise HTTPException(status_code=422, detail="invalid IMO number (check digit failed)")
    if await session.get(Vessel, body.imo) is None:
        session.add(Vessel(imo=body.imo))
        await session.flush()
    existing = await session.scalar(
        select(WatchlistEntry).where(
            WatchlistEntry.imo == body.imo,
            WatchlistEntry.alert_email == body.alert_email,
        )
    )
    if existing:
        existing.active = True
        existing.label = body.label or existing.label
        existing.rescreen_interval_hours = body.rescreen_interval_hours
        entry = existing
    else:
        entry = WatchlistEntry(
            imo=body.imo, alert_email=body.alert_email, label=body.label,
            rescreen_interval_hours=body.rescreen_interval_hours,
        )
        session.add(entry)
    await session.commit()
    return {"id": entry.id, "imo": entry.imo}


@router.delete("/{entry_id}", status_code=204)
async def remove_entry(entry_id: int, session: AsyncSession = Depends(get_session)):
    entry = await session.get(WatchlistEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="watchlist entry not found")
    entry.active = False  # soft delete: alert history stays attached
    await session.commit()


@router.get("/{entry_id}/alerts")
async def entry_alerts(entry_id: int, session: AsyncSession = Depends(get_session)):
    alerts = list(
        await session.scalars(
            select(WatchlistAlert).where(WatchlistAlert.watchlist_id == entry_id)
            .order_by(WatchlistAlert.id.desc()).limit(50)
        )
    )
    return [
        {
            "id": a.id, "screening_id": str(a.screening_id), "channel": a.channel,
            "status": a.status, "sent_at": a.sent_at.isoformat() if a.sent_at else None,
        }
        for a in alerts
    ]
