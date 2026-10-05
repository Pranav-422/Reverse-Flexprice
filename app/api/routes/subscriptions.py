"""Subscriptions and mid-period plan upgrades (API.md sections 3 and 5)."""
from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import tenant
from app.services import subscription as sub_svc
from app.services.subscription import SubscriptionError

router = APIRouter()


@router.post("/subscriptions", status_code=201)
def create_subscription(body: dict, tenant_id: str = Depends(tenant)):
    try:
        return sub_svc.create(tenant_id, body)
    except SubscriptionError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.get("/subscriptions/{sub_id}")
def get_subscription(sub_id: str, tenant_id: str = Depends(tenant)):
    sub = sub_svc.get(tenant_id, sub_id)
    if sub is None:
        raise HTTPException(404, f"subscription {sub_id} does not exist")
    return sub_svc.serialize(sub)


@router.post("/subscriptions/{sub_id}/upgrade")
def upgrade_subscription(sub_id: str, body: dict, tenant_id: str = Depends(tenant)):
    try:
        return sub_svc.upgrade(tenant_id, sub_id, body)
    except SubscriptionError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
