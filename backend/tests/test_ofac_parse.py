from app.ingest.ofac_sdn import extract_imo, parse_alt_csv, parse_sdn_csv

# Shaped like real sdn.csv rows (no header, -0- for null).
SDN_SAMPLE = (
    '8244,"HTAY CO.","-0- ","DPRK3","-0- ","-0- ","-0- ","-0- ","-0- ","-0- ","-0- ",'
    '"Ownership records."\n'
    '10001,"EXAMPLE GLORY","vessel","IRAN] [NPWMD","-0- ","EXCS","Crude Oil Tanker",'
    '"-0- ","85462","Iran","Example Shipping Lines","Vessel Registration Identification '
    'IMO 9321483; Former Vessel Flag None Identified."\n'
    'bad row\n'
)

ALT_SAMPLE = (
    '10001,1,"aka","GLORY EXAMPLE","-0- "\n'
    '10001,2,"fka","OLD GLORY","-0- "\n'
    '8244,3,"aka","-0- ","-0- "\n'
)


def test_parse_sdn_vessel_row():
    rows = parse_sdn_csv(SDN_SAMPLE)
    assert len(rows) == 2  # malformed row skipped
    vessel = rows[1]
    assert vessel["sdn_uid"] == 10001
    assert vessel["sdn_type"] == "vessel"
    assert vessel["name"] == "EXAMPLE GLORY"
    assert vessel["programs"] == ["IRAN", "NPWMD"]
    assert vessel["vessel_imo"] == "9321483"
    assert vessel["vessel_callsign"] == "EXCS"
    assert vessel["vessel_owner"] == "Example Shipping Lines"


def test_parse_sdn_nulls():
    entity = parse_sdn_csv(SDN_SAMPLE)[0]
    assert entity["sdn_type"] == "entity"  # -0- normalizes to default
    assert entity["vessel_imo"] is None
    assert entity["vessel_callsign"] is None


def test_parse_alt_csv():
    rows = parse_alt_csv(ALT_SAMPLE)
    assert len(rows) == 2  # null alt_name skipped
    assert rows[0] == {"sdn_uid": 10001, "alt_name": "GLORY EXAMPLE", "alt_type": "aka"}


def test_extract_imo():
    assert extract_imo("Vessel Registration Identification IMO 9321483.") == "9321483"
    assert extract_imo("IMO 1111111 fails the check digit") is None
    assert extract_imo("no imo here") is None
    assert extract_imo(None) is None
