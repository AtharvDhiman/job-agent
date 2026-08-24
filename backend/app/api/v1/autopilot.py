"""Autopilot: the one-shot pipeline and the readiness checklist.

The run endpoint is deliberately just a chain of the same services the worker
uses; anything it drafts still ends at the policy gate. The status endpoint is
read-only and exists so the UI can show the human the exact switches that are
still theirs to flip.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends

from app.api.deps import CurrentUser, DbSession, RequireOperator, get_agent_settings
from app.models.user import AgentSettings
from app.schemas.autopilot import AutopilotRunIn, AutopilotRunOut, AutopilotStatusOut
from app.services import audit, autopilot

router = APIRouter(prefix="/autopilot", tags=["autopilot"])


@router.post("/run", response_model=AutopilotRunOut)
def run(
    db: DbSession,
    user: RequireOperator,
    payload: AutopilotRunIn = Body(default=AutopilotRunIn()),
) -> dict:
    result = autopilot.run_pipeline(db, user, include_drafting=payload.include_drafting)
    audit.record(
        db,
        "autopilot.run",
        user_id=user.id,
        actor=user.email,
        payload={
            "discovery": result["discovery"],
            "scoring": result["scoring"],
            "drafting": result["drafting"],
        },
    )
    return result


@router.get("/status", response_model=AutopilotStatusOut)
def status(
    db: DbSession,
    user: CurrentUser,
    agent_settings: AgentSettings = Depends(get_agent_settings),
) -> dict:
    gate_state = autopilot.gates(db, user, agent_settings)
    return {**gate_state, "next_steps": autopilot.next_steps(gate_state)}
