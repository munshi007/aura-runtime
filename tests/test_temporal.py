from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from aura_runtime.cli import app
from aura_runtime.models import AgentEvent, EventKind
from aura_runtime.policy import AuraSpec
from aura_runtime.store import SQLiteEventStore
from aura_runtime.verifier import OnlineTemporalMonitor, RuntimeVerifier


def event(kind: EventKind, sequence: int, **kwargs: object) -> AgentEvent:
    return AgentEvent(
        run_id="run-1",
        kind=kind,
        timestamp=datetime(2026, 8, 4, 12, 0, sequence, tzinfo=UTC),
        sequence=sequence,
        **kwargs,
    )


def response_spec(*, within_events: int = 2) -> AuraSpec:
    return AuraSpec.model_validate(
        {
            "version": "0.1",
            "policies": [
                {
                    "id": "approval-response",
                    "description": "Deletion must receive a later approval",
                    "severity": "critical",
                    "on": {
                        "event": "tool.call.requested",
                        "tool_matches": ["delete_*"],
                    },
                    "require_after": {
                        "event": "human.approval",
                        "within_events": within_events,
                        "where": {"data.approved": True},
                    },
                }
            ],
        }
    )


def test_bounded_response_moves_from_pending_to_satisfied() -> None:
    monitor = OnlineTemporalMonitor(response_spec())

    assert monitor.observe(
        event(EventKind.TOOL_CALL_REQUESTED, 0, tool_name="delete_customer")
    ) == []
    pending = monitor.report()
    assert pending.pending_obligation_count == 1
    assert pending.pending[0].remaining_events == 2
    assert pending.pending[0].verdict == "pending"

    assert monitor.observe(
        event(EventKind.HUMAN_APPROVAL, 1, data={"approved": True})
    ) == []
    satisfied = monitor.report()
    assert satisfied.pending_obligation_count == 0
    assert satisfied.satisfied_obligation_count == 1
    assert satisfied.violated_obligation_count == 0


def test_bounded_response_violates_at_earliest_conclusive_event() -> None:
    monitor = OnlineTemporalMonitor(response_spec(within_events=2))
    trigger = event(EventKind.TOOL_CALL_REQUESTED, 0, tool_name="delete_customer")
    first = event(EventKind.STATE_CHANGED, 1)
    deadline = event(EventKind.MODEL_REQUESTED, 2)

    assert monitor.observe(trigger) == []
    assert monitor.observe(first) == []
    findings = monitor.observe(deadline)

    assert len(findings) == 1
    assert findings[0].event_id == deadline.event_id
    assert findings[0].evidence_event_ids == [
        trigger.event_id,
        first.event_id,
        deadline.event_id,
    ]
    assert findings[0].engine == "bounded-response-monitor"
    assert monitor.report().violated_obligation_count == 1


def test_finite_trace_finalization_violates_unresolved_obligation() -> None:
    trigger = event(EventKind.TOOL_CALL_REQUESTED, 0, tool_name="delete_customer")
    monitor = OnlineTemporalMonitor(response_spec(within_events=5))
    monitor.observe(trigger)

    findings = monitor.finalize()

    assert len(findings) == 1
    assert findings[0].event_id == trigger.event_id
    assert "finite trace ended" in findings[0].message
    assert monitor.report().finalized is True
    assert monitor.finalize() == []


def test_run_completed_event_finalizes_pending_obligations() -> None:
    monitor = OnlineTemporalMonitor(response_spec(within_events=5))
    monitor.observe(event(EventKind.TOOL_CALL_REQUESTED, 0, tool_name="delete_customer"))
    completed = event(EventKind.RUN_COMPLETED, 1)

    findings = monitor.observe(completed)

    assert len(findings) == 1
    assert findings[0].event_id == completed.event_id
    assert monitor.report().finalized is True


def test_one_response_can_satisfy_overlapping_response_obligations() -> None:
    monitor = OnlineTemporalMonitor(response_spec(within_events=3))
    monitor.observe(event(EventKind.TOOL_CALL_REQUESTED, 0, tool_name="delete_one"))
    monitor.observe(event(EventKind.TOOL_CALL_REQUESTED, 1, tool_name="delete_two"))

    monitor.observe(event(EventKind.HUMAN_APPROVAL, 2, data={"approved": True}))

    report = monitor.report()
    assert report.satisfied_obligation_count == 2
    assert report.pending_obligation_count == 0


def test_future_response_can_be_bound_to_its_trigger_identity() -> None:
    spec = AuraSpec.model_validate(
        {
            "version": "0.1",
            "policies": [
                {
                    "id": "correlated-completion",
                    "description": "The same call must complete",
                    "on": {"event": "tool.call.requested"},
                    "require_after": {
                        "event": "tool.call.completed",
                        "within_events": 2,
                        "correlate": {"parent_event_id": "event_id"},
                    },
                }
            ],
        }
    )
    trigger = event(EventKind.TOOL_CALL_REQUESTED, 0, tool_name="delete_customer")
    monitor = OnlineTemporalMonitor(spec)
    monitor.observe(trigger)

    monitor.observe(
        event(
            EventKind.TOOL_CALL_COMPLETED,
            1,
            tool_name="delete_customer",
            parent_event_id=uuid4(),
        )
    )
    assert monitor.report().pending_obligation_count == 1

    monitor.observe(
        event(
            EventKind.TOOL_CALL_COMPLETED,
            2,
            tool_name="delete_customer",
            parent_event_id=trigger.event_id,
        )
    )
    assert monitor.report().satisfied_obligation_count == 1


def test_complete_trace_verifier_finalizes_future_obligations() -> None:
    findings = RuntimeVerifier(response_spec(within_events=5)).verify(
        [event(EventKind.TOOL_CALL_REQUESTED, 0, tool_name="delete_customer")]
    )

    assert len(findings) == 1
    assert findings[0].engine == "bounded-response-monitor"


def test_require_after_requires_an_explicit_bound() -> None:
    payload = response_spec().model_dump(mode="json")
    del payload["policies"][0]["require_after"]["within_events"]

    with pytest.raises(ValueError, match="require_after must define within_events"):
        AuraSpec.model_validate(payload)


def test_temporal_report_does_not_expose_constraint_values() -> None:
    spec = AuraSpec.model_validate(
        {
            "version": "0.1",
            "policies": [
                {
                    "id": "secret-check",
                    "description": "Check a value",
                    "on": {"event": "state.changed"},
                    "constraints": [{"path": "data.secret", "op": "==", "value": "allowed"}],
                }
            ],
        }
    )
    monitor = OnlineTemporalMonitor(spec)

    immediate = monitor.observe(event(EventKind.STATE_CHANGED, 0, data={"secret": "private"}))

    assert len(immediate) == 1
    assert "private" in immediate[0].message
    assert monitor.report().findings == []
    assert "private" not in monitor.report().model_dump_json()


def test_temporal_state_cli_preserves_pending_prefix(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "aura.db")
    store.append_event(event(EventKind.TOOL_CALL_REQUESTED, 0, tool_name="delete_customer"))
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        """
version: "0.1"
policies:
  - id: approval-response
    description: Deletion must receive a later approval
    on:
      event: tool.call.requested
      tool_matches: [delete_*]
    require_after:
      event: human.approval
      within_events: 2
      where:
        data.approved: true
""",
        encoding="utf-8",
    )

    prefix = CliRunner().invoke(
        app,
        [
            "temporal-state",
            "run-1",
            "--db",
            str(store.path),
            "--policy",
            str(policy),
        ],
    )
    final = CliRunner().invoke(
        app,
        [
            "temporal-state",
            "run-1",
            "--db",
            str(store.path),
            "--policy",
            str(policy),
            "--final",
        ],
    )

    assert prefix.exit_code == 0, prefix.output
    assert '"pending_obligation_count": 1' in prefix.output
    assert final.exit_code == 2
    assert '"violated_obligation_count": 1' in final.output
