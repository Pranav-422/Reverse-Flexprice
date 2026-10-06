"""Vercel entrypoint: the FastAPI app with the demo tenant pre-loaded.

Vercel runs this as a serverless function. Only /tmp is writable and each
instance starts empty, so every cold start seeds the demo tenant from
app/demo_seed.py into its own SQLite file in /tmp and freezes the clock at
2026-05-01 00:30 UTC -- the same state `scripts/ui` gives you locally.
Writes made in the browser live only as long as that instance does.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/paise_perfect_demo.db")
os.environ.setdefault("BILLING_PERIOD_MINUTES", "0")
os.environ.setdefault("TENANT_ID", "tenant_default")

from app.demo_seed import ensure_seeded  # noqa: E402
from app.main import app  # noqa: E402

ensure_seeded(app)
