"""Typed AuraSpec policy schema."""

from __future__ import annotations

import re
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from aura_runtime.models import AgentEvent, EventKind, ObjectRef, Severity


class AuraLoader(yaml.SafeLoader):
    """Safe YAML loader with YAML 1.2 boolean behavior.

    PyYAML defaults to YAML 1.1, where keys such as ``on`` and ``off`` are booleans.
    AuraSpec uses ``on`` as a policy trigger, so only true/false are treated as booleans.
    """


AuraLoader.yaml_implicit_resolvers = {
    key: [(tag, pattern) for tag, pattern in resolvers if tag != "tag:yaml.org,2002:bool"]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
AuraLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false)$", re.IGNORECASE),
    list("tTfF"),
)


def value_at_path(value: Any, path: str) -> Any:
    """Resolve a dotted path through dictionaries and Pydantic models."""
    current = value
    for part in path.split("."):
        if isinstance(current, BaseModel):
            current = getattr(current, part, None)
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


class EventSelector(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: EventKind
    tool_matches: list[str] = Field(default_factory=list)
    where: dict[str, Any] = Field(default_factory=dict)
    correlate: dict[str, str] = Field(default_factory=dict)
    within_events: int | None = Field(default=None, ge=1)

    def matches(self, event: AgentEvent, *, reference: AgentEvent | None = None) -> bool:
        if event.kind != self.event:
            return False
        if self.tool_matches and not any(
            fnmatchcase(event.tool_name or "", pattern) for pattern in self.tool_matches
        ):
            return False
        if not all(
            value_at_path(event, path) == expected for path, expected in self.where.items()
        ):
            return False
        if self.correlate and reference is None:
            return False
        return all(
            value_at_path(event, candidate_path) == value_at_path(reference, reference_path)
            for candidate_path, reference_path in self.correlate.items()
        )


class DataConstraint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    op: Literal["==", "!=", "<", "<=", ">", ">="]
    value: str | int | float | bool
    message: str | None = None


class ObjectBinding(BaseModel):
    """Declaratively extract a qualified business-object link from an event."""

    model_config = ConfigDict(extra="forbid")

    on: EventSelector
    object_type: str = Field(min_length=1)
    id_path: str = Field(min_length=1)
    qualifier: str = Field(default="related", min_length=1)

    @model_validator(mode="after")
    def selector_is_local(self) -> ObjectBinding:
        if self.on.correlate:
            raise ValueError("object binding selectors cannot define correlate")
        return self

    def extract(self, event: AgentEvent) -> ObjectRef | None:
        if not self.on.matches(event):
            return None
        value = value_at_path(event, self.id_path)
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            return None
        object_id = str(value)
        if not object_id:
            return None
        return ObjectRef(
            object_type=self.object_type,
            object_id=object_id,
            qualifier=self.qualifier,
        )


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    description: str
    severity: Severity = Severity.HIGH
    effect: Literal["deny", "require_approval"] = "deny"
    on: EventSelector
    require_prior: EventSelector | None = None
    require_after: EventSelector | None = None
    constraints: list[DataConstraint] = Field(default_factory=list)

    @model_validator(mode="after")
    def has_a_requirement(self) -> Policy:
        if self.require_prior is None and self.require_after is None and not self.constraints:
            raise ValueError("policy must define require_prior, require_after, or constraints")
        if self.require_after is not None and self.require_after.within_events is None:
            raise ValueError("require_after must define within_events")
        if self.on.correlate:
            raise ValueError("on selectors cannot define correlate")
        return self


class AuraSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["0.1"]
    policies: list[Policy] = Field(min_length=1)
    object_bindings: list[ObjectBinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def policy_ids_are_unique(self) -> AuraSpec:
        ids = [policy.id for policy in self.policies]
        if len(ids) != len(set(ids)):
            raise ValueError("policy IDs must be unique")
        return self

    def bind_objects(self, event: AgentEvent) -> AgentEvent:
        """Return the event with declaratively extracted object references added."""
        references = [
            *event.objects,
            *[
                reference
                for binding in self.object_bindings
                if (reference := binding.extract(event)) is not None
            ],
        ]
        unique = {
            (item.object_type, item.object_id, item.qualifier): item for item in references
        }
        return event.model_copy(update={"objects": [unique[key] for key in sorted(unique)]})

    @classmethod
    def from_yaml(cls, path: str | Path) -> AuraSpec:
        return cls.from_yaml_text(Path(path).read_text(encoding="utf-8"))

    @classmethod
    def from_yaml_text(cls, content: str) -> AuraSpec:
        raw = yaml.load(content, Loader=AuraLoader)
        return cls.model_validate(raw)
