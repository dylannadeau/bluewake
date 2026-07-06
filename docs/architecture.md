# bluewake architecture

Maritime sanctions/compliance screening. The product is a defensible,
timestamped screening report (PDF + JSON) proving a vessel was checked
against sanctions red flags at a point in time, with the exact data and
rules used frozen into the record.

## System shape

```
aisstream.io ─ws─┐
AISHub ──poll──┐ │
OFAC SDN ─csv─┐│ │      ┌────────────────┐     ┌──────────────┐
GFW APIs ─────┤├─┴──►   │ ingestion +    │ ──► │ Postgres +   │
              └┘        │ IMO resolution │     │ PostGIS      │
                        └────────────────┘     └──────┬───────┘
                                                      │
                                     ┌────────────────┴─────────────┐
                                     │ rule engine → screening      │
                                     │ (immutable) → PDF/JSON report│
                                     └────────────────┬─────────────┘
                                                      │
                                        FastAPI ──► Next.js UI
                                                      │
                                        scheduler → watchlist alerts (P4)
```

Single Docker Compose deployment (one VM / managed container service). No
Spark, no data lake — analytics are plain SQL over Postgres.

## Non-negotiable design rules

1. **IMO is the entity key.** MMSI is spoofable and only ever an observed
   attribute. All identity observations (MMSI/name/callsign/flag, per source,
   time-bounded) accumulate in `vessel_identities` — that longitudinal record
   is the moat, so nothing is overwritten in place.
2. **Screenings are immutable** (DB triggers block UPDATE/DELETE). Each one
   freezes the ruleset (ids/versions/params) and per-source as-of timestamps,
   and the JSON report is SHA-256 hashed for tamper evidence.
3. **Decision-support, not verdicts.** Statuses are `no_findings` /
   `review_required` / `findings` — never "sanctioned"/"clean". SDN matches
   carry a `review_status` for human confirmation. Every report includes the
   disclaimer that absence of findings is not clearance.
4. **Rules are data, not code paths.** Thresholds live in
   `risk_rules.params` (jsonb); rules declare a `regime` (OFAC now, EU/EUDR
   later) so adjacent regimes are added by inserting rules, not rearchitecting.
5. **Sources are pluggable.** Every ingest module follows the same contract
   (fetch → log `source_ingest_runs` → append observations → feed resolution),
   which is the seam a paid source (Spire, Orbcomm) drops into later.

## Initial rule set

| rule_id | signal | data |
|---|---|---|
| `sdn_exposure` | vessel IMO/name or owner name on OFAC SDN | SDN list + ownership |
| `ais_gap` | dark periods ≥ threshold | own AIS stream + GFW gap events |
| `dark_sts` | STS encounter/loitering adjacent to a gap | GFW encounters/loitering × gaps |
| `flag_hopping` | >N flag changes in window | flag history |
| `identity_conflict` | concurrent conflicting identities on one IMO | identity observations |

## Build phases

1. Ingestion (AIS + SDN + GFW) → IMO resolution → schema *(schema done)*
2. Rule engine + screening report generator (PDF via WeasyPrint + JSON)
3. Next.js UI: IMO lookup, screening history, report download
4. Watchlist, scheduled re-screening (APScheduler), email alerts

## Explicit non-goals

Own AIS receivers, satellite imagery pipelines, ML risk scores, mobile app.
