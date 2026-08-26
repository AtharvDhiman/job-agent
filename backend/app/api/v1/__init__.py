"""v1 API router assembly."""

from fastapi import APIRouter, Depends

from app.api.deps import rate_limit
from app.api.v1 import (
    applications,
    assistant,
    audit,
    auth,
    autopilot,
    dashboard,
    jobs,
    notifications,
    portals,
    privacy,
    profile,
    reviews,
    settings_router,
    source_tools,
)

#: Mounted once, here, so EVERY v1 route is limited per client IP and path --
#: including /assistant/*. `rate_limit` itself picks the tighter auth budget for
#: /login, /register and /refresh. It must not also be declared on a child
#: router or each request would consume two slots from the same bucket.
api_router = APIRouter(dependencies=[Depends(rate_limit)])
api_router.include_router(auth.router)
api_router.include_router(profile.router)
api_router.include_router(jobs.router)
# source_tools shares the /sources prefix with routes in jobs.py. That is safe
# because jobs.py defines no dynamic GET /sources/{x} or POST /sources/{x}
# route that could capture /sources/catalog or /sources/find first; its only
# dynamic routes are PATCH and DELETE.
api_router.include_router(source_tools.router)
api_router.include_router(autopilot.router)
api_router.include_router(portals.router)
api_router.include_router(applications.router)
api_router.include_router(reviews.router)
api_router.include_router(settings_router.router)
api_router.include_router(dashboard.router)
api_router.include_router(notifications.router)
api_router.include_router(audit.router)
api_router.include_router(privacy.router)
api_router.include_router(assistant.router)

__all__ = ["api_router"]
