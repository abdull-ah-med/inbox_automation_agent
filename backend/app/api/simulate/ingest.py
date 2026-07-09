"""Phase 0 mock email ingestion endpoint. Implemented in a later phase."""

from fastapi import APIRouter

router = APIRouter(prefix="/simulate", tags=["simulate"])
