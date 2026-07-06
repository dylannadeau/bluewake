"""bluewake API entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.routes import screenings, vessels, watchlist
from app.config import settings
from app.db import engine
from app.scheduler import build_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = None
    if settings.enable_scheduler:
        scheduler = build_scheduler()
        scheduler.start()
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


app = FastAPI(
    title="bluewake",
    description=(
        "Maritime sanctions/compliance screening. Outputs are decision-support "
        "and audit-trail records, not legal determinations; a human reviewer "
        "must confirm findings before action."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(vessels.router)
app.include_router(screenings.router)
app.include_router(watchlist.router)


@app.get("/health")
async def health() -> dict:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ok"}
