"""Global Fishing Watch ingestion (stub — Phase 1).

Plan: for each vessel of interest, resolve the GFW vessel id from IMO via the
vessels search API, then pull the events endpoints (AIS_GAP, LOITERING,
ENCOUNTER, PORT_VISIT) into gfw_events, and vessel-insights for flag/identity
history into vessel_flag_history / vessel_identities. SAR detections deferred
until a customer funds imagery workflows (integration seam only).
"""
