"""Wire contract shared by the simulated ERP and its HTTP client."""

import uuid
from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class CreateTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    organization_id: uuid.UUID
    order_id: uuid.UUID
    assignee_id: uuid.UUID
    due_at: AwareDatetime
    description: str = Field(min_length=1, max_length=1000)


class TaskResponse(BaseModel):
    task_id: str
    organization_id: uuid.UUID
    order_id: uuid.UUID
    idempotency_key: str
    status: str
    created_at: datetime
    deduplicated: bool = False
