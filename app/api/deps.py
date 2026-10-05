"""Shared request helpers."""
from fastapi import Header

from app.core import config


def tenant(x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID")) -> str:
    """Multi-tenant partition key for the request.

    DEVIATION (logged in docs/AGENT_LOG.md): DATA_MODEL.md makes tenant_id
    NOT NULL on every table but API.md defines no auth or tenant header. The
    simplest reading is an optional X-Tenant-ID header defaulting to the
    TENANT_ID env setting, so every documented request body works verbatim.
    """
    return (x_tenant_id or "").strip() or config.default_tenant()
