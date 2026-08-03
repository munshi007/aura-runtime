"""Protocol adapters that normalize external telemetry into Aura events."""

from aura_runtime.adapters.mcp import MCPMessageRecorder
from aura_runtime.adapters.otel import events_from_otlp_json

__all__ = ["MCPMessageRecorder", "events_from_otlp_json"]
