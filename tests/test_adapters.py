from aura_runtime.adapters.mcp import MCPMessageRecorder
from aura_runtime.adapters.otel import events_from_otlp_json
from aura_runtime.models import EventKind


def test_mcp_request_response_correlation() -> None:
    recorder = MCPMessageRecorder("run-1")
    requested = recorder.record(
        "client_to_server",
        {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "lookup"}},
    )
    completed = recorder.record(
        "server_to_client", {"jsonrpc": "2.0", "id": 7, "result": {"value": 42}}
    )

    assert requested is not None and completed is not None
    assert requested.kind == EventKind.TOOL_CALL_REQUESTED
    assert completed.kind == EventKind.TOOL_CALL_COMPLETED
    assert completed.parent_event_id == requested.event_id


def test_otlp_span_normalization() -> None:
    payload = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [{"key": "service.name", "value": {"stringValue": "agent"}}]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "abc",
                                "spanId": "def",
                                "name": "execute_tool lookup",
                                "startTimeUnixNano": "1785758400000000000",
                                "endTimeUnixNano": "1785758401000000000",
                                "attributes": [
                                    {
                                        "key": "gen_ai.operation.name",
                                        "value": {"stringValue": "execute_tool"},
                                    },
                                    {
                                        "key": "gen_ai.tool.name",
                                        "value": {"stringValue": "lookup"},
                                    },
                                    {
                                        "key": "gen_ai.tool.call.arguments",
                                        "value": {"stringValue": "secret query"},
                                    },
                                ],
                                "status": {"code": "STATUS_CODE_OK"},
                            }
                        ]
                    }
                ],
            }
        ]
    }
    events = events_from_otlp_json(payload)
    assert len(events) == 2
    assert events[0].run_id == "abc"
    assert events[0].tool_name == "lookup"
    assert events[0].kind == EventKind.TOOL_CALL_REQUESTED
    assert events[1].kind == EventKind.TOOL_CALL_COMPLETED
    assert events[1].parent_event_id == events[0].event_id
    assert "gen_ai.tool.call.arguments" not in events[0].data["attributes"]
    assert events[0].data["dropped_attribute_names"] == ["gen_ai.tool.call.arguments"]


def test_otlp_invoke_agent_creates_run_lifecycle_with_stable_ids() -> None:
    span = {
        "traceId": "trace-1",
        "spanId": "span-1",
        "name": "invoke_agent planner",
        "startTimeUnixNano": "1785758400000000000",
        "endTimeUnixNano": "1785758402000000000",
        "attributes": {
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": "planner",
            "gen_ai.agent.id": "agent-7",
            "gen_ai.input.messages": "private prompt",
        },
    }
    payload = {"resourceSpans": [{"scopeSpans": [{"spans": [span]}]}]}

    first = events_from_otlp_json(payload)
    second = events_from_otlp_json(payload)

    assert [event.kind for event in first] == [
        EventKind.RUN_STARTED,
        EventKind.RUN_COMPLETED,
    ]
    assert first[0].actor == "planner"
    assert first[1].parent_event_id == first[0].event_id
    assert [event.event_id for event in first] == [event.event_id for event in second]
    assert "gen_ai.input.messages" not in first[0].data["attributes"]


def test_otlp_sequences_are_scoped_per_run() -> None:
    def span(trace_id: str, span_id: str) -> dict[str, object]:
        return {
            "traceId": trace_id,
            "spanId": span_id,
            "startTimeUnixNano": "1785758400000000000",
            "attributes": {"gen_ai.operation.name": "chat"},
        }

    payload = {
        "resourceSpans": [
            {"scopeSpans": [{"spans": [span("run-a", "one"), span("run-b", "two")]}]}
        ]
    }

    events = events_from_otlp_json(payload)

    assert [event.sequence for event in events if event.run_id == "run-a"] == [0, 1]
    assert [event.sequence for event in events if event.run_id == "run-b"] == [0, 1]
