import asyncio
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mcp import Client
from typer.testing import CliRunner

from aura_runtime.cli import app
from aura_runtime.flight import EnforcementMode, MCPFlightRecorder
from aura_runtime.mcp_server import mcp
from aura_runtime.models import AgentEvent, EventKind, GateAction, ObjectRef
from aura_runtime.object_contract import ObjectContract, ObjectContractMonitor
from aura_runtime.object_process import discover_object_behavior
from aura_runtime.policy import AuraSpec
from aura_runtime.store import SQLiteEventStore


def object_event(
    run_id: str,
    sequence: int,
    tool: str,
    *,
    customer: bool = False,
) -> AgentEvent:
    objects = [ObjectRef(object_type="ticket", object_id="ticket-secret")]
    if customer:
        objects.append(ObjectRef(object_type="customer", object_id="customer-secret"))
    return AgentEvent(
        run_id=run_id,
        kind=EventKind.TOOL_CALL_REQUESTED,
        timestamp=datetime(2026, 8, 4, 12, tzinfo=UTC) + timedelta(seconds=sequence),
        sequence=sequence,
        tool_name=tool,
        objects=objects,
        data={"arguments": {"secret": "must-not-leak"}},
    )


def trusted_events(run_id: str = "trusted") -> list[AgentEvent]:
    return [
        object_event(run_id, 0, "read_ticket"),
        object_event(run_id, 1, "notify_customer", customer=True),
        object_event(run_id, 2, "close_ticket"),
    ]


def contract() -> ObjectContract:
    return ObjectContract.from_profile(discover_object_behavior(trusted_events()))


def binding_spec() -> AuraSpec:
    selectors = ["read_ticket", "notify_customer", "close_ticket", "delete_ticket"]
    return AuraSpec.model_validate(
        {
            "version": "0.1",
            "policies": [
                {
                    "id": "unused-control",
                    "description": "A control that does not match the fixture tools",
                    "on": {
                        "event": "tool.call.requested",
                        "tool_matches": ["never_*"],
                    },
                    "constraints": [
                        {"path": "data.arguments.allowed", "op": "==", "value": True}
                    ],
                }
            ],
            "object_bindings": [
                {
                    "on": {
                        "event": "tool.call.requested",
                        "tool_matches": selectors,
                    },
                    "object_type": "ticket",
                    "id_path": "data.arguments.ticket_id",
                    "qualifier": "subject",
                }
            ],
        }
    )


def call(tool: str, request_id: int) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": tool, "arguments": {"ticket_id": "ticket-secret"}},
    }


def test_contract_is_deterministic_and_detects_tampering() -> None:
    first = contract()
    second = contract()

    assert first.contract_hash == second.contract_hash
    assert len(first.contract_hash) == 64
    raw = first.model_dump(mode="json")
    raw["effect"] = "require_approval"
    try:
        ObjectContract.model_validate(raw)
    except ValueError as error:
        assert "hash does not match" in str(error)
    else:
        raise AssertionError("tampered contract should be rejected")


def test_monitor_reports_a_pseudonymous_minimal_counterexample() -> None:
    monitor = ObjectContractMonitor(contract())
    first = trusted_events()[0]
    monitor.observe(first, commit=True)
    invalid = object_event("candidate", 1, "delete_ticket")

    findings = monitor.observe(invalid, commit=False)
    report = monitor.report()

    assert len(findings) == 1
    assert findings[0].engine == "object-contract-v0.1"
    assert report.violation_count == 1
    violation = report.violations[0]
    assert violation.code == "invalid_transition"
    assert violation.previous_activity == "tool.call.requested:read_ticket"
    assert violation.expected_activities == ["tool.call.requested:notify_customer"]
    assert violation.evidence_event_ids == [first.event_id]
    assert report.states[0].last_activity == "tool.call.requested:read_ticket"
    serialized = report.model_dump_json()
    assert "ticket-secret" not in serialized
    assert "must-not-leak" not in serialized


def test_monitor_checks_terminal_state_and_interactions() -> None:
    value = contract()
    monitor = ObjectContractMonitor(value)
    first = trusted_events()[0]
    monitor.observe(first, commit=True)

    endings = monitor.finalize()
    assert len(endings) == 1
    assert endings[0].code == "invalid_end"
    assert endings[0].expected_activities == ["tool.call.requested:close_ticket"]

    interaction_monitor = ObjectContractMonitor(value)
    invalid = object_event("candidate", 0, "read_ticket", customer=True)
    findings = interaction_monitor.observe(invalid, commit=False)
    assert {item.policy_id for item in findings} == {
        f"object-contract.{value.contract_hash[:16]}"
    }
    assert {item.code for item in interaction_monitor.report().violations} == {
        "invalid_start",
        "invalid_interaction",
    }


def test_flight_recorder_blocks_without_advancing_object_state(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "aura.db")
    recorder = MCPFlightRecorder(
        run_id="candidate",
        store=store,
        spec=binding_spec(),
        object_contract=contract(),
        mode=EnforcementMode.ENFORCE,
    )

    allowed = recorder.handle_client_message(call("read_ticket", 1))
    blocked = recorder.handle_client_message(call("delete_ticket", 2))

    assert allowed.forward is True
    assert blocked.forward is False
    assert blocked.action == GateAction.DENY
    assert blocked.response is not None
    assert blocked.response["error"]["code"] == -32042
    assert store.protocol_records("candidate")[-2].forwarded is False
    report = recorder.object_report()
    assert report is not None
    assert report.states[0].last_activity == "tool.call.requested:read_ticket"
    assert report.violations[0].code == "invalid_transition"
    assert len(store.findings("candidate")) == 1


def test_flight_recorder_denies_a_matched_binding_without_an_identifier(tmp_path: Path) -> None:
    recorder = MCPFlightRecorder(
        run_id="missing-id",
        store=SQLiteEventStore(tmp_path / "aura.db"),
        spec=binding_spec(),
        object_contract=contract(),
        mode=EnforcementMode.ENFORCE,
    )
    message = call("read_ticket", 1)
    message["params"]["arguments"] = {}

    result = recorder.handle_client_message(message)

    assert result.forward is False
    assert result.action == GateAction.DENY
    report = recorder.object_report()
    assert report is not None
    assert report.states == []
    assert report.violations[0].code == "missing_object_binding"
    assert "identifier" in report.violations[0].message


def test_observe_mode_records_violation_and_advances_executed_state(tmp_path: Path) -> None:
    recorder = MCPFlightRecorder(
        run_id="candidate",
        store=SQLiteEventStore(tmp_path / "aura.db"),
        spec=binding_spec(),
        object_contract=contract(),
        mode=EnforcementMode.OBSERVE,
    )
    recorder.handle_client_message(call("read_ticket", 1))
    result = recorder.handle_client_message(call("delete_ticket", 2))

    assert result.forward is True
    report = recorder.object_report()
    assert report is not None
    assert report.states[0].last_activity == "tool.call.requested:delete_ticket"
    assert report.violation_count == 1


def test_real_stdio_proxy_blocks_invalid_object_transition(tmp_path: Path) -> None:
    contract_path = tmp_path / "object-contract.json"
    contract_path.write_text(contract().model_dump_json(indent=2), encoding="utf-8")
    policy_path = tmp_path / "aura.yaml"
    policy_path.write_text(
        """version: "0.1"
policies:
  - id: unused-control
    description: Does not match this scenario
    on:
      event: tool.call.requested
      tool_matches: [never_*]
    constraints:
      - path: data.arguments.allowed
        op: "=="
        value: true
object_bindings:
  - on:
      event: tool.call.requested
      tool_matches: [read_ticket, delete_ticket]
    object_type: ticket
    id_path: data.arguments.ticket_id
    qualifier: subject
""",
        encoding="utf-8",
    )
    upstream = "import sys; [(sys.stdout.write(line), sys.stdout.flush()) for line in sys.stdin]"
    messages = [call("read_ticket", 1), call("delete_ticket", 2)]

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "aura_runtime.cli",
            "proxy",
            "--db",
            str(tmp_path / "aura.db"),
            "--run-id",
            "stdio-object-run",
            "--policy",
            str(policy_path),
            "--object-contract",
            str(contract_path),
            "--mode",
            "enforce",
            "--",
            sys.executable,
            "-c",
            upstream,
        ],
        input="\n".join(json.dumps(message) for message in messages) + "\n",
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    responses = [json.loads(line) for line in completed.stdout.splitlines()]
    by_id = {response["id"]: response for response in responses}
    assert by_id[1] == messages[0]
    assert by_id[2]["error"]["code"] == -32042
    assert by_id[2]["error"]["data"]["aura.action"] == "deny"


def test_contract_cli_create_check_and_state(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "aura.db")
    for item in trusted_events():
        store.append_event(item)
    invalid = [trusted_events("candidate")[0], object_event("candidate", 1, "delete_ticket")]
    for item in invalid:
        store.append_event(item)
    output = tmp_path / "object-contract.json"

    created = CliRunner().invoke(
        app,
        [
            "objects",
            "contract",
            "create",
            "--baseline-run",
            "trusted",
            "--db",
            str(store.path),
            "--output",
            str(output),
        ],
    )
    checked = CliRunner().invoke(
        app,
        [
            "objects",
            "contract",
            "check",
            str(output),
            "--run",
            "candidate",
            "--db",
            str(store.path),
        ],
    )
    state = CliRunner().invoke(
        app,
        [
            "objects",
            "state",
            "trusted",
            "--contract",
            str(output),
            "--db",
            str(store.path),
        ],
    )

    assert created.exit_code == 0, created.output
    assert len(json.loads(output.read_text())["contract_hash"]) == 64
    assert checked.exit_code == 2
    assert json.loads(checked.output)["violation_count"] >= 1
    assert state.exit_code == 0, state.output
    assert json.loads(state.output)["states"][0]["object_key"].startswith("aura:")


def test_mcp_contract_tools_are_read_only_and_content_free(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "aura.db")
    for item in trusted_events():
        store.append_event(item)
    contract_json = contract().model_dump_json()

    async def inspect() -> None:
        async with Client(mcp) as client:
            metadata = await client.call_tool(
                "aura_object_contract", {"contract_json": contract_json}
            )
            state = await client.call_tool(
                "aura_object_state",
                {
                    "run_id": "trusted",
                    "contract_json": contract_json,
                    "db_path": str(store.path),
                    "final": True,
                },
            )
            tools = await client.list_tools()

        assert metadata.structured_content["valid"] is True
        assert state.structured_content["violation_count"] == 0
        assert "ticket-secret" not in str(state.structured_content)
        selected = {
            tool.name: tool
            for tool in tools.tools
            if tool.name in {"aura_object_contract", "aura_object_state"}
        }
        assert set(selected) == {"aura_object_contract", "aura_object_state"}
        assert all(tool.annotations.read_only_hint is True for tool in selected.values())

    asyncio.run(inspect())
