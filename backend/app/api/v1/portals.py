"""Portal readiness: what each ATS can do, and what is stopping it right now.

Read-only. Everything here is derived from the connector registry plus this
user's own rows; granting or revoking automation still happens in
/settings/authorizations, and policy.decide() still gates every real action.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, DbSession, get_agent_settings
from app.models.user import AgentSettings
from app.schemas.portals import PortalStateOut
from app.services import portal_status

router = APIRouter(prefix="/portals", tags=["portals"])


@router.get("", response_model=list[PortalStateOut])
def list_portals(
    db: DbSession,
    user: CurrentUser,
    agent_settings: AgentSettings = Depends(get_agent_settings),
) -> list[dict[str, Any]]:
    """Every registered portal, closest-to-working first.

    The order is the order the dashboard shows them in, so the portals that can
    actually act appear above the ones that never will.
    """
    return portal_status.portal_states(db, user, agent_settings)
