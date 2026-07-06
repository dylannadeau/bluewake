"""OFAC SDN list ingestion.

Downloads sdn.csv + alt.csv (unauthenticated), versions rows by list publish
date, seeds vessels rows for SDN-listed vessels with an IMO, and recomputes
sdn_matches:

  vessel_imo_exact  — SDN vessel entry's IMO equals a known vessel
  vessel_name_fuzzy — trigram similarity between vessel name and SDN
                      vessel names/aliases
  owner_name_fuzzy  — trigram similarity between recorded owners/operators
                      and SDN individual/entity names/aliases

Matches are additive with review_status='unreviewed' (human-in-the-loop);
matches whose SDN entry drops off the latest list are deactivated, never
deleted, so the audit trail of past exposure is preserved.

Run: python -m app.ingest.ofac_sdn
"""

import asyncio
import csv
import io
import re
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.ingest.common import ingest_run
from app.models import RiskRule, SdnAltName, SdnEntity, Vessel
from app.resolution.resolver import is_valid_imo

# sdn.csv has no header row; column layout per OFAC file spec.
SDN_COLUMNS = [
    "ent_num", "sdn_name", "sdn_type", "program", "title", "call_sign",
    "vess_type", "tonnage", "grt", "vess_flag", "vess_owner", "remarks",
]
ALT_COLUMNS = ["ent_num", "alt_num", "alt_type", "alt_name", "alt_remarks"]

IMO_RE = re.compile(r"IMO\s+(\d{7})")


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return None if value in ("", "-0-") else value


def _split_programs(program: str | None) -> list[str]:
    if not program:
        return []
    # Multiple programs appear as "IRAN] [SDGT"; strip stray brackets.
    parts = re.split(r"\]\s*\[|;", program)
    return [p.strip(" []") for p in parts if p.strip(" []")]


def extract_imo(remarks: str | None) -> str | None:
    if not remarks:
        return None
    m = IMO_RE.search(remarks)
    if m and is_valid_imo(m.group(1)):
        return m.group(1)
    return None


def parse_sdn_csv(content: str) -> list[dict]:
    rows = []
    for record in csv.reader(io.StringIO(content)):
        if len(record) < len(SDN_COLUMNS):
            continue
        row = {col: _clean(val) for col, val in zip(SDN_COLUMNS, record)}
        if not row["ent_num"] or not row["sdn_name"]:
            continue
        rows.append(
            {
                "sdn_uid": int(row["ent_num"]),
                "name": row["sdn_name"],
                "sdn_type": (row["sdn_type"] or "entity").lower(),
                "programs": _split_programs(row["program"]),
                "vessel_imo": extract_imo(row["remarks"]),
                "vessel_callsign": row["call_sign"],
                "vessel_flag": row["vess_flag"],
                "vessel_owner": row["vess_owner"],
                "remarks": row["remarks"],
                "raw": row,
            }
        )
    return rows


def parse_alt_csv(content: str) -> list[dict]:
    rows = []
    for record in csv.reader(io.StringIO(content)):
        if len(record) < len(ALT_COLUMNS):
            continue
        row = {col: _clean(val) for col, val in zip(ALT_COLUMNS, record)}
        if not row["ent_num"] or not row["alt_name"]:
            continue
        rows.append(
            {
                "sdn_uid": int(row["ent_num"]),
                "alt_name": row["alt_name"],
                "alt_type": row["alt_type"],
            }
        )
    return rows


def _list_date_from_response(response: httpx.Response) -> date:
    last_modified = response.headers.get("last-modified")
    if last_modified:
        try:
            return parsedate_to_datetime(last_modified).date()
        except (TypeError, ValueError):
            pass
    return datetime.now(timezone.utc).date()


async def _load_thresholds(session: AsyncSession) -> dict:
    rule = await session.get(RiskRule, "sdn_exposure")
    params = rule.params if rule else {}
    return {
        "name_thr": float(params.get("name_similarity_threshold", 0.72)),
        "owner_thr": float(params.get("owner_similarity_threshold", 0.80)),
    }


async def refresh(session: AsyncSession) -> dict:
    """Fetch latest SDN + alt lists, upsert versioned rows, recompute matches."""
    async with httpx.AsyncClient(timeout=120) as client:
        sdn_resp = await client.get(settings.ofac_sdn_url)
        sdn_resp.raise_for_status()
        alt_resp = await client.get(settings.ofac_alt_url)
        alt_resp.raise_for_status()

    list_date = _list_date_from_response(sdn_resp)
    entities = parse_sdn_csv(sdn_resp.text)
    alt_names = parse_alt_csv(alt_resp.text)

    for entity in entities:
        await session.execute(
            pg_insert(SdnEntity)
            .values(list_date=list_date, **entity)
            .on_conflict_do_nothing(index_elements=["sdn_uid", "list_date"])
        )

    # alt table has no natural key; replace this list_date's rows wholesale.
    await session.execute(
        text("DELETE FROM sdn_alt_names WHERE list_date = :d"), {"d": list_date}
    )
    for alt in alt_names:
        session.add(SdnAltName(list_date=list_date, **alt))

    # Seed vessels rows for SDN-listed vessels so they're tracked from day one.
    seeded = 0
    for entity in entities:
        if entity["sdn_type"] == "vessel" and entity["vessel_imo"]:
            result = await session.execute(
                pg_insert(Vessel)
                .values(
                    imo=entity["vessel_imo"],
                    name=entity["name"],
                    callsign=entity["vessel_callsign"],
                )
                .on_conflict_do_nothing(index_elements=["imo"])
            )
            seeded += result.rowcount or 0

    await session.flush()
    match_counts = await recompute_matches(session, list_date)
    await session.commit()

    return {
        "list_date": list_date.isoformat(),
        "entities": len(entities),
        "alt_names": len(alt_names),
        "vessels_seeded": seeded,
        **match_counts,
    }


async def recompute_matches(session: AsyncSession, list_date: date) -> dict:
    thresholds = await _load_thresholds(session)
    params = {"d": list_date, **thresholds}
    counts = {}

    result = await session.execute(
        text("""
        INSERT INTO sdn_matches (imo, sdn_uid, match_type, score, matched_value, sdn_value, list_date)
        SELECT v.imo, s.sdn_uid, 'vessel_imo_exact', 1.0, v.imo, s.name, s.list_date
        FROM sdn_entities s
        JOIN vessels v ON v.imo = s.vessel_imo
        WHERE s.list_date = :d
          AND NOT EXISTS (
            SELECT 1 FROM sdn_matches m
            WHERE m.imo = v.imo AND m.sdn_uid = s.sdn_uid
              AND m.match_type = 'vessel_imo_exact' AND m.active)
        """),
        params,
    )
    counts["matches_imo_exact"] = result.rowcount or 0

    result = await session.execute(
        text("""
        INSERT INTO sdn_matches (imo, sdn_uid, match_type, score, matched_value, sdn_value, list_date)
        SELECT DISTINCT ON (v.imo, s.sdn_uid)
               v.imo, s.sdn_uid, 'vessel_name_fuzzy',
               similarity(v.name, cand.candidate), v.name, cand.candidate, s.list_date
        FROM sdn_entities s
        CROSS JOIN LATERAL (
            SELECT s.name AS candidate
            UNION
            SELECT a.alt_name FROM sdn_alt_names a
            WHERE a.sdn_uid = s.sdn_uid AND a.list_date = s.list_date
        ) cand
        JOIN vessels v
          ON v.name IS NOT NULL
         AND similarity(v.name, cand.candidate) >= :name_thr
        WHERE s.list_date = :d
          AND s.sdn_type = 'vessel'
          AND (s.vessel_imo IS NULL OR s.vessel_imo <> v.imo)
          AND NOT EXISTS (
            SELECT 1 FROM sdn_matches m
            WHERE m.imo = v.imo AND m.sdn_uid = s.sdn_uid
              AND m.match_type = 'vessel_name_fuzzy' AND m.active)
        ORDER BY v.imo, s.sdn_uid, similarity(v.name, cand.candidate) DESC
        """),
        params,
    )
    counts["matches_name_fuzzy"] = result.rowcount or 0

    result = await session.execute(
        text("""
        INSERT INTO sdn_matches (imo, sdn_uid, match_type, score, matched_value, sdn_value, list_date)
        SELECT DISTINCT ON (o.imo, s.sdn_uid)
               o.imo, s.sdn_uid, 'owner_name_fuzzy',
               similarity(o.owner_name, cand.candidate), o.owner_name, cand.candidate, s.list_date
        FROM sdn_entities s
        CROSS JOIN LATERAL (
            SELECT s.name AS candidate
            UNION
            SELECT a.alt_name FROM sdn_alt_names a
            WHERE a.sdn_uid = s.sdn_uid AND a.list_date = s.list_date
        ) cand
        JOIN vessel_ownership o
          ON o.valid_to IS NULL
         AND similarity(o.owner_name, cand.candidate) >= :owner_thr
        WHERE s.list_date = :d
          AND s.sdn_type <> 'vessel'
          AND NOT EXISTS (
            SELECT 1 FROM sdn_matches m
            WHERE m.imo = o.imo AND m.sdn_uid = s.sdn_uid
              AND m.match_type = 'owner_name_fuzzy' AND m.active)
        ORDER BY o.imo, s.sdn_uid, similarity(o.owner_name, cand.candidate) DESC
        """),
        params,
    )
    counts["matches_owner_fuzzy"] = result.rowcount or 0

    result = await session.execute(
        text("""
        UPDATE sdn_matches m SET active = false
        WHERE m.active
          AND NOT EXISTS (
            SELECT 1 FROM sdn_entities s
            WHERE s.sdn_uid = m.sdn_uid AND s.list_date = :d)
        """),
        params,
    )
    counts["matches_deactivated"] = result.rowcount or 0
    return counts


async def main() -> None:
    async with ingest_run("ofac_sdn") as run:
        async with SessionLocal() as session:
            summary = await refresh(session)
        run.record_count = summary["entities"]
        run.source_version = summary["list_date"]
        run.details = summary
        print(summary)


if __name__ == "__main__":
    asyncio.run(main())
