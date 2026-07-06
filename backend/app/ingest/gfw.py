"""Global Fishing Watch ingestion.

On-demand per vessel (called at screening time and by scheduled re-screening):
resolve the GFW vessel identity from IMO, record its identity/flag/ownership
history through the resolution layer, and pull the events endpoints (AIS gaps,
loitering, encounters/STS, port visits) into gfw_events.

All upstream payloads are stored verbatim in raw jsonb; normalized columns are
extracted defensively since GFW response shapes evolve. SAR detections are a
deliberate non-goal until a customer funds imagery workflows.

Run: python -m app.ingest.gfw <IMO> [days]
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.ingest.common import ingest_run
from app.models import GfwEvent, Vessel, VesselOwnership
from app.resolution.resolver import is_valid_imo, observe_identity

BASE_URL = "https://gateway.api.globalfishingwatch.org/v3"
IDENTITY_DATASET = "public-global-vessel-identity:latest"
EVENT_DATASETS = {
    "encounter": "public-global-encounters-events:latest",
    "loitering": "public-global-loitering-events:latest",
    "gap": "public-global-gaps-events:latest",
    "port_visit": "public-global-port-visits-events:latest",
}
PAGE_SIZE = 100


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.gfw_api_token}"}


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


async def search_vessel(client: httpx.AsyncClient, imo: str) -> dict | None:
    """Find the GFW identity record whose registry IMO matches ours."""
    resp = await client.get(
        f"{BASE_URL}/vessels/search",
        params={
            "query": imo,
            "datasets[0]": IDENTITY_DATASET,
            "includes[0]": "OWNERSHIP",
        },
        headers=_headers(),
    )
    resp.raise_for_status()
    for entry in resp.json().get("entries", []):
        registry_imos = {
            str(r.get("imo")) for r in entry.get("registryInfo", []) if r.get("imo")
        }
        if imo in registry_imos:
            return entry
    return None


def normalize_event(entry: dict, event_type: str, imo: str, gfw_vessel_id: str) -> dict:
    """Map a GFW event payload to a gfw_events row (raw kept verbatim)."""
    position = entry.get("position") or {}
    lat, lon = position.get("lat"), position.get("lon")
    encounter_vessel = (entry.get("encounter") or {}).get("vessel") or {}
    return {
        "gfw_event_id": str(entry["id"]),
        "event_type": event_type,
        "imo": imo,
        "gfw_vessel_id": gfw_vessel_id,
        "other_gfw_vessel_id": encounter_vessel.get("id"),
        "other_imo": None,  # resolved later if the counterparty is ever screened
        "start_ts": _parse_ts(entry.get("start")),
        "end_ts": _parse_ts(entry.get("end")),
        "geom": f"SRID=4326;POINT({lon} {lat})" if lat is not None and lon is not None else None,
        "raw": entry,
    }


async def fetch_events(
    client: httpx.AsyncClient, gfw_vessel_id: str, dataset: str, start: datetime, end: datetime
) -> list[dict]:
    entries: list[dict] = []
    offset = 0
    while True:
        resp = await client.get(
            f"{BASE_URL}/events",
            params={
                "vessels[0]": gfw_vessel_id,
                "datasets[0]": dataset,
                "start-date": start.date().isoformat(),
                "end-date": end.date().isoformat(),
                "limit": PAGE_SIZE,
                "offset": offset,
            },
            headers=_headers(),
        )
        resp.raise_for_status()
        payload = resp.json()
        page = payload.get("entries", [])
        entries.extend(page)
        offset += len(page)
        if len(page) < PAGE_SIZE or offset >= int(payload.get("total", 0)):
            break
    return entries


async def _record_identities(session: AsyncSession, imo: str, profile: dict) -> None:
    for info in profile.get("selfReportedInfo", []):
        observed_at = _parse_ts(info.get("transmissionDateFrom"))
        if observed_at is None:
            continue
        mmsi = str(info["ssvid"]) if info.get("ssvid") else None
        await observe_identity(
            session,
            imo=imo,
            source="gfw",
            observed_at=observed_at,
            valid_to=_parse_ts(info.get("transmissionDateTo")),
            mmsi=mmsi if mmsi and len(mmsi) == 9 else None,
            name=info.get("shipname"),
            callsign=info.get("callsign"),
            flag=info.get("flag"),
        )

    for owner in profile.get("registryOwners", []):
        name = owner.get("name")
        if not name:
            continue
        valid_from = _parse_ts(owner.get("dateFrom")) or datetime.now(timezone.utc)
        if await _ownership_exists(session, imo, name, valid_from):
            continue
        session.add(
            VesselOwnership(
                imo=imo,
                owner_name=name,
                role="registered_owner",
                country=owner.get("flag"),
                source="gfw",
                valid_from=valid_from,
                valid_to=_parse_ts(owner.get("dateTo")),
            )
        )


async def _ownership_exists(session, imo, name, valid_from) -> bool:
    return (
        await session.scalar(
            select(VesselOwnership.id)
            .where(
                VesselOwnership.imo == imo,
                VesselOwnership.owner_name == name,
                VesselOwnership.valid_from == valid_from,
                VesselOwnership.source == "gfw",
            )
            .limit(1)
        )
    ) is not None


async def sync_vessel(session: AsyncSession, imo: str, days: int = 365) -> dict:
    """Pull GFW identity + events for one vessel. Returns a summary dict."""
    if not is_valid_imo(imo):
        raise ValueError(f"invalid IMO: {imo}")
    if not settings.gfw_api_token:
        raise RuntimeError("GFW_API_TOKEN not configured")

    async with httpx.AsyncClient(timeout=60) as client:
        profile = await search_vessel(client, imo)
        if profile is None:
            return {"imo": imo, "gfw": "not_found", "events": 0}

        await _record_identities(session, imo, profile)

        gfw_ids = [
            info["id"] for info in profile.get("selfReportedInfo", []) if info.get("id")
        ]
        vessel = await session.get(Vessel, imo)
        if vessel is not None and gfw_ids:
            vessel.gfw_vessel_id = gfw_ids[0]

        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        inserted = 0
        per_type: dict[str, int] = {}
        for event_type, dataset in EVENT_DATASETS.items():
            for gfw_id in gfw_ids:
                for entry in await fetch_events(client, gfw_id, dataset, start, end):
                    row = normalize_event(entry, event_type, imo, gfw_id)
                    result = await session.execute(
                        pg_insert(GfwEvent)
                        .values(**row)
                        .on_conflict_do_nothing(index_elements=["gfw_event_id"])
                    )
                    added = result.rowcount or 0
                    inserted += added
                    per_type[event_type] = per_type.get(event_type, 0) + added

    await session.commit()
    return {"imo": imo, "gfw": "ok", "events": inserted, "by_type": per_type}


async def main() -> None:
    imo = sys.argv[1]
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 365
    async with ingest_run("gfw_events") as run:
        async with SessionLocal() as session:
            summary = await sync_vessel(session, imo, days)
        run.record_count = summary.get("events", 0)
        run.details = summary
        print(summary)


if __name__ == "__main__":
    asyncio.run(main())
