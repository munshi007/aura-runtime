import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mcp import Client
from typer.testing import CliRunner

from aura_runtime.cli import app
from aura_runtime.mcp_server import mcp
from aura_runtime.models import AgentEvent, EventKind, ObjectRef
from aura_runtime.object_process import compare_object_behavior, discover_object_behavior
from aura_runtime.store import SQLiteEventStore


def event(
    run_id: str,
    sequence: int,
    tool_name: str,
    objects: list[ObjectRef],
) -> AgentEvent:
    return AgentEvent(
        run_id=run_id,
        kind=EventKind.TOOL_CALL_REQUESTED,
        timestamp=datetime(2026, 8, 4, 12, tzinfo=UTC) + timedelta(seconds=sequence),
        sequence=sequence,
        tool_name=tool_name,
        objects=objects,
        data={"arguments": {"secret": "must-not-leak"}},
    )


def ref(object_type: str, object_id: str) -> ObjectRef:
    return ObjectRef(object_type=object_type, object_id=object_id)


def lifecycle(run_id: str, final_tool: str = "close_ticket") -> list[AgentEvent]:
    ticket = ref("ticket", "ticket-secret")
    customer = ref("customer", "customer-secret")
    return [
        event(run_id, 0, "read_ticket", [ticket]),
        event(run_id, 1, "notify_customer", [ticket, customer]),
        event(run_id, 2, final_tool, [ticket]),
    ]


def test_discovers_typed_lifecycles_and_cross_object_interactions() -> None:
    profile = discover_object_behavior(lifecycle("baseline"))

    assert profile.run_count == 1
    assert profile.event_count == 3
    assert profile.object_count == 2
    ticket = next(item for item in profile.object_types if item.object_type == "ticket")
    assert ticket.object_count == 1
    assert [(item.source, item.target) for item in ticket.transitions] == [
        (
            "tool.call.requested:notify_customer",
            "tool.call.requested:close_ticket",
        ),
        (
            "tool.call.requested:read_ticket",
            "tool.call.requested:notify_customer",
        ),
    ]
    assert [item.model_dump() for item in profile.interactions] == [
        {
            "left_object_type": "customer",
            "right_object_type": "ticket",
            "activity": "tool.call.requested:notify_customer",
            "count": 1,
        }
    ]

    serialized = profile.model_dump_json()
    assert "ticket-secret" not in serialized
    assert "customer-secret" not in serialized
    assert "must-not-leak" not in serialized
    assert profile.content_included is False


def test_duplicate_qualified_references_do_not_duplicate_a_trace_event() -> None:
    duplicate = ref("ticket", "same")
    profile = discover_object_behavior(
        [event("run", 0, "read_ticket", [duplicate, duplicate])]
    )

    assert profile.object_count == 1
    assert profile.object_types[0].event_count == 1


def test_discovery_requires_object_evidence() -> None:
    try:
        discover_object_behavior([event("run", 0, "read_ticket", [])])
    except ValueError as error:
        assert str(error) == "events do not contain business-object references"
    else:
        raise AssertionError("object-free evidence should fail")


def test_structural_comparison_ignores_frequency_but_detects_new_paths() -> None:
    baseline_events = lifecycle("baseline")
    baseline = discover_object_behavior(baseline_events)
    repeated = discover_object_behavior(baseline_events + lifecycle("baseline-2"))

    assert compare_object_behavior(baseline, repeated).verdict == "pass"

    candidate = discover_object_behavior(lifecycle("candidate", "delete_ticket"))
    report = compare_object_behavior(baseline, candidate)
    assert report.verdict == "fail"
    assert {(item.kind, item.object_type) for item in report.drifts} == {
        ("activity_added", "ticket"),
        ("activity_removed", "ticket"),
        ("transition_added", "ticket"),
        ("transition_removed", "ticket"),
    }


def test_object_cli_discovers_and_fails_on_drift(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "aura.db")
    for item in lifecycle("baseline") + lifecycle("candidate", "delete_ticket"):
        store.append_event(item)
    output = tmp_path / "object-report.json"

    discovered = CliRunner().invoke(
        app,
        ["objects", "discover", "--run", "baseline", "--db", str(store.path)],
    )
    compared = CliRunner().invoke(
        app,
        [
            "objects",
            "compare",
            "--baseline-run",
            "baseline",
            "--candidate-run",
            "candidate",
            "--db",
            str(store.path),
            "--output",
            str(output),
        ],
    )

    assert discovered.exit_code == 0, discovered.output
    assert json.loads(discovered.output)["object_count"] == 2
    assert compared.exit_code == 2
    assert json.loads(output.read_text())["verdict"] == "fail"


def test_mcp_object_tools_are_content_free_and_read_only(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "aura.db")
    for item in lifecycle("baseline") + lifecycle("candidate", "delete_ticket"):
        store.append_event(item)

    async def inspect() -> None:
        async with Client(mcp) as client:
            profile = await client.call_tool(
                "aura_object_behavior",
                {"run_ids": ["baseline"], "db_path": str(store.path)},
            )
            report = await client.call_tool(
                "aura_object_conformance",
                {
                    "baseline_run_ids": ["baseline"],
                    "candidate_run_ids": ["candidate"],
                    "db_path": str(store.path),
                },
            )
            tools = await client.list_tools()

        assert profile.structured_content["content_included"] is False
        assert report.structured_content["verdict"] == "fail"
        serialized = str(profile.structured_content) + str(report.structured_content)
        assert "ticket-secret" not in serialized
        assert "must-not-leak" not in serialized
        selected = {
            tool.name: tool
            for tool in tools.tools
            if tool.name in {"aura_object_behavior", "aura_object_conformance"}
        }
        assert set(selected) == {"aura_object_behavior", "aura_object_conformance"}
        assert all(tool.annotations.read_only_hint is True for tool in selected.values())

    asyncio.run(inspect())
