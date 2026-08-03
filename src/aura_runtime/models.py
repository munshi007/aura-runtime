"""Canonical evidence models shared by every Aura adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EventKind(StrEnum):
    RUN_STARTED = "run.started"
    MODEL_REQUESTED = "model.requested"
    MODEL_COMPLETED = "model.completed"
    TOOL_CALL_REQUESTED = "tool.call.requested"
    TOOL_CALL_COMPLETED = "tool.call.completed"
    TOOL_CALL_FAILED = "tool.call.failed"
    HUMAN_APPROVAL = "human.approval"
    STATE_CHANGED = "state.changed"
    RUN_COMPLETED = "run.completed"
    UNKNOWN = "unknown"


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AgentEvent(BaseModel):
    """One immutable fact observed during an agent execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: UUID = Field(default_factory=uuid4)
    run_id: str = Field(min_length=1)
    kind: EventKind
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: str = Field(default="aura", min_length=1)
    actor: str | None = None
    tool_name: str | None = None
    sequence: int | None = Field(default=None, ge=0)
    parent_event_id: UUID | None = None
    trace_id: str | None = None
    span_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(UTC)


class Finding(BaseModel):
    """A deterministic policy violation with evidence references."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    finding_id: UUID = Field(default_factory=uuid4)
    run_id: str
    policy_id: str
    severity: Severity
    message: str
    event_id: UUID
    evidence_event_ids: list[UUID] = Field(default_factory=list)
    engine: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
