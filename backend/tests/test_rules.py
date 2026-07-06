from datetime import datetime, timedelta, timezone

from app.models import GfwEvent, SdnMatch, VesselFlagHistory, VesselIdentity
from app.reports.generator import build_report, report_hash
from app.rules.engine import (
    RuleContext,
    rule_ais_gap,
    rule_dark_sts,
    rule_flag_hopping,
    rule_identity_conflict,
    rule_sdn_exposure,
)

NOW = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
IMO = "9321483"


def ctx(**kwargs) -> RuleContext:
    return RuleContext(imo=IMO, now=NOW, **kwargs)


def gap_event(start_h_ago: float, duration_h: float) -> GfwEvent:
    start = NOW - timedelta(hours=start_h_ago)
    return GfwEvent(
        gfw_event_id=f"gap-{start_h_ago}", event_type="gap", imo=IMO,
        start_ts=start, end_ts=start + timedelta(hours=duration_h), raw={},
    )


def encounter_event(start_h_ago: float) -> GfwEvent:
    start = NOW - timedelta(hours=start_h_ago)
    return GfwEvent(
        gfw_event_id=f"enc-{start_h_ago}", event_type="encounter", imo=IMO,
        start_ts=start, end_ts=start + timedelta(hours=3),
        other_gfw_vessel_id="gfw-x", raw={},
    )


# --- ais_gap ---------------------------------------------------------------

def test_ais_gap_flags_long_gaps_only():
    context = ctx(gfw_events=[gap_event(100, 12), gap_event(50, 2)])
    findings = rule_ais_gap(context, {"min_gap_hours": 6, "lookback_days": 180})
    assert len(findings) == 1
    assert findings[0].severity == "warning"
    assert len(findings[0].evidence["gaps"]) == 1  # 2h gap filtered out


def test_ais_gap_escalates_on_repeat():
    context = ctx(gfw_events=[gap_event(h, 10) for h in (100, 200, 300)])
    findings = rule_ais_gap(context, {"min_gap_hours": 6, "lookback_days": 180})
    assert findings[0].severity == "alert"


# --- dark_sts ---------------------------------------------------------------

def test_dark_sts_detects_encounter_adjacent_to_gap():
    context = ctx(gfw_events=[gap_event(100, 12), encounter_event(84)])  # 4h after gap end
    findings = rule_dark_sts(
        context, {"gap_proximity_hours": 12, "min_gap_hours": 6, "lookback_days": 180}
    )
    assert len(findings) == 1
    assert findings[0].severity == "alert"
    assert findings[0].evidence["event"]["type"] == "encounter"


def test_dark_sts_ignores_distant_encounter():
    context = ctx(gfw_events=[gap_event(500, 12), encounter_event(20)])
    findings = rule_dark_sts(
        context, {"gap_proximity_hours": 12, "min_gap_hours": 6, "lookback_days": 180}
    )
    assert findings == []


# --- flag_hopping -----------------------------------------------------------

def flag(code: str, days_ago: int) -> VesselFlagHistory:
    return VesselFlagHistory(
        imo=IMO, flag=code, source="test", observed_from=NOW - timedelta(days=days_ago)
    )


def test_flag_hopping_triggers_above_threshold():
    context = ctx(flag_history=[flag("PAN", 400), flag("COM", 300), flag("TGO", 200), flag("GAB", 100)])
    findings = rule_flag_hopping(context, {"max_changes": 2, "window_months": 18})
    assert len(findings) == 1
    assert "3 flag changes" in findings[0].summary


def test_flag_hopping_quiet_below_threshold():
    context = ctx(flag_history=[flag("PAN", 400), flag("COM", 300)])
    assert rule_flag_hopping(context, {"max_changes": 2, "window_months": 18}) == []


# --- identity_conflict --------------------------------------------------------

def ident(mmsi: str, from_days: int, to_days: int | None) -> VesselIdentity:
    return VesselIdentity(
        imo=IMO, mmsi=mmsi, source="test",
        valid_from=NOW - timedelta(days=from_days),
        valid_to=NOW - timedelta(days=to_days) if to_days is not None else None,
    )


def test_identity_conflict_on_overlapping_mmsis():
    context = ctx(identities=[ident("111111111", 60, None), ident("222222222", 30, None)])
    findings = rule_identity_conflict(context, {"lookback_days": 90})
    assert len(findings) == 1
    assert findings[0].severity == "alert"


def test_no_conflict_for_sequential_mmsis():
    context = ctx(identities=[ident("111111111", 60, 30), ident("222222222", 30, None)])
    assert rule_identity_conflict(context, {"lookback_days": 90}) == []


# --- sdn_exposure -------------------------------------------------------------

def match(match_type: str, review_status: str = "unreviewed") -> SdnMatch:
    return SdnMatch(
        id=1, imo=IMO, sdn_uid=10001, match_type=match_type, score=0.95,
        matched_value="X", sdn_value="Y", list_date=NOW.date(),
        review_status=review_status,
    )


def test_sdn_exposure_severity_mapping():
    context = ctx(sdn_matches=[match("vessel_imo_exact"), match("owner_name_fuzzy")])
    findings = rule_sdn_exposure(context, {})
    assert [f.severity for f in findings] == ["alert", "warning"]


def test_sdn_exposure_skips_false_positives():
    context = ctx(sdn_matches=[match("owner_name_fuzzy", review_status="false_positive")])
    assert rule_sdn_exposure(context, {}) == []


def test_confirmed_fuzzy_match_escalates():
    context = ctx(sdn_matches=[match("owner_name_fuzzy", review_status="confirmed")])
    assert rule_sdn_exposure(context, {})[0].severity == "alert"


# --- report hashing -----------------------------------------------------------

def test_report_hash_deterministic_and_sensitive():
    kwargs = dict(
        screening_id="abc", imo=IMO, screened_at=NOW.isoformat(), trigger="manual",
        requested_by=None, vessel_profile={"name": "X"}, ruleset=[], data_sources={},
        findings=[], overall_status="no_findings",
    )
    a = report_hash(build_report(**kwargs))
    b = report_hash(build_report(**kwargs))
    assert a == b
    kwargs["overall_status"] = "findings"
    assert report_hash(build_report(**kwargs)) != a
