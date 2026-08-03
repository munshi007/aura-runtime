"""MCP JSON-RPC evidence adapter independent of a particular transport."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from aura_runtime.models import AgentEvent, EventKind

Direction = Literal["client_to_server", "server_to_client"]


class MCPMessageRecorder:
    """Correlate MCP tool requests and responses into canonical events."""

    def __init__(self, run_id: str, *, source: str = "mcp") -> None:
        self.run_id = run_id
        self.source = source
        self._sequence = 0
        self._pending_tools: dict[str, tuple[str | None, UUID]] = {}

    def record(self, direction: Direction, message: dict[str, Any]) -> AgentEvent | None:
        request_id = str(message.get("id", ""))
        method = message.get("method")

        if direction == "client_to_server" and method == "tools/call":
            params = message.get("params") or {}
            tool_name = params.get("name")
            event = self._event(
                EventKind.TOOL_CALL_REQUESTED,
                tool_name=tool_name,
                data={
                    "arguments": params.get("arguments", {}),
                    "mcp.request_id": request_id,
                    "mcp.method.name": method,
                },
            )
            self._pending_tools[request_id] = (tool_name, event.event_id)
            return event

        if direction == "server_to_client" and request_id in self._pending_tools:
            tool_name, parent_event_id = self._pending_tools.pop(request_id)
            failed = "error" in message
            return self._event(
                EventKind.TOOL_CALL_FAILED if failed else EventKind.TOOL_CALL_COMPLETED,
                tool_name=tool_name,
                parent_event_id=parent_event_id,
                data={
                    "result": message.get("result"),
                    "error": message.get("error"),
                    "mcp.request_id": request_id,
                },
            )

        return None

    def _event(
        self,
        kind: EventKind,
        *,
        tool_name: str | None,
        data: dict[str, Any],
        parent_event_id: UUID | None = None,
    ) -> AgentEvent:
        event = AgentEvent(
            run_id=self.run_id,
            kind=kind,
            source=self.source,
            sequence=self._sequence,
            tool_name=tool_name,
            parent_event_id=parent_event_id,
            data=data,
        )
        self._sequence += 1
        return event
