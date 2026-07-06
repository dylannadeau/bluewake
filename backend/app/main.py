"""bluewake API entrypoint.

Route modules are registered here as they land:
  Phase 2: /vessels/{imo}, /screenings
  Phase 4: /watchlist
"""

from fastapi import FastAPI

from sqlalchemy import text

from app.db import engine

app = FastAPI(
    title="bluewake",
    description=(
        "Maritime sanctions/compliance screening. Outputs are decision-support "
        "and audit-trail records, not legal determinations; a human reviewer "
        "must confirm findings before action."
    ),
    version="0.1.0",
)


@app.get("/health")
async def health() -> dict:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ok"}
