from aura_runtime.alphabet import EventAlphabet
from aura_runtime.policy import AuraSpec
from aura_runtime.verifier import OnlineLTLfMonitor


def policy(
    propositions: dict[str, dict[str, object]],
    formula: str,
    *,
    controls: dict[str, str] | None = None,
):
    return AuraSpec.model_validate(
        {
            "version": "0.1",
            "ltlf_policies": [
                {
                    "id": "alphabet",
                    "description": "Event alphabet test",
                    "formula": formula,
                    "propositions": propositions,
                    "proposition_control": controls or {},
                }
            ],
        }
    ).ltlf_policies[0]


def test_different_event_kinds_cannot_share_one_valuation() -> None:
    item = policy(
        {
            "start": {"event": "run.started"},
            "done": {"event": "run.completed"},
        },
        "F start & F done",
    )
    alphabet = EventAlphabet(item)

    assert alphabet.rejection_reason(frozenset({"start", "done"})) == (
        "conflicting_event_kinds"
    )
    report = alphabet.report()
    assert report.total_valuation_count == 4
    assert report.feasible_valuation_count == 3
    assert report.rejection_reasons == {"conflicting_event_kinds": 1}


def test_identical_selectors_cannot_disagree() -> None:
    item = policy(
        {
            "first": {"event": "human.approval"},
            "second": {"event": "human.approval"},
        },
        "first | second",
    )
    alphabet = EventAlphabet(item)

    assert alphabet.rejection_reason(frozenset({"first"})) == (
        "identical_selectors_disagree"
    )
    assert alphabet.report().feasible_valuation_count == 2


def test_conflicting_payload_and_exact_tool_constraints_are_rejected() -> None:
    payload = policy(
        {
            "approved": {
                "event": "state.changed",
                "where": {"data.status": "approved"},
            },
            "rejected": {
                "event": "state.changed",
                "where": {"data.status": "rejected"},
            },
        },
        "approved | rejected",
    )
    tools = policy(
        {
            "delete": {
                "event": "tool.call.requested",
                "tool_matches": ["delete_customer"],
            },
            "send": {
                "event": "tool.call.requested",
                "tool_matches": ["send_email"],
            },
        },
        "delete | send",
    )

    assert EventAlphabet(payload).rejection_reason(frozenset({"approved", "rejected"})) == (
        "conflicting_where_values"
    )
    assert EventAlphabet(tools).rejection_reason(frozenset({"delete", "send"})) == (
        "conflicting_exact_tools"
    )


def test_wildcard_tool_overlap_remains_feasible() -> None:
    item = policy(
        {
            "delete": {
                "event": "tool.call.requested",
                "tool_matches": ["delete_customer", "*"],
            },
            "send": {
                "event": "tool.call.requested",
                "tool_matches": ["send_email", "*"],
            },
        },
        "F (delete & send)",
    )

    assert EventAlphabet(item).is_feasible(frozenset({"delete", "send"}))


def test_strategy_game_removes_impossible_joint_events() -> None:
    item = policy(
        {
            "start": {"event": "run.started"},
            "done": {"event": "run.completed"},
        },
        "F (start & done)",
        controls={"start": "agent", "done": "agent"},
    )
    spec = AuraSpec(version="0.1", ltlf_policies=[item])

    report = OnlineLTLfMonitor(spec).strategy_report().policies[0]

    assert report.strategy.status == "unachievable"
    assert report.strategy.total_valuation_count == 4
    assert report.strategy.feasible_valuation_count == 3
    assert report.alphabet.rejected_valuation_count == 1
