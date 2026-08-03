from datetime import UTC, datetime

from aura_runtime.models import AgentEvent, EventKind
from aura_runtime.policy import AuraSpec
from aura_runtime.verifier import RuntimeVerifier


def event(kind: EventKind, sequence: int, **kwargs: object) -> AgentEvent:
    return AgentEvent(
        run_id="run-1",
        kind=kind,
        timestamp=datetime(2026, 8, 3, 12, 0, sequence, tzinfo=UTC),
        sequence=sequence,
        **kwargs,
    )


def spec() -> AuraSpec:
    return AuraSpec.model_validate(
        {
            "version": "0.1",
            "policies": [
                {
                    "id": "approval",
                    "description": "Require approval",
                    "severity": "critical",
                    "on": {"event": "tool.call.requested", "tool_matches": ["delete_*"]},
                    "require_prior": {
                        "event": "human.approval",
                        "within_events": 5,
                        "where": {"data.approved": True},
                    },
                },
                {
                    "id": "limit",
                    "description": "Check amount",
                    "on": {"event": "tool.call.requested", "tool_matches": ["transfer_funds"]},
                    "constraints": [{"path": "data.arguments.amount", "op": "<=", "value": 10}],
                },
            ],
        }
    )


def test_missing_approval_emits_finding() -> None:
    findings = RuntimeVerifier(spec()).verify(
        [event(EventKind.TOOL_CALL_REQUESTED, 0, tool_name="delete_customer")]
    )
    assert len(findings) == 1
    assert findings[0].policy_id == "approval"
    assert findings[0].engine == "bounded-temporal-monitor"


def test_prior_approval_satisfies_policy() -> None:
    findings = RuntimeVerifier(spec()).verify(
        [
            event(EventKind.HUMAN_APPROVAL, 0, data={"approved": True}),
            event(EventKind.TOOL_CALL_REQUESTED, 1, tool_name="delete_customer"),
        ]
    )
    assert findings == []


def test_data_constraint_emits_finding() -> None:
    findings = RuntimeVerifier(spec()).verify(
        [
            event(
                EventKind.TOOL_CALL_REQUESTED,
                0,
                tool_name="transfer_funds",
                data={"arguments": {"amount": 11}},
            )
        ]
    )
    assert len(findings) == 1
    assert findings[0].policy_id == "limit"
    assert "observed 11" in findings[0].message
