from aura_runtime.flight import EnforcementMode, MCPFlightRecorder, verify_protocol_chain
from aura_runtime.models import GateAction
from aura_runtime.policy import AuraSpec
from aura_runtime.store import SQLiteEventStore


def approval_spec() -> AuraSpec:
    return AuraSpec.model_validate(
        {
            "version": "0.1",
            "policies": [
                {
                    "id": "approval",
                    "description": "Require approval before deletion",
                    "effect": "require_approval",
                    "on": {"event": "tool.call.requested", "tool_matches": ["delete_*"]},
                    "require_prior": {
                        "event": "human.approval",
                        "where": {"data.approved": True},
                    },
                }
            ],
        }
    )


def completion_spec() -> AuraSpec:
    return AuraSpec.model_validate(
        {
            "version": "0.1",
            "policies": [
                {
                    "id": "tool-completes",
                    "description": "A tool call must complete on the next observed event",
                    "on": {"event": "tool.call.requested"},
                    "require_after": {
                        "event": "tool.call.completed",
                        "within_events": 1,
                        "correlate": {"parent_event_id": "event_id"},
                    },
                }
            ],
        }
    )


def call(tool: str = "delete_customer", request_id: int = 1) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": tool, "arguments": {"customer_id": "cus_123"}},
    }


def test_observe_mode_records_but_forwards(tmp_path) -> None:
    store = SQLiteEventStore(tmp_path / "aura.db")
    recorder = MCPFlightRecorder(
        run_id="run-1",
        store=store,
        spec=approval_spec(),
        mode=EnforcementMode.OBSERVE,
    )

    result = recorder.handle_client_message(call())

    assert result.forward is True
    assert result.action == GateAction.ALLOW
    assert len(result.findings) == 1
    assert len(store.findings("run-1")) == 1
    assert verify_protocol_chain(store.protocol_records("run-1"))


def test_enforce_mode_returns_approval_response_without_forwarding(tmp_path) -> None:
    store = SQLiteEventStore(tmp_path / "aura.db")
    recorder = MCPFlightRecorder(
        run_id="run-1",
        store=store,
        spec=approval_spec(),
        mode=EnforcementMode.ENFORCE,
    )

    result = recorder.handle_client_message(call())

    assert result.forward is False
    assert result.action == GateAction.REQUIRE_APPROVAL
    assert result.response is not None
    assert result.response["error"]["code"] == -32043
    records = store.protocol_records("run-1")
    assert len(records) == 2
    assert records[0].forwarded is False
    assert verify_protocol_chain(records)


def test_tool_manifest_is_snapshotted(tmp_path) -> None:
    store = SQLiteEventStore(tmp_path / "aura.db")
    recorder = MCPFlightRecorder(run_id="run-1", store=store)
    request = {"jsonrpc": "2.0", "id": 9, "method": "tools/list", "params": {}}
    response = {
        "jsonrpc": "2.0",
        "id": 9,
        "result": {"tools": [{"name": "search", "inputSchema": {"type": "object"}}]},
    }

    assert recorder.handle_client_message(request).forward is True
    recorder.handle_server_message(response)

    snapshots = store.manifests("run-1")
    assert len(snapshots) == 1
    assert snapshots[0].tools[0]["name"] == "search"
    assert len(snapshots[0].content_hash) == 64


def test_flight_recorder_monitors_server_responses_online(tmp_path) -> None:
    store = SQLiteEventStore(tmp_path / "aura.db")
    recorder = MCPFlightRecorder(run_id="run-1", store=store, spec=completion_spec())

    recorder.handle_client_message(call(tool="lookup"))
    pending = recorder.temporal_report()
    assert pending is not None
    assert pending.pending_obligation_count == 1

    recorder.handle_server_message({"jsonrpc": "2.0", "id": 1, "result": {"value": 42}})

    report = recorder.temporal_report()
    assert report is not None
    assert report.pending_obligation_count == 0
    assert report.satisfied_obligation_count == 1
    assert store.findings("run-1") == []


def test_hash_chain_detects_tampering(tmp_path) -> None:
    store = SQLiteEventStore(tmp_path / "aura.db")
    recorder = MCPFlightRecorder(run_id="run-1", store=store)
    recorder.handle_client_message({"jsonrpc": "2.0", "id": 9, "method": "tools/list"})
    records = store.protocol_records("run-1")

    tampered = records[0].model_copy(update={"message": {"method": "tools/call"}})
    assert verify_protocol_chain([tampered]) is False
