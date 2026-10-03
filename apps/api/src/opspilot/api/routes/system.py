"""What this deployment is running, for the people using it.

Configuration names only — never a key, a secret or a URL. The UI uses this to label
the development model's scripted answers as such, and to show the bounds a run works
within, so nobody mistakes a fixture for a model or a bound for a bug.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter
from pydantic import BaseModel

from opspilot.agent.prompts import PROMPT_VERSION
from opspilot.auth.dependencies import CurrentPrincipal
from opspilot.core.clock import business_date
from opspilot.core.config import get_settings
from opspilot.retrieval.embeddings import embedding_identity

router = APIRouter()


class RunBounds(BaseModel):
    max_turns: int
    max_tool_calls: int
    deadline_seconds: float


class SystemInfo(BaseModel):
    app_env: str
    llm_provider: str
    llm_model: str
    #: True for the deterministic development provider: answers are scripted.
    scripted_model: bool
    embedding_mode: str
    prompt_version: str
    business_date: date
    writes_enabled: bool
    agent_enabled: bool
    bounds: RunBounds


@router.get("/system")
def system_info(principal: CurrentPrincipal) -> SystemInfo:
    settings = get_settings()
    return SystemInfo(
        app_env=settings.app_env.value,
        llm_provider=settings.llm_provider,
        llm_model="fake-1" if settings.llm_provider == "fake" else settings.llm_model,
        scripted_model=settings.llm_provider == "fake",
        embedding_mode=embedding_identity(settings),
        prompt_version=PROMPT_VERSION,
        business_date=business_date(settings),
        writes_enabled=settings.feature_writes_enabled,
        agent_enabled=settings.feature_agent_enabled,
        bounds=RunBounds(
            max_turns=settings.run_max_turns,
            max_tool_calls=settings.run_max_tool_calls,
            deadline_seconds=settings.run_deadline_seconds,
        ),
    )
