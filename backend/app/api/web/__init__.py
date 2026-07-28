from app.api.web.dashboard import router as dashboard_router
from app.api.web.mailboxes import router as mailboxes_router
from app.api.web.threads import router as threads_router

__all__ = ["dashboard_router", "mailboxes_router", "threads_router"]
