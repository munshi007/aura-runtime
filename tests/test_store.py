from aura_runtime.models import AgentEvent, EventKind, Finding, Severity
from aura_runtime.store import SQLiteEventStore


def test_store_round_trip(tmp_path) -> None:
    store = SQLiteEventStore(tmp_path / "aura.db")
    event = AgentEvent(run_id="run-1", kind=EventKind.RUN_STARTED, sequence=0)
    finding = Finding(
        run_id="run-1",
        policy_id="policy-1",
        severity=Severity.HIGH,
        message="violation",
        event_id=event.event_id,
        evidence_event_ids=[event.event_id],
        engine="test",
    )

    store.append_event(event)
    store.append_finding(finding)

    assert store.events("run-1") == [event]
    assert store.findings("run-1") == [finding]
    assert list(store.run_ids()) == ["run-1"]
