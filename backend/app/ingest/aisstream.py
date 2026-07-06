"""aisstream.io live AIS ingestion (stub — Phase 1).

Plan: long-running websocket consumer subscribed to PositionReport and
ShipStaticData messages. Position reports append to ais_positions keyed by
MMSI; static-data messages (which carry IMO) feed entity resolution so the
MMSI→IMO link is learned from the vessel's own static broadcasts.
"""
