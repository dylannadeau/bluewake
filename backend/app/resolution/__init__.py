"""Entity resolution (Phase 1).

The single invariant of the system: vessels are keyed on IMO, never MMSI.

Responsibilities:
  * learn MMSI→IMO links from AIS static data, GFW vessel records, and SDN
    vessel entries, recording each observation in vessel_identities with a
    validity window and confidence;
  * stamp resolved IMOs onto ais_positions rows retroactively;
  * surface conflicts (one MMSI on two IMOs concurrently, or one IMO
    broadcasting two MMSIs) as identity_conflict inputs to the rule engine
    rather than silently picking a winner.
"""
