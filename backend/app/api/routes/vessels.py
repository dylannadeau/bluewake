"""Vessel lookup + screening trigger endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Screening, Vessel
from app.resolution.resolver import is_valid_imo
from app.rules.engine import load_context
from app.screening import _vessel_profile, run_screening

router = APIRouter(prefix="/vessels", tags=["vessels"])


class ScreeningRequest(BaseModel):
    requested_by: str | None = None
    regimes: list[str] = ["OFAC"]
    refresh_gfw: bool = True


def _require_valid_imo(imo: str) -> None:
    if not is_valid_imo(imo):
        raise HTTPException(status_code=422, detail="invalid IMO number (check digit failed)")


@router.get("/{imo}")
async def get_vessel(imo: str, session: AsyncSession = Depends(get_session)):
    _require_valid_imo(imo)
    vessel = await session.get(Vessel, imo)
    if vessel is None:
        raise HTTPException(status_code=404, detail="vessel not yet known; run a screening to create it")
    ctx = await load_context(session, imo)
    screenings = (
        await session.execute(
            select(Screening.id, Screening.screened_at, Screening.overall_status)
            .where(Screening.imo == imo)
            .order_by(Screening.screened_at.desc())
            .limit(20)
        )
    ).all()
    return {
        "imo": imo,
        "profile": _vessel_profile(ctx),
        "active_sdn_matches": len(ctx.sdn_matches),
        "gfw_events": len(ctx.gfw_events),
        "screenings": [
            {"id": str(s_id), "screened_at": at.isoformat(), "overall_status": status}
            for s_id, at, status in screenings
        ],
    }


@router.post("/{imo}/screenings", status_code=201)
async def create_screening(
    imo: str, body: ScreeningRequest, session: AsyncSession = Depends(get_session)
):
    _require_valid_imo(imo)
    screening = await run_screening(
        session,
        imo,
        requested_by=body.requested_by,
        trigger="manual",
        regimes=body.regimes,
        refresh_gfw=body.refresh_gfw,
    )
    return {
        "id": str(screening.id),
        "imo": imo,
        "screened_at": screening.screened_at.isoformat(),
        "overall_status": screening.overall_status,
        "report_sha256": screening.report_sha256,
        "report": screening.report_json,
    }
