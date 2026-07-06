"""bluewake API entrypoint."""

from fastapi import FastAPI
from sqlalchemy import text

from app.api.routes import screenings, vessels
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

app.include_router(vessels.router)
app.include_router(screenings.router)


@app.get("/health")
async def health() -> dict:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ok"}
