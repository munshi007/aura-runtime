import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from aura_runtime.cli import app
from aura_runtime.models import AgentEvent, EventKind, ObjectRef
from aura_runtime.ocel_export import events_to_ocel_json
from aura_runtime.store import SQLiteEventStore


def event(
    run_id: str,
    sequence: int,
    *,
    objects: list[ObjectRef],
    offset: int = 0,
) -> AgentEvent:
    return AgentEvent(
        run_id=run_id,
        kind=EventKind.TOOL_CALL_REQUESTED,
        timestamp=datetime(2026, 8, 4, 12, 0, tzinfo=UTC) + timedelta(seconds=offset),
        sequence=sequence,
        tool_name="update_customer",
        objects=objects,
        data={"arguments": {"secret": "must-not-export"}},
    )


def customer() -> ObjectRef:
    return ObjectRef(object_type="customer", object_id="cus-secret", qualifier="subject")


def test_ocel_export_preserves_shared_objects_without_raw_content() -> None:
    first = event("run-a", 0, objects=[customer()])
    second = event(
        "run-b",
        0,
        objects=[customer(), ObjectRef(object_type="ticket", object_id="tic-7")],
        offset=1,
    )

    payload = events_to_ocel_json([second, first])

    assert [item["name"] for item in payload["eventTypes"]] == ["tool.call.requested"]
    assert [item["name"] for item in payload["objectTypes"]] == [
        "aura.agent_run",
        "customer",
        "ticket",
    ]
    assert len(payload["events"]) == 2
    assert len(payload["objects"]) == 4
    customer_ids = [
        item["id"] for item in payload["objects"] if item["type"] == "customer"
    ]
    assert len(customer_ids) == 1
    assert customer_ids[0].startswith("aura:customer:")
    assert all(item["attributes"] == [] for item in payload["events"])
    assert all(item["attributes"] == [] for item in payload["objects"])

    serialized = json.dumps(payload)
    assert "cus-secret" not in serialized
    assert "run-a" not in serialized
    assert "run-b" not in serialized
    assert "must-not-export" not in serialized


def test_ocel_relationships_are_qualified_and_deduplicated() -> None:
    duplicate = customer()
    payload = events_to_ocel_json(
        [event("run-a", 0, objects=[duplicate, duplicate])],
        include_identifiers=True,
    )

    relationships = payload["events"][0]["relationships"]
    assert relationships == [
        {"objectId": "aura.agent_run:run-a", "qualifier": "execution"},
        {"objectId": "customer:cus-secret", "qualifier": "subject"},
    ]
    assert {item["id"] for item in payload["objects"]} == {
        "aura.agent_run:run-a",
        "customer:cus-secret",
    }


def test_ocel_export_rejects_an_empty_collection() -> None:
    try:
        events_to_ocel_json([])
    except ValueError as error:
        assert str(error) == "cannot export an empty event collection"
    else:
        raise AssertionError("empty export should fail")


def test_export_ocel_cli_can_select_multiple_runs(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "aura.db")
    store.append_event(event("run-a", 0, objects=[customer()], offset=1))
    store.append_event(event("run-b", 0, objects=[customer()], offset=0))
    store.append_event(event("run-c", 0, objects=[], offset=2))
    output = tmp_path / "evidence.jsonocel"

    result = CliRunner().invoke(
        app,
        [
            "export-ocel",
            "--db",
            str(store.path),
            "--run",
            "run-a",
            "--run",
            "run-b",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert len(payload["events"]) == 2
    assert (
        len([item for item in payload["objects"] if item["type"] == "aura.agent_run"])
        == 2
    )
    assert "run-c" not in output.read_text(encoding="utf-8")


def test_store_all_events_uses_deterministic_cross_run_time_order(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "aura.db")
    later = event("run-a", 0, objects=[], offset=2)
    earlier = event("run-b", 0, objects=[], offset=1)
    store.append_event(later)
    store.append_event(earlier)

    assert [item.event_id for item in store.all_events()] == [earlier.event_id, later.event_id]
