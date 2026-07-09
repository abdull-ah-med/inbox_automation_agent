"""Microsoft Graph notification webhook. Implemented in a later phase."""

from fastapi import APIRouter

router = APIRouter(prefix="/webhooks/graph", tags=["graph-webhooks"])
