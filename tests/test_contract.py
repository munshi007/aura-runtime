import json
from pathlib import Path

from aura_runtime.contract import TraceContract, check_contract
from aura_runtime.models import AgentEvent, EventKind, ToolManifestSnapshot
from aura_runtime.store import SQLiteEventStore

ROOT = Path(__file__).parents[1]
REFERENCE = ROOT / "examples" / "reference_agent"


def baseline_events() -> list[AgentEvent]:
    return [
        AgentEvent.model_validate_json(line)
        for line in (REFERENCE / "baseline" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]


def baseline_tools() -> list[dict]:
    return json.loads((REFERENCE / "baseline" / "tools.json").read_text())["tools"]


def candidate_store(tmp_path: Path) -> SQLiteEventStore:
    store = SQLiteEventStore(tmp_path / "aura.db")
    for event in baseline_events():
        store.append_event(event.model_copy(update={"run_id": "candidate"}))
    store.append_manifest(
        ToolManifestSnapshot.create(
            run_id="candidate",
            request_id="1",
            tools=baseline_tools(),
        )
    )
    return store


def test_matching_candidate_passes_contract(tmp_path) -> None:
    store = candidate_store(tmp_path)
    contract = TraceContract.from_yaml(REFERENCE / "aura-contract.yaml")

    report = check_contract(contract, store, "candidate")

    assert report.verdict == "pass"
    assert report.reasons == []
    assert report.run_diff.identical is True
    assert report.manifest_diff.has_drift is False
    assert "Verdict: PASS" in report.to_markdown()


def test_dangerous_candidate_fails_with_first_divergence(tmp_path) -> None:
    store = candidate_store(tmp_path)
    store.append_event(
        AgentEvent(
            run_id="candidate",
            kind=EventKind.TOOL_CALL_REQUESTED,
            tool_name="delete_customer",
            sequence=4,
            data={"arguments": {"customer_id": "cus_123"}},
        )
    )

    report = check_contract(
        TraceContract.from_yaml(REFERENCE / "aura-contract.yaml"),
        store,
        "candidate",
    )

    assert report.verdict == "fail"
    assert len(report.introduced_findings) == 1
    assert report.introduced_findings[0].policy_id == "destructive-tools-require-approval"
    assert report.run_diff.first_divergence_index == 4
    assert "new policy finding" in report.reasons[0]
    assert "Verdict: FAIL" in report.to_markdown()
