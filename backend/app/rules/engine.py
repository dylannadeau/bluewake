"""Risk-rule engine.

Rules are pure functions over a pre-loaded RuleContext, registered by rule_id.
The DB rows in risk_rules control which rules run, their thresholds (params
jsonb), and their regime; evaluate() freezes exactly what ran into the
screening record. Adding an EU/EUDR rule is: write a function, register it,
insert a risk_rules row — no rearchitecting.

Severity semantics (decision-support, not verdicts):
  alert   — pattern strongly associated with sanctions-evasion; human review required
  warning — anomaly worth review
  info    — contextual note
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AisGap,
    GfwEvent,
    RiskRule,
    SdnMatch,
    Vessel,
    VesselFlagHistory,
    VesselIdentity,
    VesselOwnership,
)


@dataclass
class Finding:
    rule_id: str
    severity: str  # info | warning | alert
    summary: str
    evidence: dict = field(default_factory=dict)


@dataclass
class RuleContext:
    """Everything a rule may inspect, loaded once per screening."""

    imo: str
    now: datetime
    vessel: Vessel | None = None
    identities: list[VesselIdentity] = field(default_factory=list)
    flag_history: list[VesselFlagHistory] = field(default_factory=list)
    ownership: list[VesselOwnership] = field(default_factory=list)
    sdn_matches: list[SdnMatch] = field(default_factory=list)
    gfw_events: list[GfwEvent] = field(default_factory=list)
    ais_gaps: list[AisGap] = field(default_factory=list)


async def load_context(session: AsyncSession, imo: str) -> RuleContext:
    ctx = RuleContext(imo=imo, now=datetime.now(timezone.utc))
    ctx.vessel = await session.get(Vessel, imo)
    ctx.identities = list(
        await session.scalars(
            select(VesselIdentity).where(VesselIdentity.imo == imo)
            .order_by(VesselIdentity.valid_from)
        )
    )
    ctx.flag_history = list(
        await session.scalars(
            select(VesselFlagHistory).where(VesselFlagHistory.imo == imo)
            .order_by(VesselFlagHistory.observed_from)
        )
    )
    ctx.ownership = list(
        await session.scalars(select(VesselOwnership).where(VesselOwnership.imo == imo))
    )
    ctx.sdn_matches = list(
        await session.scalars(
            select(SdnMatch).where(SdnMatch.imo == imo, SdnMatch.active.is_(True))
        )
    )
    ctx.gfw_events = list(
        await session.scalars(
            select(GfwEvent).where(GfwEvent.imo == imo).order_by(GfwEvent.start_ts)
        )
    )
    ctx.ais_gaps = list(
        await session.scalars(
            select(AisGap).where(AisGap.imo == imo).order_by(AisGap.gap_start)
        )
    )
    return ctx


def _iso(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts else None


def _hours(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return round((end - start).total_seconds() / 3600.0, 2)


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

def rule_sdn_exposure(ctx: RuleContext, params: dict) -> list[Finding]:
    findings = []
    for match in ctx.sdn_matches:
        if match.review_status == "false_positive":
            continue
        exact = match.match_type == "vessel_imo_exact"
        confirmed = match.review_status == "confirmed"
        findings.append(
            Finding(
                rule_id="sdn_exposure",
                severity="alert" if (exact or confirmed) else "warning",
                summary=(
                    f"{match.match_type.replace('_', ' ')} against OFAC SDN entry "
                    f"{match.sdn_uid} ('{match.sdn_value}'), score {match.score:.2f}, "
                    f"review status: {match.review_status}"
                ),
                evidence={
                    "sdn_match_id": match.id,
                    "sdn_uid": match.sdn_uid,
                    "match_type": match.match_type,
                    "matched_value": match.matched_value,
                    "sdn_value": match.sdn_value,
                    "score": match.score,
                    "list_date": match.list_date.isoformat(),
                    "review_status": match.review_status,
                },
            )
        )
    return findings


def _gap_windows(ctx: RuleContext, params: dict) -> list[dict]:
    """AIS-dark windows from both GFW gap events and our derived gaps."""
    lookback = ctx.now - timedelta(days=int(params.get("lookback_days", 180)))
    min_hours = float(params.get("min_gap_hours", 6))
    windows = []
    for ev in ctx.gfw_events:
        if ev.event_type != "gap" or ev.start_ts < lookback:
            continue
        duration = _hours(ev.start_ts, ev.end_ts)
        if duration is None or duration >= min_hours:
            windows.append(
                {"source": "gfw", "id": ev.gfw_event_id, "start": ev.start_ts,
                 "end": ev.end_ts, "duration_hours": duration}
            )
    for gap in ctx.ais_gaps:
        if gap.gap_start < lookback:
            continue
        duration = _hours(gap.gap_start, gap.gap_end)
        if duration is None or duration >= min_hours:
            windows.append(
                {"source": gap.source, "id": gap.id, "start": gap.gap_start,
                 "end": gap.gap_end, "duration_hours": duration}
            )
    return windows


def rule_ais_gap(ctx: RuleContext, params: dict) -> list[Finding]:
    windows = _gap_windows(ctx, params)
    if not windows:
        return []
    min_hours = float(params.get("min_gap_hours", 6))
    longest = max((w["duration_hours"] or 0) for w in windows)
    return [
        Finding(
            rule_id="ais_gap",
            severity="alert" if len(windows) >= 3 else "warning",
            summary=(
                f"{len(windows)} AIS dark period(s) ≥ {min_hours:g}h in the last "
                f"{params.get('lookback_days', 180)} days (longest {longest:g}h). "
                "Extended transmission gaps can indicate transponder manipulation."
            ),
            evidence={
                "gaps": [
                    {**w, "start": _iso(w["start"]), "end": _iso(w["end"])}
                    for w in windows
                ]
            },
        )
    ]


def rule_dark_sts(ctx: RuleContext, params: dict) -> list[Finding]:
    """STS encounter or loitering adjacent in time to an AIS gap — the classic
    dark ship-to-ship transfer pattern."""
    proximity = timedelta(hours=float(params.get("gap_proximity_hours", 12)))
    lookback = ctx.now - timedelta(days=int(params.get("lookback_days", 180)))
    gaps = _gap_windows(ctx, params)
    findings = []
    for ev in ctx.gfw_events:
        if ev.event_type not in ("encounter", "loitering") or ev.start_ts < lookback:
            continue
        for gap in gaps:
            gap_end = gap["end"] or ctx.now
            adjacent = (
                ev.start_ts <= gap_end + proximity
                and (ev.end_ts or ev.start_ts) >= gap["start"] - proximity
            )
            if adjacent:
                findings.append(
                    Finding(
                        rule_id="dark_sts",
                        severity="alert",
                        summary=(
                            f"{ev.event_type} event at {_iso(ev.start_ts)} within "
                            f"{params.get('gap_proximity_hours', 12)}h of an AIS dark "
                            f"period starting {_iso(gap['start'])} — pattern consistent "
                            "with a dark ship-to-ship transfer"
                        ),
                        evidence={
                            "event": {
                                "gfw_event_id": ev.gfw_event_id,
                                "type": ev.event_type,
                                "start": _iso(ev.start_ts),
                                "end": _iso(ev.end_ts),
                                "other_gfw_vessel_id": ev.other_gfw_vessel_id,
                            },
                            "adjacent_gap": {**gap, "start": _iso(gap["start"]),
                                             "end": _iso(gap["end"])},
                        },
                    )
                )
                break  # one finding per event is enough
    return findings


def rule_flag_hopping(ctx: RuleContext, params: dict) -> list[Finding]:
    window_start = ctx.now - timedelta(days=30 * int(params.get("window_months", 18)))
    max_changes = int(params.get("max_changes", 2))
    recent = [f for f in ctx.flag_history if f.observed_from >= window_start]
    # Changes = transitions between consecutive distinct flags in the window.
    changes = [
        {"from": prev.flag, "to": cur.flag, "at": _iso(cur.observed_from)}
        for prev, cur in zip(recent, recent[1:])
        if prev.flag != cur.flag
    ]
    if len(changes) <= max_changes:
        return []
    return [
        Finding(
            rule_id="flag_hopping",
            severity="warning",
            summary=(
                f"{len(changes)} flag changes in the last "
                f"{params.get('window_months', 18)} months "
                f"({' → '.join([recent[0].flag] + [c['to'] for c in changes])}). "
                "Frequent reflagging is a known sanctions-evasion pattern."
            ),
            evidence={"changes": changes},
        )
    ]


def rule_identity_conflict(ctx: RuleContext, params: dict) -> list[Finding]:
    """Concurrent conflicting identity broadcasts on one IMO (spoofing signal)."""
    lookback = ctx.now - timedelta(days=int(params.get("lookback_days", 90)))
    findings = []
    idents = [
        i for i in ctx.identities
        if i.mmsi and (i.valid_to is None or i.valid_to >= lookback)
    ]
    for a_idx, a in enumerate(idents):
        for b in idents[a_idx + 1:]:
            if a.mmsi == b.mmsi:
                continue
            a_end = a.valid_to or ctx.now
            b_end = b.valid_to or ctx.now
            if a.valid_from < b_end and b.valid_from < a_end:  # windows overlap
                findings.append(
                    Finding(
                        rule_id="identity_conflict",
                        severity="alert",
                        summary=(
                            f"IMO {ctx.imo} observed broadcasting two MMSIs "
                            f"concurrently ({a.mmsi} via {a.source}, {b.mmsi} via "
                            f"{b.source}) — possible AIS identity spoofing"
                        ),
                        evidence={
                            "identity_a": {"id": a.id, "mmsi": a.mmsi, "source": a.source,
                                           "from": _iso(a.valid_from), "to": _iso(a.valid_to)},
                            "identity_b": {"id": b.id, "mmsi": b.mmsi, "source": b.source,
                                           "from": _iso(b.valid_from), "to": _iso(b.valid_to)},
                        },
                    )
                )
    return findings


RULES: dict[str, Callable[[RuleContext, dict], list[Finding]]] = {
    "sdn_exposure": rule_sdn_exposure,
    "ais_gap": rule_ais_gap,
    "dark_sts": rule_dark_sts,
    "flag_hopping": rule_flag_hopping,
    "identity_conflict": rule_identity_conflict,
}


async def evaluate(
    session: AsyncSession, ctx: RuleContext, regimes: list[str] | None = None
) -> tuple[list[Finding], list[dict]]:
    """Run all enabled registered rules for the given regimes.

    Returns (findings, ruleset) where ruleset is the frozen record of exactly
    which rule versions/params were evaluated — stored on the screening.
    """
    regimes = regimes or ["OFAC"]
    db_rules = list(
        await session.scalars(
            select(RiskRule).where(RiskRule.enabled.is_(True), RiskRule.regime.in_(regimes))
        )
    )
    findings: list[Finding] = []
    ruleset: list[dict] = []
    for db_rule in db_rules:
        fn = RULES.get(db_rule.rule_id)
        if fn is None:
            continue  # rule registered in DB but not implemented — skip, don't fail
        params = db_rule.params or {}
        findings.extend(fn(ctx, params))
        ruleset.append(
            {
                "rule_id": db_rule.rule_id,
                "name": db_rule.name,
                "regime": db_rule.regime,
                "version": db_rule.version,
                "params": params,
            }
        )
    return findings, ruleset
