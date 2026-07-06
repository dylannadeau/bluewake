"""OFAC SDN list ingestion (stub — Phase 1).

Plan: download sdn.csv + alt.csv, parse vessel entries (IMO extracted from
remarks), upsert sdn_entities/sdn_alt_names versioned by list publish date,
then recompute sdn_matches (exact IMO, trigram vessel-name, trigram owner-name)
and deactivate matches whose SDN entries dropped off the list.
"""
