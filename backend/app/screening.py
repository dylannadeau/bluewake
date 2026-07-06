"""Screening orchestrator: refresh sources (best effort) → load context →
evaluate rules → persist the immutable screening record → write PDF/JSON.

The screening row is the audit trail; it freezes the ruleset, the as-of state
of every data source, and the full report JSON (SHA-256 hashed). PDF and JSON
files land in settings.report_storage_dir keyed by screening id.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.ingest import gfw
from app.models import (
    AisPosition,
    Screening,
    ScreeningFinding,
    SdnEntity,
    SourceIngestRun,
    Vessel,
)
from app.reports.generator import build_report, render_pdf, report_hash
from app.resolution.resolver import is_valid_imo
from app.rules.engine import evaluate, load_context

logger = logging.getLogger("bluewake.screening")


async def _data_sources_snapshot(session: AsyncSession, imo: str) -> dict:
    """As-of state of every source, embedded in the report for defensibility."""
    snapshot: dict[str, dict] = {}
    rows = await session.execute(
        select(
            SourceIngestRun.source,
            func.max(SourceIngestRun.finished_at),
            func.max(SourceIngestRun.source_version),
        )
        .where(SourceIngestRun.status == "ok")
        .group_by(SourceIngestRun.source)
    )
    for source, finished_at, version in rows:
        snapshot[source] = {
            "status": "ok",
            "as_of": finished_at.isoformat() if finished_at else None,
            "version": version,
        }

    sdn_date = await session.scalar(select(func.max(SdnEntity.list_date)))
    snapshot.setdefault("ofac_sdn", {"status": "never_ingested", "as_of": None, "version": None})
    if sdn_date:
        snapshot["ofac_sdn"]["version"] = sdn_date.isoformat()

    last_pos = await session.scalar(
        select(func.max(AisPosition.ts)).where(AisPosition.imo == imo)
    )
    snapshot["ais_positions"] = {
        "status": "ok" if last_pos else "no_positions_for_vessel",
        "as_of": last_pos.isoformat() if last_pos else None,
        "version": None,
    }
    return snapshot


def _vessel_profile(ctx) -> dict:
    vessel = ctx.vessel
    return {
        "name": vessel.name if vessel else None,
        "callsign": vessel.callsign if vessel else None,
        "flag": vessel.flag if vessel else None,
        "vessel_type": vessel.vessel_type if vessel else None,
        "mmsis": sorted({i.mmsi for i in ctx.identities if i.mmsi}),
        "names": sorted({i.name for i in ctx.identities if i.name}),
        "flags": [f.flag for f in ctx.flag_history],
        "owners": [
            {"name": o.owner_name, "role": o.role, "country": o.country, "source": o.source}
            for o in ctx.ownership
        ],
        "first_seen_at": vessel.first_seen_at.isoformat() if vessel and vessel.first_seen_at else None,
        "last_seen_at": vessel.last_seen_at.isoformat() if vessel and vessel.last_seen_at else None,
    }


def _overall_status(findings) -> str:
    severities = {f.severity for f in findings}
    if "alert" in severities:
        return "findings"
    if "warning" in severities:
        return "review_required"
    return "no_findings"


async def run_screening(
    session: AsyncSession,
    imo: str,
    *,
    requested_by: str | None = None,
    trigger: str = "manual",
    regimes: list[str] | None = None,
    refresh_gfw: bool = True,
) -> Screening:
    if not is_valid_imo(imo):
        raise ValueError(f"IMO {imo!r} fails check-digit validation")

    # Ensure the vessel exists so history starts accumulating from first lookup.
    vessel = await session.get(Vessel, imo)
    if vessel is None:
        vessel = Vessel(imo=imo)
        session.add(vessel)
        await session.flush()

    gfw_status = {"status": "skipped", "as_of": None, "version": None}
    if refresh_gfw and settings.gfw_api_token:
        try:
            summary = await gfw.sync_vessel(session, imo)
            gfw_status = {
                "status": summary.get("gfw", "ok"),
                "as_of": datetime.now(timezone.utc).isoformat(),
                "version": None,
            }
        except Exception as exc:
            # A source being down must not block the screening; it is recorded
            # in the report so the reader knows what the screening stood on.
            logger.warning("GFW refresh failed for %s: %r", imo, exc)
            gfw_status = {"status": f"unavailable: {exc}", "as_of": None, "version": None}

    ctx = await load_context(session, imo)
    findings, ruleset = await evaluate(session, ctx, regimes)
    overall = _overall_status(findings)

    screening_id = uuid.uuid4()
    screened_at = datetime.now(timezone.utc)
    data_sources = await _data_sources_snapshot(session, imo)
    data_sources["gfw_live_refresh"] = gfw_status

    findings_payload = [
        {"rule_id": f.rule_id, "severity": f.severity, "summary": f.summary,
         "evidence": f.evidence}
        for f in findings
    ]
    report = build_report(
        screening_id=str(screening_id),
        imo=imo,
        screened_at=screened_at.isoformat(),
        trigger=trigger,
        requested_by=requested_by,
        vessel_profile=_vessel_profile(ctx),
        ruleset=ruleset,
        data_sources=data_sources,
        findings=findings_payload,
        overall_status=overall,
    )
    sha256 = report_hash(report)

    # Write report files before the DB row: the row is immutable, so the pdf
    # path must be final at insert time.
    storage = Path(settings.report_storage_dir)
    storage.mkdir(parents=True, exist_ok=True)
    json_path = storage / f"{screening_id}.json"
    pdf_path = storage / f"{screening_id}.pdf"
    json_path.write_text(json.dumps(report, indent=2, default=str))
    try:
        pdf_path.write_bytes(render_pdf(report, sha256))
        stored_pdf_path = str(pdf_path)
    except Exception as exc:
        logger.error("PDF render failed for %s: %r (JSON report intact)", screening_id, exc)
        stored_pdf_path = None

    screening = Screening(
        id=screening_id,
        imo=imo,
        screened_at=screened_at,
        requested_by=requested_by,
        trigger=trigger,
        ruleset={"rules": ruleset, "regimes": regimes or ["OFAC"]},
        data_sources=data_sources,
        overall_status=overall,
        report_json=report,
        report_pdf_path=stored_pdf_path,
        report_sha256=sha256,
    )
    session.add(screening)
    for f in findings:
        session.add(
            ScreeningFinding(
                screening_id=screening_id,
                rule_id=f.rule_id,
                severity=f.severity,
                summary=f.summary,
                evidence=f.evidence,
            )
        )
    await session.commit()
    return screening
