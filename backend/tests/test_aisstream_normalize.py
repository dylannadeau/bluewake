from app.ingest.aisstream import normalize_position, normalize_static

POSITION_MSG = {
    "MessageType": "PositionReport",
    "MetaData": {
        "MMSI": 366999712,
        "ShipName": "TEST VESSEL",
        "latitude": 25.25,
        "longitude": 55.28,
        "time_utc": "2026-07-06 19:02:13.456789 +0000 UTC",
    },
    "Message": {
        "PositionReport": {
            "Latitude": 25.25,
            "Longitude": 55.28,
            "Sog": 12.3,
            "Cog": 88.1,
            "TrueHeading": 90,
            "NavigationalStatus": 0,
        }
    },
}

STATIC_MSG = {
    "MessageType": "ShipStaticData",
    "MetaData": {"MMSI": 366999712},
    "Message": {
        "ShipStaticData": {
            "ImoNumber": 9074729,
            "CallSign": "TEST1 ",
            "Name": "TEST VESSEL          ",
        }
    },
}


def test_normalize_position():
    row = normalize_position(POSITION_MSG)
    assert row["mmsi"] == "366999712"
    assert row["imo"] is None  # stamped at flush time, never trusted from AIS alone
    assert row["position"] == "SRID=4326;POINT(55.28 25.25)"
    assert row["sog_knots"] == 12.3
    assert row["ts"].year == 2026
    assert row["source"] == "aisstream"


def test_normalize_position_missing_fields():
    assert normalize_position({"MetaData": {}, "Message": {}}) is None


def test_normalize_static():
    data = normalize_static(STATIC_MSG)
    assert data == {
        "mmsi": "366999712",
        "imo": "9074729",
        "name": "TEST VESSEL",
        "callsign": "TEST1",
    }


def test_normalize_static_without_imo():
    msg = {"MetaData": {"MMSI": 1}, "Message": {"ShipStaticData": {"ImoNumber": 0}}}
    assert normalize_static(msg) is None
