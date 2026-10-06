"""Core Billing Engine -- FastAPI application entrypoint.

A clean-room rebuild driven entirely by docs/. See README.md.
"""
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse

from app.api.router import api_router
from app.core import config, time as btime
from app.db.database import init_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    if os.environ.get("VERCEL") or config.get("DEMO_SEED") == "1":
        # Hosted demo: every serverless instance starts from the same seeded
        # tenant with the clock frozen (see app/demo_seed.py).
        from app.demo_seed import ensure_seeded
        ensure_seeded(app)
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
        "hosted_preview": bool(os.environ.get("VERCEL")),
    }


@app.get("/ui", include_in_schema=False)
def explainer_ui():
    """Browser view of the differentiator: /ui?invoice=<id>&customers=<id>,<id>"""
    return FileResponse(Path(__file__).parent / "static" / "explainer.html")


@app.get("/app", include_in_schema=False)
def dashboard_ui():
    """Browser dashboard over the /v1 API (all six screens, live data)."""
    return FileResponse(Path(__file__).parent / "static" / "app.html")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/app")
