-- ============================================================================
-- bluewake — maritime sanctions/compliance screening
-- Migration 001: core schema (PostGIS)
--
-- Design invariants:
--   * IMO number is the ONLY entity key for vessels. MMSI is spoofable and
--     appears here strictly as an observed attribute, never a key.
--   * Raw observations are append-only; the longitudinal identity/behavior
--     history is the dataset moat and must never be overwritten in place.
--   * Screenings are immutable audit records: they snapshot the rule-set
--     version and the as-of state of every data source used.
--   * Rules carry a `regime` so adjacent regimes (EU packages, EUDR) can be
--     added without schema changes.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;    -- fuzzy name matching (SDN aliases)
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()

-- ----------------------------------------------------------------------------
-- Ingestion audit: every fetch from every source is logged so any screening
-- can prove exactly which data (and how fresh) it was based on.
-- ----------------------------------------------------------------------------
CREATE TABLE source_ingest_runs (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source       text        NOT NULL,  -- 'aisstream' | 'aishub' | 'ofac_sdn' | 'gfw_events' | 'gfw_insights' | ...
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    status       text        NOT NULL DEFAULT 'running',  -- running | ok | error
    record_count integer,
    source_version text,               -- e.g. SDN publish date, GFW dataset version
    details      jsonb       NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX idx_ingest_runs_source_time ON source_ingest_runs (source, started_at DESC);

-- ----------------------------------------------------------------------------
-- Entity layer: one row per IMO. Columns hold current best-known values;
-- all history lives in vessel_identities / vessel_flag_history / ownership.
-- ----------------------------------------------------------------------------
CREATE TABLE vessels (
    imo             char(7) PRIMARY KEY CHECK (imo ~ '^[0-9]{7}$'),
    name            text,
    callsign        text,
    flag            char(3),            -- ISO 3166-1 alpha-3 of current flag state
    vessel_type     text,
    gross_tonnage   numeric,
    length_m        numeric,
    gfw_vessel_id   text,               -- Global Fishing Watch internal id, once resolved
    first_seen_at   timestamptz,
    last_seen_at    timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- Longitudinal identity observations: which MMSI/name/callsign/flag a vessel
-- broadcast, over what window, per source. Multiple MMSIs resolving to one
-- IMO is expected; conflicting concurrent identities are a spoofing signal.
CREATE TABLE vessel_identities (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    imo         char(7) NOT NULL REFERENCES vessels (imo),
    mmsi        char(9) CHECK (mmsi ~ '^[0-9]{9}$'),
    name        text,
    callsign    text,
    flag        char(3),
    source      text        NOT NULL,
    confidence  real        NOT NULL DEFAULT 1.0 CHECK (confidence BETWEEN 0 AND 1),
    valid_from  timestamptz NOT NULL,
    valid_to    timestamptz,            -- NULL = still current
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_identities_imo   ON vessel_identities (imo, valid_from DESC);
CREATE INDEX idx_identities_mmsi  ON vessel_identities (mmsi, valid_from DESC);
CREATE INDEX idx_identities_name  ON vessel_identities USING gin (name gin_trgm_ops);

-- Flag history as first-class rows: makes the flag-hopping rule a window query.
CREATE TABLE vessel_flag_history (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    imo           char(7) NOT NULL REFERENCES vessels (imo),
    flag          char(3) NOT NULL,
    source        text    NOT NULL,
    observed_from timestamptz NOT NULL,
    observed_to   timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_flag_history_imo ON vessel_flag_history (imo, observed_from DESC);

-- Ownership/management chain, time-bounded. Sanctioned-owner exposure joins
-- this against sdn_entities/sdn_alt_names by fuzzy name match.
CREATE TABLE vessel_ownership (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    imo         char(7) NOT NULL REFERENCES vessels (imo),
    owner_name  text    NOT NULL,
    role        text    NOT NULL DEFAULT 'registered_owner',
                -- registered_owner | beneficial_owner | operator | manager
    country     char(3),
    source      text    NOT NULL,
    valid_from  timestamptz NOT NULL,
    valid_to    timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_ownership_imo  ON vessel_ownership (imo);
CREATE INDEX idx_ownership_name ON vessel_ownership USING gin (owner_name gin_trgm_ops);

-- ----------------------------------------------------------------------------
-- AIS observations (append-only). Rows arrive keyed by MMSI and get an IMO
-- stamped once entity resolution links them; imo stays NULL until then.
-- BRIN on ts keeps the index tiny as this table grows into the millions.
-- ----------------------------------------------------------------------------
CREATE TABLE ais_positions (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    mmsi        char(9) NOT NULL,
    imo         char(7) REFERENCES vessels (imo),
    ts          timestamptz NOT NULL,
    position    geography(Point, 4326) NOT NULL,
    sog_knots   real,
    cog_deg     real,
    heading_deg real,
    nav_status  smallint,
    source      text NOT NULL,          -- 'aisstream' | 'aishub'
    raw         jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_ais_positions_imo_ts  ON ais_positions (imo, ts DESC) WHERE imo IS NOT NULL;
CREATE INDEX idx_ais_positions_mmsi_ts ON ais_positions (mmsi, ts DESC);
CREATE INDEX idx_ais_positions_geom    ON ais_positions USING gist (position);
CREATE INDEX idx_ais_positions_ts_brin ON ais_positions USING brin (ts);

-- Derived AIS gaps (either computed from our own position stream or imported
-- from GFW gap events; `source` records which).
CREATE TABLE ais_gaps (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    imo           char(7) REFERENCES vessels (imo),
    mmsi          char(9),
    gap_start     timestamptz NOT NULL,
    gap_end       timestamptz,          -- NULL = still dark
    start_position geography(Point, 4326),
    end_position   geography(Point, 4326),
    duration_hours numeric GENERATED ALWAYS AS
        (EXTRACT(epoch FROM (gap_end - gap_start)) / 3600.0) STORED,
    source        text NOT NULL,        -- 'derived' | 'gfw'
    details       jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_ais_gaps_imo ON ais_gaps (imo, gap_start DESC);

-- ----------------------------------------------------------------------------
-- Global Fishing Watch events: gaps, loitering, encounters (STS), port visits.
-- Kept verbatim (raw jsonb) plus normalized columns the rule engine reads.
-- ----------------------------------------------------------------------------
CREATE TABLE gfw_events (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    gfw_event_id  text NOT NULL UNIQUE,
    event_type    text NOT NULL,        -- 'gap' | 'loitering' | 'encounter' | 'port_visit' | 'fishing'
    imo           char(7) REFERENCES vessels (imo),
    gfw_vessel_id text,
    other_imo     char(7),              -- encounter counterparty, if resolved
    other_gfw_vessel_id text,
    start_ts      timestamptz NOT NULL,
    end_ts        timestamptz,
    geom          geography(Point, 4326),
    raw           jsonb NOT NULL,
    ingested_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_gfw_events_imo_type ON gfw_events (imo, event_type, start_ts DESC);
CREATE INDEX idx_gfw_events_geom     ON gfw_events USING gist (geom);

-- ----------------------------------------------------------------------------
-- OFAC SDN list. One row per SDN entry per list version we ingested; aliases
-- split out for trigram matching. Matches are materialized with score +
-- provenance so a human can review them (human-in-the-loop).
-- ----------------------------------------------------------------------------
CREATE TABLE sdn_entities (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sdn_uid       integer NOT NULL,     -- OFAC ent_num
    list_date     date    NOT NULL,     -- SDN publish date this row came from
    name          text    NOT NULL,
    sdn_type      text,                 -- 'vessel' | 'individual' | 'entity' | ...
    programs      text[],
    vessel_imo    char(7),              -- parsed from remarks when present
    vessel_callsign text,
    vessel_flag   text,
    vessel_owner  text,
    remarks       text,
    raw           jsonb NOT NULL,
    ingested_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (sdn_uid, list_date)
);
CREATE INDEX idx_sdn_name       ON sdn_entities USING gin (name gin_trgm_ops);
CREATE INDEX idx_sdn_vessel_imo ON sdn_entities (vessel_imo) WHERE vessel_imo IS NOT NULL;

CREATE TABLE sdn_alt_names (
    id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sdn_uid   integer NOT NULL,
    list_date date    NOT NULL,
    alt_name  text    NOT NULL,
    alt_type  text                      -- 'aka' | 'fka' | 'nka'
);
CREATE INDEX idx_sdn_alt_names ON sdn_alt_names USING gin (alt_name gin_trgm_ops);

CREATE TABLE sdn_matches (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    imo           char(7) NOT NULL REFERENCES vessels (imo),
    sdn_uid       integer NOT NULL,
    match_type    text    NOT NULL,     -- 'vessel_imo_exact' | 'vessel_name_fuzzy' | 'owner_name_fuzzy'
    score         real    NOT NULL CHECK (score BETWEEN 0 AND 1),
    matched_value text    NOT NULL,     -- the vessel-side string that matched
    sdn_value     text    NOT NULL,     -- the SDN-side string it matched against
    list_date     date    NOT NULL,
    first_matched_at timestamptz NOT NULL DEFAULT now(),
    active        boolean NOT NULL DEFAULT true,   -- false once entry drops off list
    review_status text    NOT NULL DEFAULT 'unreviewed',
                  -- unreviewed | confirmed | false_positive
    reviewed_by   text,
    reviewed_at   timestamptz
);
CREATE INDEX idx_sdn_matches_imo ON sdn_matches (imo) WHERE active;

-- ----------------------------------------------------------------------------
-- Rule registry + immutable screenings (the product).
-- ----------------------------------------------------------------------------
CREATE TABLE risk_rules (
    rule_id     text PRIMARY KEY,       -- 'dark_sts' | 'ais_gap' | 'flag_hopping' | 'sdn_exposure' | ...
    name        text    NOT NULL,
    regime      text    NOT NULL DEFAULT 'OFAC',  -- 'OFAC' | 'EU' | 'EUDR' | ...
    version     integer NOT NULL DEFAULT 1,
    params      jsonb   NOT NULL DEFAULT '{}'::jsonb,  -- thresholds live here, not in code
    enabled     boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE screenings (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    imo             char(7) NOT NULL REFERENCES vessels (imo),
    screened_at     timestamptz NOT NULL DEFAULT now(),
    requested_by    text,
    trigger         text NOT NULL DEFAULT 'manual',   -- manual | scheduled | watchlist
    ruleset         jsonb NOT NULL,     -- frozen copy of every rule (id/version/params) evaluated
    data_sources    jsonb NOT NULL,     -- as-of timestamps + versions per source (SDN list date, last AIS ts, ...)
    overall_status  text  NOT NULL,     -- 'no_findings' | 'review_required' | 'findings'
    report_json     jsonb NOT NULL,     -- the full report payload, verbatim
    report_pdf_path text,
    report_sha256   char(64)            -- hash of the JSON report for tamper evidence
);
CREATE INDEX idx_screenings_imo ON screenings (imo, screened_at DESC);

CREATE TABLE screening_findings (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    screening_id uuid NOT NULL REFERENCES screenings (id),
    rule_id      text NOT NULL REFERENCES risk_rules (rule_id),
    severity     text NOT NULL,         -- 'info' | 'warning' | 'alert'
    summary      text NOT NULL,
    evidence     jsonb NOT NULL DEFAULT '{}'::jsonb   -- event ids, positions, match rows backing the finding
);
CREATE INDEX idx_findings_screening ON screening_findings (screening_id);

-- Screenings are the audit trail: forbid UPDATE/DELETE at the schema level.
CREATE OR REPLACE FUNCTION forbid_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'screening records are immutable';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER screenings_immutable
    BEFORE UPDATE OR DELETE ON screenings
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER screening_findings_immutable
    BEFORE UPDATE OR DELETE ON screening_findings
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();

-- ----------------------------------------------------------------------------
-- Watchlist + alerts (phase 4; schema now so screenings can reference it).
-- ----------------------------------------------------------------------------
CREATE TABLE watchlist_entries (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    imo         char(7) NOT NULL REFERENCES vessels (imo),
    label       text,
    alert_email text NOT NULL,
    rescreen_interval_hours integer NOT NULL DEFAULT 24,
    active      boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (imo, alert_email)
);

CREATE TABLE watchlist_alerts (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    watchlist_id bigint NOT NULL REFERENCES watchlist_entries (id),
    screening_id uuid   NOT NULL REFERENCES screenings (id),
    channel      text   NOT NULL DEFAULT 'email',
    sent_at      timestamptz,
    status       text   NOT NULL DEFAULT 'pending'   -- pending | sent | failed
);

-- ----------------------------------------------------------------------------
-- Seed the initial rule registry (params are defaults; tune per deployment).
-- ----------------------------------------------------------------------------
INSERT INTO risk_rules (rule_id, name, regime, params) VALUES
  ('sdn_exposure',  'Vessel or owner matches OFAC SDN list',        'OFAC',
   '{"imo_exact": true, "name_similarity_threshold": 0.72, "owner_similarity_threshold": 0.80}'),
  ('ais_gap',       'AIS dark period / possible transponder manipulation', 'OFAC',
   '{"min_gap_hours": 6, "lookback_days": 180}'),
  ('dark_sts',      'STS encounter or loitering adjacent to an AIS gap',   'OFAC',
   '{"gap_proximity_hours": 12, "lookback_days": 180}'),
  ('flag_hopping',  'Multiple flag changes in a short window',      'OFAC',
   '{"max_changes": 2, "window_months": 18}'),
  ('identity_conflict', 'Concurrent conflicting identities on one IMO (spoofing signal)', 'OFAC',
   '{"lookback_days": 90}');
