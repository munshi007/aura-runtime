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
                                "attributes": [
                                    {
                                        "key": "gen_ai.operation.name",
                                        "value": {"stringValue": "execute_tool"},
                                    },
                                    {
                                        "key": "gen_ai.tool.name",
                                        "value": {"stringValue": "lookup"},
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
    assert len(events) == 1
    assert events[0].run_id == "abc"
    assert events[0].tool_name == "lookup"
    assert events[0].kind == EventKind.TOOL_CALL_COMPLETED
