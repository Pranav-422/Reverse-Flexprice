"""Usage event ingestion (API.md section 4)."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.api.deps import tenant
from app.db import database
from app.services.ingestion import IngestionError, ingest

router = APIRouter()


@router.post("/events")
def ingest_event(body: dict, tenant_id: str = Depends(tenant)):
    try:
        status, payload = ingest(tenant_id, body)
    except IngestionError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    return JSONResponse(payload, status_code=status)


@router.get("/events")
def count_events(event_id: str, tenant_id: str = Depends(tenant)):
    """Storage-level inspection used by the dedup tests and the demo."""
    rows = database.fetch_all(
        "SELECT id, event_id, customer_id, event_name, timestamp, quantity, "
        "ingested_at FROM usage_events WHERE tenant_id = ? AND event_id = ?",
        (tenant_id, event_id))
    return {"event_id": event_id, "count": len(rows),
            "rows": [dict(r) for r in rows]}
