"""Policy-aware MCP flight recorder and decision engine."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from aura_runtime.adapters.mcp import MCPMessageRecorder
from aura_runtime.models import (
    AgentEvent,
    Finding,
    GateAction,
    ProtocolRecord,
    ToolManifestSnapshot,
)
from aura_runtime.policy import AuraSpec
from aura_runtime.store import SQLiteEventStore
from aura_runtime.verifier import OnlineTemporalMonitor, TemporalMonitorReport


class EnforcementMode(StrEnum):
    OBSERVE = "observe"
    ENFORCE = "enforce"


class GatewayResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    forward: bool
    action: GateAction
    findings: list[Finding] = Field(default_factory=list)
    response: dict[str, Any] | None = None


def verify_protocol_chain(records: list[ProtocolRecord]) -> bool:
    previous_hash = ""
    for expected_sequence, record in enumerate(records):
        if record.sequence != expected_sequence or record.previous_hash != previous_hash:
            return False
        rebuilt = ProtocolRecord.create(
            run_id=record.run_id,
            sequence=record.sequence,
            direction=record.direction,
            message=record.message,
            forwarded=record.forwarded,
            action=record.action,
            previous_hash=record.previous_hash,
        )
        if rebuilt.content_hash != record.content_hash:
            return False
        previous_hash = record.content_hash
    return True


class MCPFlightRecorder:
    """Record, correlate, and optionally gate one MCP connection."""

    def __init__(
        self,
        *,
        run_id: str,
        store: SQLiteEventStore,
        spec: AuraSpec | None = None,
        mode: EnforcementMode = EnforcementMode.OBSERVE,
    ) -> None:
        self.run_id = run_id
        self.store = store
        self.mode = mode
        self.spec = spec
        self.message_recorder = MCPMessageRecorder(run_id, source="mcp-flight-recorder")
        self.history = store.events(run_id)
        self.monitor = OnlineTemporalMonitor(spec) if spec else None
        if self.monitor is not None:
            for previous_event in self.history:
                self.monitor.observe(previous_event)
        existing_records = store.protocol_records(run_id)
        self._protocol_sequence = len(existing_records)
        self._previous_hash = existing_records[-1].content_hash if existing_records else ""
        self._pending_methods: dict[str, str] = {}

    def handle_client_message(self, message: dict[str, Any]) -> GatewayResult:
        request_id = str(message.get("id", ""))
        method = message.get("method")
        if request_id and isinstance(method, str):
            self._pending_methods[request_id] = method

        event = self.message_recorder.record("client_to_server", message)
        findings: list[Finding] = []
        if event is not None:
            findings = self._record_event(event)

        action = self._enforcement_action(findings)
        forward = self.mode == EnforcementMode.OBSERVE or action == GateAction.ALLOW
        effective_action = GateAction.ALLOW if forward else action
        self._append_protocol("client_to_server", message, forward, effective_action)

        if forward:
            return GatewayResult(forward=True, action=effective_action, findings=findings)

        response = self._blocked_response(message, action, findings)
        self._append_protocol("server_to_client", response, False, action)
        failed_event = self.message_recorder.record("server_to_client", response)
        if failed_event is not None:
            self._record_event(failed_event)
        self._pending_methods.pop(request_id, None)
        return GatewayResult(
            forward=False,
            action=action,
            findings=findings,
            response=response,
        )

    def handle_server_message(self, message: dict[str, Any]) -> None:
        self._append_protocol("server_to_client", message, True, GateAction.ALLOW)
        event = self.message_recorder.record("server_to_client", message)
        if event is not None:
            self._record_event(event)

        request_id = str(message.get("id", ""))
        method = self._pending_methods.pop(request_id, None)
        result = message.get("result") or {}
        if method == "tools/list" and isinstance(result.get("tools"), list):
            self.store.append_manifest(
                ToolManifestSnapshot.create(
                    run_id=self.run_id,
                    request_id=request_id,
                    tools=result["tools"],
                )
            )

    def temporal_report(self) -> TemporalMonitorReport | None:
        """Return the current content-free temporal monitor state, when configured."""
        return self.monitor.report() if self.monitor is not None else None

    def _record_event(self, event: AgentEvent) -> list[Finding]:
        findings = self.monitor.observe(event) if self.monitor is not None else []
        self.store.append_event(event)
        self.history.append(event)
        for finding in findings:
            self.store.append_finding(finding)
        return findings

    def _enforcement_action(self, findings: list[Finding]) -> GateAction:
        if not findings or self.spec is None:
            return GateAction.ALLOW
        effects = {
            policy.id: policy.effect
            for policy in self.spec.policies
            if policy.id in {finding.policy_id for finding in findings}
        }
        if "deny" in effects.values():
            return GateAction.DENY
        return GateAction.REQUIRE_APPROVAL

    def _append_protocol(
        self,
        direction: str,
        message: dict[str, Any],
        forwarded: bool,
        action: GateAction,
    ) -> None:
        record = ProtocolRecord.create(
            run_id=self.run_id,
            sequence=self._protocol_sequence,
            direction=direction,
            message=message,
            forwarded=forwarded,
            action=action,
            previous_hash=self._previous_hash,
        )
        self.store.append_protocol_record(record)
        self._protocol_sequence += 1
        self._previous_hash = record.content_hash

    @staticmethod
    def _blocked_response(
        request: dict[str, Any], action: GateAction, findings: list[Finding]
    ) -> dict[str, Any]:
        approval = action == GateAction.REQUIRE_APPROVAL
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "error": {
                "code": -32043 if approval else -32042,
                "message": "Aura approval required" if approval else "Aura policy denied tool call",
                "data": {
                    "aura.action": action.value,
                    "aura.findings": [finding.model_dump(mode="json") for finding in findings],
                },
            },
        }
