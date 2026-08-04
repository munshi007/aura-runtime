import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from aura_runtime.cli import app
from aura_runtime.flight import EnforcementMode, MCPFlightRecorder
from aura_runtime.models import AgentEvent, EventKind, GateAction
from aura_runtime.policy import AuraSpec
from aura_runtime.store import SQLiteEventStore
from aura_runtime.verifier import OnlineLTLfMonitor, RuntimeVerifier


def event(kind: EventKind, sequence: int, **kwargs: object) -> AgentEvent:
    return AgentEvent(
        run_id="run-ltlf",
        kind=kind,
        timestamp=datetime(2026, 8, 4, 12, 0, sequence, tzinfo=UTC),
        sequence=sequence,
        **kwargs,
    )


def spec(formula: str, *, effect: str = "deny") -> AuraSpec:
    return AuraSpec.model_validate(
        {
            "version": "0.1",
            "ltlf_policies": [
                {
                    "id": "lifecycle",
                    "description": "Finite-trace lifecycle rule",
                    "effect": effect,
                    "formula": formula,
                    "propositions": {
                        "delete": {
                            "event": "tool.call.requested",
                            "tool_matches": ["delete_*"],
                        },
                        "approval": {
                            "event": "human.approval",
                            "where": {"data.approved": True},
                        },
                    },
                    "proposition_control": {
                        "delete": "agent",
                        "approval": "environment",
                    },
                }
            ],
        }
    )


def test_formula_only_spec_and_undefined_proposition_validation() -> None:
    assert spec("G !delete").policies == []
    with pytest.raises(ValidationError, match="undefined propositions: missing"):
        spec("F missing")
    payload = spec("G !delete").model_dump(mode="json")
    payload["ltlf_policies"][0]["proposition_control"]["ghost"] = "agent"
    with pytest.raises(ValidationError, match="proposition_control references undefined"):
        AuraSpec.model_validate(payload)


def test_safety_violation_is_emitted_at_earliest_conclusive_prefix() -> None:
    monitor = OnlineLTLfMonitor(spec("G !delete"))

    findings = monitor.observe(
        event(EventKind.TOOL_CALL_REQUESTED, 0, tool_name="delete_customer")
    )

    assert len(findings) == 1
    assert findings[0].engine == "ltlf-progression-monitor-v0.1"
    assert monitor.report().policies[0].monitor.prefix_verdict == "permanently_violated"


def test_eventual_obligation_is_pending_then_decided_at_trace_end() -> None:
    monitor = OnlineLTLfMonitor(spec("F approval"))
    monitor.observe(event(EventKind.RUN_STARTED, 0))

    assert monitor.report().policies[0].monitor.prefix_verdict == "currently_violated"
    findings = monitor.finalize()

    assert len(findings) == 1
    assert monitor.report().policies[0].monitor.finite_trace_verdict == "fail"


def test_eventual_obligation_can_succeed_without_a_finding() -> None:
    events = [
        event(EventKind.RUN_STARTED, 0),
        event(EventKind.HUMAN_APPROVAL, 1, data={"approved": True}),
    ]

    assert RuntimeVerifier(spec("F approval")).verify(events) == []


def test_flight_recorder_can_deny_a_permanently_violating_action(tmp_path) -> None:
    recorder = MCPFlightRecorder(
        run_id="run-ltlf",
        store=SQLiteEventStore(tmp_path / "aura.db"),
        spec=spec("G !delete"),
        mode=EnforcementMode.ENFORCE,
    )

    result = recorder.handle_client_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "delete_customer", "arguments": {}},
        }
    )

    assert result.forward is False
    assert result.action == GateAction.DENY
    assert result.shield is not None
    assert result.shield.safe is False
    assert result.shield.policies[0].intervention == "suppress"
    assert result.response["error"]["data"]["aura.shield"]["safe"] is False
    assert result.findings[0].policy_id == "lifecycle"


def test_blocked_proposal_does_not_poison_accepted_ltlf_state(tmp_path) -> None:
    recorder = MCPFlightRecorder(
        run_id="run-ltlf",
        store=SQLiteEventStore(tmp_path / "aura.db"),
        spec=spec("G !delete"),
        mode=EnforcementMode.ENFORCE,
    )
    recorder.handle_client_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "delete_customer", "arguments": {}},
        }
    )

    report = recorder.ltlf_report()

    assert report.policies[0].monitor.prefix_verdict != "permanently_violated"
    assert report.policies[0].monitor.observed_event_count == 1


def test_environment_approval_is_guidance_not_a_controllable_repair() -> None:
    monitor = OnlineLTLfMonitor(spec("G approval"))
    proposed = event(EventKind.STATE_CHANGED, 0)

    report = monitor.preview(proposed)

    decision = report.policies[0]
    assert decision.intervention == "request_approval"
    assert decision.follow_up == "request_approval"
    assert decision.assessment.alternatives == []
    assert decision.assessment.environment_requirements == [["approval"]]


def test_repair_delays_action_while_waiting_for_environment_approval() -> None:
    monitor = OnlineLTLfMonitor(spec("(!delete) U approval"))
    proposed = event(EventKind.TOOL_CALL_REQUESTED, 0, tool_name="delete_customer")

    decision = monitor.preview(proposed).policies[0]

    assert decision.intervention == "delay"
    assert decision.follow_up == "request_approval"
    assert decision.assessment.alternatives[0].changed_propositions == ["delete"]


def test_runtime_strategy_report_distinguishes_guarantee_from_cooperation() -> None:
    agent = OnlineLTLfMonitor(spec("F delete")).strategy_report()
    environment = OnlineLTLfMonitor(spec("F approval")).strategy_report()

    assert agent.all_realizable is True
    assert agent.policies[0].strategy.status == "realizable"
    assert environment.all_realizable is False
    assert environment.policies[0].strategy.status == "cooperative_only"


def test_ltlf_state_cli_reports_prefix_and_final_verdict(tmp_path) -> None:
    db_path = tmp_path / "aura.db"
    SQLiteEventStore(db_path).append_event(event(EventKind.RUN_STARTED, 0))
    policy_path = tmp_path / "aura.yaml"
    policy_path.write_text(
        """version: "0.1"
ltlf_policies:
  - id: lifecycle
    description: Approval eventually occurs
    formula: F approval
    propositions:
      approval:
        event: human.approval
""",
        encoding="utf-8",
    )
    runner = CliRunner()

    prefix = runner.invoke(
        app, ["ltlf-state", "run-ltlf", "--policy", str(policy_path), "--db", str(db_path)]
    )
    final = runner.invoke(
        app,
        [
            "ltlf-state",
            "run-ltlf",
            "--policy",
            str(policy_path),
            "--db",
            str(db_path),
            "--final",
        ],
    )

    assert prefix.exit_code == 0
    prefix_monitor = json.loads(prefix.stdout)["policies"][0]["monitor"]
    assert prefix_monitor["prefix_verdict"] == "currently_violated"
    assert final.exit_code == 2
    final_monitor = json.loads(final.stdout)["policies"][0]["monitor"]
    assert final_monitor["finite_trace_verdict"] == "fail"


def test_shield_action_cli_returns_repairs_without_an_llm(tmp_path) -> None:
    db_path = tmp_path / "aura.db"
    SQLiteEventStore(db_path).append_event(event(EventKind.RUN_STARTED, 0))
    policy_path = tmp_path / "aura.yaml"
    policy_path.write_text(
        """version: "0.1"
ltlf_policies:
  - id: lifecycle
    description: Never delete
    formula: G !delete
    propositions:
      delete:
        event: tool.call.requested
        tool_matches: [delete_*]
    proposition_control:
      delete: agent
""",
        encoding="utf-8",
    )
    event_path = tmp_path / "proposed.json"
    proposed = event(EventKind.TOOL_CALL_REQUESTED, 1, tool_name="delete_customer")
    event_path.write_text(proposed.model_dump_json(), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "shield-action",
            "run-ltlf",
            "--policy",
            str(policy_path),
            "--event",
            str(event_path),
            "--db",
            str(db_path),
        ],
    )

    assert result.exit_code == 2
    report = json.loads(result.stdout)
    assert report["safe"] is False
    assert report["policies"][0]["assessment"]["alternatives"][0][
        "changed_propositions"
    ] == ["delete"]


def test_strategy_check_cli_fails_for_an_environment_only_goal(tmp_path) -> None:
    policy_path = tmp_path / "aura.yaml"
    policy_path.write_text(
        """version: "0.1"
ltlf_policies:
  - id: approval-goal
    description: Approval eventually occurs
    formula: F approval
    propositions:
      approval:
        event: human.approval
    proposition_control:
      approval: environment
""",
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["strategy-check", "--policy", str(policy_path)])

    assert result.exit_code == 2
    report = json.loads(result.stdout)
    assert report["all_realizable"] is False
    assert report["policies"][0]["strategy"]["status"] == "cooperative_only"
