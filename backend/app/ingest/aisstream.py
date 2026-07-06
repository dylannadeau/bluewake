"""aisstream.io live AIS consumer.

Long-running websocket process (run alongside the API):

    python -m app.ingest.aisstream

Storage policy (confirmed): positions are PERSISTED only for tracked vessels —
any IMO present in the vessels table (screened, watchlisted, or SDN-seeded).
Everything else is held in a short in-memory ring buffer per MMSI so that the
moment a vessel becomes tracked (static data reveals a tracked IMO, or a new
screening creates the vessel), its recent context is flushed to the database
rather than lost. A global firehose would be millions of rows/day for vessels
nobody asked about.

ShipStaticData messages carry the IMO and are the ground truth for MMSI→IMO
links; they flow through the resolution layer, which also validates the IMO
check digit so spoofed static data doesn't create garbage entities.
"""

import asyncio
import json
import logging
from collections import defaultdict, deque
from datetime import datetime, timezone

import websockets
from sqlalchemy import insert, select

from app.config import settings
from app.db import SessionLocal
from app.models import AisPosition, Vessel, VesselIdentity
from app.resolution.resolver import observe_identity

logger = logging.getLogger("bluewake.aisstream")

WS_URL = "wss://stream.aisstream.io/v0/stream"
BUFFER_PER_MMSI = 32          # untracked recent-context ring buffer
FLUSH_INTERVAL_SECONDS = 5.0  # batch positions instead of row-at-a-time
TRACKED_REFRESH_SECONDS = 300


def _subscription() -> str:
    return json.dumps(
        {
            "APIKey": settings.aisstream_api_key,
            "BoundingBoxes": [[[-90, -180], [90, 180]]],
            "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
        }
    )


def normalize_position(message: dict) -> dict | None:
    """Map an aisstream PositionReport to an ais_positions row dict."""
    meta = message.get("MetaData") or {}
    body = (message.get("Message") or {}).get("PositionReport") or {}
    mmsi = meta.get("MMSI")
    lat = body.get("Latitude", meta.get("latitude"))
    lon = body.get("Longitude", meta.get("longitude"))
    if mmsi is None or lat is None or lon is None:
        return None
    ts = meta.get("time_utc")
    try:
        # e.g. "2026-07-06 19:02:13.456 +0000 UTC"
        parsed_ts = datetime.strptime(ts[:23], "%Y-%m-%d %H:%M:%S.%f").replace(
            tzinfo=timezone.utc
        ) if ts else datetime.now(timezone.utc)
    except (ValueError, TypeError):
        parsed_ts = datetime.now(timezone.utc)
    return {
        "mmsi": str(mmsi).zfill(9),
        "imo": None,  # stamped at flush time from the tracked map
        "ts": parsed_ts,
        "position": f"SRID=4326;POINT({lon} {lat})",
        "sog_knots": body.get("Sog"),
        "cog_deg": body.get("Cog"),
        "heading_deg": body.get("TrueHeading"),
        "nav_status": body.get("NavigationalStatus"),
        "source": "aisstream",
    }


def normalize_static(message: dict) -> dict | None:
    """Map a ShipStaticData message to identity attributes (IMO ground truth)."""
    meta = message.get("MetaData") or {}
    body = (message.get("Message") or {}).get("ShipStaticData") or {}
    mmsi = meta.get("MMSI")
    imo = body.get("ImoNumber")
    if mmsi is None or not imo:
        return None
    return {
        "mmsi": str(mmsi).zfill(9),
        "imo": str(imo).zfill(7),
        "name": (body.get("Name") or "").strip() or None,
        "callsign": (body.get("CallSign") or "").strip() or None,
    }


class AisStreamConsumer:
    def __init__(self) -> None:
        self.tracked: dict[str, str] = {}  # mmsi -> imo, persisted vessels only
        self.buffers: dict[str, deque] = defaultdict(lambda: deque(maxlen=BUFFER_PER_MMSI))
        self.pending: list[dict] = []      # rows awaiting batch insert

    async def load_tracked(self) -> None:
        """Tracked set = current MMSI links for every IMO in the vessels table."""
        async with SessionLocal() as session:
            rows = await session.execute(
                select(VesselIdentity.mmsi, VesselIdentity.imo)
                .join(Vessel, Vessel.imo == VesselIdentity.imo)
                .where(VesselIdentity.mmsi.is_not(None), VesselIdentity.valid_to.is_(None))
            )
            self.tracked = {mmsi: imo for mmsi, imo in rows}
        logger.info("tracking %d MMSI→IMO links", len(self.tracked))

    async def handle_static(self, data: dict) -> None:
        mmsi = data["mmsi"]
        async with SessionLocal() as session:
            # Storage policy: only vessels already in the DB (screened,
            # watchlisted, or SDN-seeded) accumulate identity/position history.
            # Auto-creating vessels from every static broadcast would re-open
            # the global-firehose volume problem this policy exists to avoid.
            if await session.get(Vessel, data["imo"]) is None:
                return
            vessel = await observe_identity(
                session,
                imo=data["imo"],
                source="aisstream",
                observed_at=datetime.now(timezone.utc),
                mmsi=mmsi,
                name=data["name"],
                callsign=data["callsign"],
            )
            if vessel is None:
                return  # IMO failed checksum — spoofed/garbage static data
            await session.commit()
        if mmsi not in self.tracked:
            self.tracked[mmsi] = data["imo"]
            # promote buffered recent context now that the vessel is tracked
            self.pending.extend(
                {**row, "imo": data["imo"]} for row in self.buffers.pop(mmsi, ())
            )

    def handle_position(self, row: dict) -> None:
        imo = self.tracked.get(row["mmsi"])
        if imo is not None:
            self.pending.append({**row, "imo": imo})
        else:
            self.buffers[row["mmsi"]].append(row)

    async def flush(self) -> None:
        if not self.pending:
            return
        batch, self.pending = self.pending, []
        async with SessionLocal() as session:
            await session.execute(insert(AisPosition), batch)
            await session.commit()
        logger.debug("flushed %d positions", len(batch))

    async def periodic_flush(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_INTERVAL_SECONDS)
            try:
                await self.flush()
            except Exception:
                logger.exception("position flush failed; retrying next interval")

    async def periodic_tracked_refresh(self) -> None:
        while True:
            await asyncio.sleep(TRACKED_REFRESH_SECONDS)
            try:
                await self.load_tracked()
            except Exception:
                logger.exception("tracked-set refresh failed")

    async def consume(self) -> None:
        async for ws in websockets.connect(WS_URL, ping_interval=20):
            try:
                await ws.send(_subscription())
                async for raw in ws:
                    message = json.loads(raw)
                    msg_type = message.get("MessageType")
                    if msg_type == "PositionReport":
                        row = normalize_position(message)
                        if row:
                            self.handle_position(row)
                    elif msg_type == "ShipStaticData":
                        data = normalize_static(message)
                        if data:
                            await self.handle_static(data)
            except websockets.ConnectionClosed:
                logger.warning("stream closed; reconnecting")
                continue

    async def run(self) -> None:
        if not settings.aisstream_api_key:
            raise RuntimeError("AISSTREAM_API_KEY not configured")
        await self.load_tracked()
        async with asyncio.TaskGroup() as group:
            group.create_task(self.consume())
            group.create_task(self.periodic_flush())
            group.create_task(self.periodic_tracked_refresh())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(AisStreamConsumer().run())
