"""Environment-driven settings.

Every value is read LAZILY on each access (never cached at import time) so that
tests and the demo script can move BILLING_NOW mid-run and the engine picks it
up immediately. Variable names come from ARCHITECTURE.md section 7.
"""
import os

from dotenv import load_dotenv

load_dotenv(override=False)

DEFAULTS = {
    "DATABASE_URL": "sqlite:///./billing.db",
    "BILLING_NOW": "",
    "BILLING_PERIOD_MINUTES": "0",
    "AI_PROVIDER_API_KEY": "",
    "AI_MODEL": "claude-opus-5-5",
    "SPIKE_THRESHOLD_FACTOR": "3.0",
    "MAX_PAST_DRIFT_DAYS": "30",
    "MAX_FUTURE_DRIFT_MINUTES": "5",
    "TENANT_ID": "tenant_default",
}


def get(name: str) -> str:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return DEFAULTS.get(name, "")
    return raw


def get_int(name: str) -> int:
    return int(float(get(name)))


def get_float(name: str) -> float:
    return float(get(name))


def database_path() -> str:
    """Translate the sqlite:/// URL into a plain filesystem path."""
    url = get("DATABASE_URL")
    for prefix in ("sqlite:///", "sqlite://", "sqlite:"):
        if url.startswith(prefix):
            return url[len(prefix):] or "./billing.db"
    return url


def ai_api_key() -> str:
    """Empty string means: run the deterministic template fallback."""
    return os.environ.get("AI_PROVIDER_API_KEY", "").strip()


def default_tenant() -> str:
    return get("TENANT_ID")
