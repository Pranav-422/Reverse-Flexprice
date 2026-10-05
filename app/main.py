"""Core Billing Engine -- FastAPI application entrypoint.

A clean-room rebuild driven entirely by docs/. See README.md.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.core import config, time as btime
from app.db.database import init_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Core Billing Engine",
    description="Usage-based billing for Indian AI API startups. Money in integer paise.",
    version="1.0.0",
    lifespan=lifespan,
)
app.include_router(api_router)


@app.get("/health")
def health():
    """Also reports whether the AI explainer is live or on template fallback."""
    return {
        "status": "ok",
        "now": btime.now(),
        "now_iso": btime.to_iso(btime.now()),
        "billing_now_injected": bool(config.get("BILLING_NOW")),
        "billing_period_minutes": config.get_int("BILLING_PERIOD_MINUTES"),
        "ai_explainer": "live" if config.ai_api_key() else "template_fallback",
    }
