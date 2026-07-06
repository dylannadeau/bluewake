"""Data ingestion (Phase 1).

Each module here owns one upstream source and follows the same contract:
fetch → log a source_ingest_runs row → write append-only observation rows →
hand identity attributes to app.resolution for IMO linking.

Modules (stubs until the plan is confirmed):
  aisstream  — live AIS via aisstream.io websocket
  aishub     — AIS via AISHub polling API
  ofac_sdn   — OFAC SDN + alt-names lists (CSV)
  gfw        — Global Fishing Watch events (gaps, loitering, encounters) + vessel insights

Paid-source seam: a future Spire/Orbcomm module implements this same contract
and drops in beside these without touching resolution or rules.
"""
