"""IMO-keyed entity resolution.

Every source funnels identity observations through observe_identity(). It
maintains the vessels row (current best-known attributes), the longitudinal
vessel_identities windows, flag history, and retro-stamps IMO onto AIS
positions that arrived keyed only by MMSI.

Conflicting observations are never merged away: a change in attributes closes
the previous validity window and opens a new one, so spoofing/renaming leaves
a queryable trail for the identity_conflict rule.
"""

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AisPosition, Vessel, VesselFlagHistory, VesselIdentity

IMO_WEIGHTS = (7, 6, 5, 4, 3, 2)


def is_valid_imo(imo: str | None) -> bool:
    """IMO number check-digit validation (rejects spoofed/garbage static data)."""
    if not imo or len(imo) != 7 or not imo.isdigit():
        return False
    checksum = sum(int(d) * w for d, w in zip(imo[:6], IMO_WEIGHTS))
    return checksum % 10 == int(imo[6])


async def observe_identity(
    session: AsyncSession,
    *,
    imo: str,
    source: str,
    observed_at: datetime,
    mmsi: str | None = None,
    name: str | None = None,
    callsign: str | None = None,
    flag: str | None = None,
    confidence: float = 1.0,
    valid_to: datetime | None = None,
) -> Vessel | None:
    """Record one identity observation for a vessel. Returns None if the IMO
    fails checksum validation (observation is dropped, caller may log it)."""
    if not is_valid_imo(imo):
        return None

    vessel = await session.get(Vessel, imo)
    if vessel is None:
        vessel = Vessel(imo=imo, first_seen_at=observed_at)
        session.add(vessel)

    # Current best-known attributes: only advance, never regress in time.
    if vessel.last_seen_at is None or observed_at >= vessel.last_seen_at:
        vessel.last_seen_at = observed_at
        vessel.updated_at = observed_at
        if name:
            vessel.name = name
        if callsign:
            vessel.callsign = callsign
        if flag:
            vessel.flag = flag
    if vessel.first_seen_at is None or observed_at < vessel.first_seen_at:
        vessel.first_seen_at = observed_at

    await _record_identity_window(
        session,
        imo=imo,
        source=source,
        observed_at=observed_at,
        mmsi=mmsi,
        name=name,
        callsign=callsign,
        flag=flag,
        confidence=confidence,
        valid_to=valid_to,
    )
    if flag:
        await _record_flag(session, imo=imo, flag=flag, source=source, observed_at=observed_at)
    if mmsi:
        await stamp_positions(session, mmsi=mmsi, imo=imo)
    return vessel


async def _record_identity_window(session: AsyncSession, *, imo, source, observed_at,
                                   mmsi, name, callsign, flag, confidence, valid_to) -> None:
    current = await session.scalar(
        select(VesselIdentity)
        .where(
            VesselIdentity.imo == imo,
            VesselIdentity.source == source,
            VesselIdentity.valid_to.is_(None),
        )
        .order_by(VesselIdentity.valid_from.desc())
        .limit(1)
    )
    if current is not None:
        unchanged = (
            (mmsi is None or current.mmsi == mmsi)
            and (name is None or current.name == name)
            and (callsign is None or current.callsign == callsign)
            and (flag is None or current.flag == flag)
        )
        if unchanged:
            return
        current.valid_to = observed_at

    session.add(
        VesselIdentity(
            imo=imo,
            mmsi=mmsi,
            name=name,
            callsign=callsign,
            flag=flag,
            source=source,
            confidence=confidence,
            valid_from=observed_at,
            valid_to=valid_to,
        )
    )


async def _record_flag(session: AsyncSession, *, imo, flag, source, observed_at) -> None:
    current = await session.scalar(
        select(VesselFlagHistory)
        .where(VesselFlagHistory.imo == imo, VesselFlagHistory.observed_to.is_(None))
        .order_by(VesselFlagHistory.observed_from.desc())
        .limit(1)
    )
    if current is not None:
        if current.flag == flag:
            return
        current.observed_to = observed_at
    session.add(
        VesselFlagHistory(imo=imo, flag=flag, source=source, observed_from=observed_at)
    )


async def stamp_positions(session: AsyncSession, *, mmsi: str, imo: str) -> int:
    """Retro-link AIS positions that arrived before the MMSI→IMO link was known."""
    result = await session.execute(
        update(AisPosition)
        .where(AisPosition.mmsi == mmsi, AisPosition.imo.is_(None))
        .values(imo=imo)
    )
    return result.rowcount or 0


async def imo_for_mmsi(session: AsyncSession, mmsi: str) -> str | None:
    """Most recent IMO a given MMSI resolved to, if any. Lookup helper only —
    never use MMSI as a storage key."""
    return await session.scalar(
        select(VesselIdentity.imo)
        .where(VesselIdentity.mmsi == mmsi)
        .order_by(VesselIdentity.valid_from.desc())
        .limit(1)
    )
