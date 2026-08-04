"""Privacy-safe OCEL 2.0 JSON export for object-centric agent evidence."""

from __future__ import annotations

from hashlib import sha256
from typing import Any

from aura_runtime.models import AgentEvent, ObjectRef

RUN_OBJECT_TYPE = "aura.agent_run"


def _time(event: AgentEvent) -> str:
    return event.timestamp.isoformat().replace("+00:00", "Z")


def _object_id(object_type: str, object_id: str, *, include_identifiers: bool) -> str:
    if include_identifiers:
        return f"{object_type}:{object_id}"
    digest = sha256(f"{object_type}\0{object_id}".encode()).hexdigest()[:24]
    return f"aura:{object_type}:{digest}"


def _relation(
    reference: ObjectRef,
    *,
    include_identifiers: bool,
) -> dict[str, str]:
    return {
        "objectId": _object_id(
            reference.object_type,
            reference.object_id,
            include_identifiers=include_identifiers,
        ),
        "qualifier": reference.qualifier,
    }


def events_to_ocel_json(
    events: list[AgentEvent],
    *,
    include_identifiers: bool = False,
) -> dict[str, Any]:
    """Return an OCEL 2.0 JSON document without event or object attributes."""
    if not events:
        raise ValueError("cannot export an empty event collection")

    ordered = sorted(
        events,
        key=lambda event: (
            event.timestamp,
            event.run_id,
            event.sequence is None,
            event.sequence if event.sequence is not None else 0,
            str(event.event_id),
        ),
    )
    object_refs = {
        (reference.object_type, reference.object_id)
        for event in ordered
        for reference in event.objects
    }
    run_refs = {(RUN_OBJECT_TYPE, event.run_id) for event in ordered}
    all_objects = object_refs | run_refs
    object_types = sorted({object_type for object_type, _ in all_objects})
    event_types = sorted({event.kind.value for event in ordered})

    ocel_events: list[dict[str, Any]] = []
    for event in ordered:
        run_relation = {
            "objectId": _object_id(
                RUN_OBJECT_TYPE,
                event.run_id,
                include_identifiers=include_identifiers,
            ),
            "qualifier": "execution",
        }
        relationships = [
            run_relation,
            *[
                _relation(reference, include_identifiers=include_identifiers)
                for reference in event.objects
            ],
        ]
        unique_relationships = {
            (item["objectId"], item["qualifier"]): item for item in relationships
        }
        ocel_events.append(
            {
                "id": f"event:{event.event_id}",
                "type": event.kind.value,
                "time": _time(event),
                "attributes": [],
                "relationships": [
                    unique_relationships[key] for key in sorted(unique_relationships)
                ],
            }
        )

    return {
        "eventTypes": [
            {"name": event_type, "attributes": []} for event_type in event_types
        ],
        "objectTypes": [
            {"name": object_type, "attributes": []} for object_type in object_types
        ],
        "events": ocel_events,
        "objects": [
            {
                "id": _object_id(
                    object_type,
                    object_id,
                    include_identifiers=include_identifiers,
                ),
                "type": object_type,
                "attributes": [],
                "relationships": [],
            }
            for object_type, object_id in sorted(
                all_objects,
                key=lambda item: _object_id(
                    item[0], item[1], include_identifiers=include_identifiers
                ),
            )
        ],
    }
