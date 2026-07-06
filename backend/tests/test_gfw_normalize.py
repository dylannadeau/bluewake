from app.ingest.gfw import normalize_event

ENCOUNTER_EVENT = {
    "id": "enc-abc123",
    "type": "encounter",
    "start": "2026-05-01T02:00:00Z",
    "end": "2026-05-01T07:30:00Z",
    "position": {"lat": 24.9, "lon": 54.1},
    "encounter": {"vessel": {"id": "gfw-other-1", "ssvid": "412345678", "name": "OTHER"}},
}


def test_normalize_encounter():
    row = normalize_event(ENCOUNTER_EVENT, "encounter", "9321483", "gfw-self-1")
    assert row["gfw_event_id"] == "enc-abc123"
    assert row["event_type"] == "encounter"
    assert row["imo"] == "9321483"
    assert row["other_gfw_vessel_id"] == "gfw-other-1"
    assert row["geom"] == "SRID=4326;POINT(54.1 24.9)"
    assert row["start_ts"].isoformat() == "2026-05-01T02:00:00+00:00"
    assert row["raw"] == ENCOUNTER_EVENT


def test_normalize_event_without_position():
    event = {"id": "gap-1", "start": "2026-05-01T02:00:00Z"}
    row = normalize_event(event, "gap", "9321483", "gfw-self-1")
    assert row["geom"] is None
    assert row["end_ts"] is None
