"""Retrieval of immutable screening records (JSON + PDF)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Screening

router = APIRouter(prefix="/screenings", tags=["screenings"])


async def _get_screening(screening_id: str, session: AsyncSession) -> Screening:
    try:
        key = uuid.UUID(screening_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid screening id")
    screening = await session.get(Screening, key)
    if screening is None:
        raise HTTPException(status_code=404, detail="screening not found")
    return screening


@router.get("/{screening_id}")
async def get_screening(screening_id: str, session: AsyncSession = Depends(get_session)):
    screening = await _get_screening(screening_id, session)
    return {
        "id": str(screening.id),
        "imo": screening.imo,
        "screened_at": screening.screened_at.isoformat(),
        "overall_status": screening.overall_status,
        "report_sha256": screening.report_sha256,
        "report": screening.report_json,
    }


@router.get("/{screening_id}/pdf")
async def get_screening_pdf(screening_id: str, session: AsyncSession = Depends(get_session)):
    screening = await _get_screening(screening_id, session)
    if not screening.report_pdf_path:
        raise HTTPException(status_code=404, detail="PDF was not generated for this screening")
    return FileResponse(
        screening.report_pdf_path,
        media_type="application/pdf",
        filename=f"screening-{screening.imo}-{screening_id}.pdf",
    )
