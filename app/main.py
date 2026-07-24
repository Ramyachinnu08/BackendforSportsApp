"""SportyQo API entrypoint.

Run (dev):  uvicorn app.main:app --reload
Docs:       /docs (Swagger) · /redoc
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.errors import install_error_handlers
from app.db.base import SessionLocal
from app.services.scoring import seed_tiers

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # storage dirs + card-tier seed (config data, safe to run on every boot)
    Path(settings.storage_dir, "public").mkdir(parents=True, exist_ok=True)
    Path(settings.storage_dir, "private").mkdir(parents=True, exist_ok=True)
    async with SessionLocal() as db:
        # Lightweight one-shot migration: add columns that were introduced
        # after the initial schema. Postgres supports IF NOT EXISTS so
        # this is safe to run on every boot without a real migration tool.
        from sqlalchemy import text
        alter_statements = [
            "ALTER TABLE match_participants ADD COLUMN IF NOT EXISTS is_player_of_match BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE match_participants ADD COLUMN IF NOT EXISTS is_best_bowler BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE match_participants ADD COLUMN IF NOT EXISTS is_best_batsman BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE match_participants ADD COLUMN IF NOT EXISTS is_mvp BOOLEAN NOT NULL DEFAULT FALSE",
        ]
        for stmt in alter_statements:
            try:
                await db.execute(text(stmt))
            except Exception as e:
                logging.warning("Migration statement failed (may be OK): %s — %s", stmt, e)
        await seed_tiers(db)
        await db.commit()
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.api_version,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=settings.cors_origins != "*",
    allow_methods=["*"],
    allow_headers=["*"],
)

install_error_handlers(app)
app.include_router(api_router, prefix="/v1")

# public media (local storage provider); production serves from S3+CDN instead
Path(settings.storage_dir, "public").mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(Path(settings.storage_dir) / "public")), name="static")


@app.get("/health", tags=["system"])
async def health():
    return {"status": "ok", "version": settings.api_version}
