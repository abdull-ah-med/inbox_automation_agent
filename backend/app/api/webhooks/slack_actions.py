"""Slack button and modal action handler. Implemented in a later phase."""

from fastapi import APIRouter

router = APIRouter(prefix="/webhooks/slack", tags=["slack-actions"])
