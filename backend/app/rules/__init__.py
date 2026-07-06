"""Risk-rule engine (Phase 2).

Rules are small classes registered against a rule_id from the risk_rules
table; thresholds come from that table's params jsonb, not code, so tuning
never requires a deploy. Each rule receives a vessel's assembled history and
returns findings with evidence pointers (event ids, position ids, match ids).

Extensibility seam: rules declare a `regime` (OFAC today; EU packages, EUDR
later). A screening evaluates all enabled rules for the requested regimes and
freezes the exact ruleset (ids/versions/params) into the screening record.

Initial rules: sdn_exposure, ais_gap, dark_sts, flag_hopping, identity_conflict.
"""
