# bluewake

Maritime sanctions/compliance screening. IMO-keyed vessel risk lookup that
screens against red-flag behaviors (dark STS transfers, AIS gaps/spoofing,
flag-hopping, sanctioned-owner exposure) and produces timestamped,
audit-ready screening reports (PDF + JSON).

> Outputs are decision-support and audit-trail records built from third-party
> data of varying quality — not legal determinations. Findings require human
> review, and the absence of findings is not clearance.

## Quick start

```sh
cp .env.example .env   # add API keys (aisstream.io, GFW)
make up                # postgres/postgis + api on :8000
```

The PostGIS schema in `db/migrations/` is applied automatically on first
database start.

## Layout

```
backend/app/ingest/      # aisstream, AISHub, OFAC SDN, GFW events (Phase 1)
backend/app/resolution/  # MMSI/name/flag → IMO entity resolution (Phase 1)
backend/app/rules/       # extensible risk-rule engine (Phase 2)
backend/app/reports/     # PDF/JSON screening report generator (Phase 2)
backend/app/api/         # FastAPI routes
db/migrations/           # PostGIS schema
frontend/                # Next.js UI (Phase 3)
docs/architecture.md     # design decisions + build plan
```
