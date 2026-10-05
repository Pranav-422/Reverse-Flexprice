"""Mounts every /v1 route group."""
from fastapi import APIRouter

from app.api.routes import (catalog, dashboard, events, insights, invoices,
                            subscriptions)

api_router = APIRouter(prefix="/v1")
api_router.include_router(catalog.router, tags=["catalog"])
api_router.include_router(events.router, tags=["events"])
api_router.include_router(subscriptions.router, tags=["subscriptions"])
api_router.include_router(invoices.router, tags=["invoices"])
api_router.include_router(insights.router, tags=["insights"])
api_router.include_router(dashboard.router, tags=["dashboard"])
