from aura_runtime.models import AgentEvent, EventKind, ToolManifestSnapshot
from aura_runtime.policy import AuraSpec
from aura_runtime.replay import compare_manifests, compare_runs, replay_run
from aura_runtime.store import SQLiteEventStore
from aura_runtime.verifier import RuntimeVerifier


def limit_spec(limit: int = 10) -> AuraSpec:
    return AuraSpec.model_validate(
        {
            "version": "0.1",
            "policies": [
                {
                    "id": "limit",
                    "description": "Transfer limit",
                    "on": {
                        "event": "tool.call.requested",
                        "tool_matches": ["transfer_funds"],
                    },
                    "constraints": [{"path": "data.arguments.amount", "op": "<=", "value": limit}],
                }
            ],
        }
    )


def transfer(run_id: str, amount: int, sequence: int = 0) -> AgentEvent:
    return AgentEvent(
        run_id=run_id,
        kind=EventKind.TOOL_CALL_REQUESTED,
        tool_name="transfer_funds",
        sequence=sequence,
        data={"arguments": {"amount": amount}},
    )


def test_replay_is_read_only_and_matches_recorded_findings(tmp_path) -> None:
    store = SQLiteEventStore(tmp_path / "aura.db")
    event = transfer("run-1", 11)
    store.append_event(event)
    for finding in RuntimeVerifier(limit_spec()).verify([event]):
        store.append_finding(finding)
    before_events = store.events("run-1")
    before_findings = store.findings("run-1")

    report = replay_run(store, "run-1", limit_spec())

    assert report.introduced == []
    assert report.resolved == []
    assert len(report.unchanged) == 1
    assert report.read_only is True
    assert store.events("run-1") == before_events
    assert store.findings("run-1") == before_findings


def test_replay_finds_counterfactual_violation(tmp_path) -> None:
    store = SQLiteEventStore(tmp_path / "aura.db")
    store.append_event(transfer("run-1", 8))

    report = replay_run(store, "run-1", limit_spec(limit=5))

    assert len(report.introduced) == 1
    assert report.introduced[0].policy_id == "limit"


def test_run_diff_locates_first_behavioral_divergence(tmp_path) -> None:
    store = SQLiteEventStore(tmp_path / "aura.db")
    for run_id, amount in (("left", 5), ("right", 9)):
        store.append_event(AgentEvent(run_id=run_id, kind=EventKind.RUN_STARTED, sequence=0))
        store.append_event(transfer(run_id, amount, sequence=1))

    report = compare_runs(store, "left", "right")

    assert report.identical is False
    assert report.common_prefix_count == 1
    assert report.first_divergence_index == 1
    assert report.left_event is not None and report.right_event is not None
    assert report.left_event.data_hash != report.right_event.data_hash
    assert report.edits[0].operation == "replace"


def test_run_diff_ignores_protocol_scoped_ids(tmp_path) -> None:
    store = SQLiteEventStore(tmp_path / "aura.db")
    for run_id, request_id in (("left", "call-1"), ("right", "call-99")):
        store.append_event(
            AgentEvent(
                run_id=run_id,
                kind=EventKind.TOOL_CALL_COMPLETED,
                tool_name="search",
                data={"result": {"count": 3}, "mcp.request_id": request_id},
            )
        )

    report = compare_runs(store, "left", "right")

    assert report.identical is True
    assert report.first_divergence_index is None


def test_manifest_diff_reports_added_removed_and_changed(tmp_path) -> None:
    store = SQLiteEventStore(tmp_path / "aura.db")
    store.append_manifest(
        ToolManifestSnapshot.create(
            run_id="left",
            request_id="1",
            tools=[
                {"name": "search", "description": "v1"},
                {"name": "legacy", "description": "old"},
            ],
        )
    )
    store.append_manifest(
        ToolManifestSnapshot.create(
            run_id="right",
            request_id="1",
            tools=[
                {"name": "search", "description": "v2"},
                {"name": "create", "description": "new"},
            ],
        )
    )

    report = compare_manifests(store, "left", "right")

    assert report.added == ["create"]
    assert report.removed == ["legacy"]
    assert report.changed == ["search"]
    assert report.has_drift is True
