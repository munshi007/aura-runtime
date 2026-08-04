import pytest

from aura_runtime.ltlf import (
    LTLfComplexityError,
    LTLfMonitor,
    LTLfSyntaxError,
    PrefixVerdict,
    accepts_empty,
    atoms,
    parse_ltlf,
)
from aura_runtime.strategy_backend import ExplicitProgressionBackend


def test_parser_supports_boolean_and_temporal_surface() -> None:
    formula = parse_ltlf("G(request -> F (approved & Xw completed))")

    assert atoms(formula) == {"request", "approved", "completed"}
    rendered = str(formula)
    assert "G(" in rendered
    assert "F(" in rendered
    assert "Xw(" in rendered
    assert "!request" in rendered


def test_negation_uses_finite_trace_next_duality() -> None:
    strong_negation = parse_ltlf("!(X approved)")
    weak_negation = parse_ltlf("!(Xw approved)")

    assert str(strong_negation) == "Xw(!approved)"
    assert str(weak_negation) == "X(!approved)"


def test_strong_and_weak_next_differ_at_end_of_trace() -> None:
    strong = LTLfMonitor("X approved")
    weak = LTLfMonitor("Xw approved")
    strong.observe(set())
    weak.observe(set())

    assert strong.finalize().finite_trace_verdict == "fail"
    assert weak.finalize().finite_trace_verdict == "pass"

    two_step_strong = LTLfMonitor("X approved")
    two_step_weak = LTLfMonitor("Xw approved")
    for monitor in (two_step_strong, two_step_weak):
        monitor.observe(set())
        monitor.observe({"approved"})
        assert monitor.finalize().finite_trace_verdict == "pass"


def test_eventually_and_always_have_exact_prefix_verdicts() -> None:
    eventually = LTLfMonitor("F approved")
    eventually.observe(set())
    assert eventually.report().prefix_verdict == PrefixVerdict.CURRENTLY_VIOLATED
    eventually.observe({"approved"})
    assert (
        eventually.report().prefix_verdict
        == PrefixVerdict.PERMANENTLY_SATISFIED
    )

    always = LTLfMonitor("G safe")
    always.observe({"safe"})
    assert always.report().prefix_verdict == PrefixVerdict.CURRENTLY_SATISFIED
    always.observe(set())
    assert always.report().prefix_verdict == PrefixVerdict.PERMANENTLY_VIOLATED


def test_until_and_release_follow_finite_trace_semantics() -> None:
    until = LTLfMonitor("working U completed")
    until.observe({"working"})
    assert until.report().finite_trace_verdict == "undetermined"
    until.observe({"completed"})
    assert until.finalize().finite_trace_verdict == "pass"

    release = parse_ltlf("! (working U completed)")
    assert " R " in str(release)
    assert accepts_empty(release) is True


def test_tautology_and_contradiction_are_conclusive() -> None:
    tautology = LTLfMonitor("approved | !approved")
    contradiction = LTLfMonitor("approved & !approved")
    tautology.observe(set())
    contradiction.observe(set())

    assert (
        tautology.report().prefix_verdict
        == PrefixVerdict.PERMANENTLY_SATISFIED
    )
    assert (
        contradiction.report().prefix_verdict
        == PrefixVerdict.PERMANENTLY_VIOLATED
    )


@pytest.mark.parametrize(
    "formula",
    ["", "G(", "approved ??? completed", "approved &", ") approved"],
)
def test_parser_rejects_invalid_formulas(formula: str) -> None:
    with pytest.raises(LTLfSyntaxError):
        parse_ltlf(formula)


def test_monitor_rejects_unknown_or_excessive_propositions() -> None:
    with pytest.raises(LTLfComplexityError, match="limit is 2"):
        LTLfMonitor("a & b & c", max_atoms=2)

    monitor = LTLfMonitor("approved")
    with pytest.raises(ValueError, match="unknown propositions"):
        monitor.observe({"secret"})


def test_monitor_cannot_finalize_an_empty_trace_or_resume_after_final() -> None:
    monitor = LTLfMonitor("approved")
    with pytest.raises(ValueError, match="empty"):
        monitor.finalize()
    monitor.observe({"approved"})
    monitor.finalize()
    with pytest.raises(ValueError, match="finalized"):
        monitor.observe({"approved"})


def test_preview_is_non_mutating_and_finds_nearest_safe_valuations() -> None:
    monitor = LTLfMonitor("G !delete")

    report = monitor.preview({"delete"})

    assert report.classification == "permanently_violating"
    assert report.resulting_verdict == "permanently_violated"
    assert report.alternatives[0].true_propositions == []
    assert report.alternatives[0].changed_propositions == ["delete"]
    assert report.alternatives[0].distance == 1
    assert monitor.observed_event_count == 0
    assert str(monitor.residual) == "G(!delete)"


def test_safe_preview_needs_no_repair() -> None:
    report = LTLfMonitor("F approved").preview(set())

    assert report.classification == "safe"
    assert report.resulting_verdict == "currently_violated"
    assert report.alternatives == []


def test_preview_changes_only_controllable_propositions() -> None:
    monitor = LTLfMonitor("(!delete) U approval")

    report = monitor.preview(
        {"delete"},
        controllable_propositions={"delete"},
        environment_propositions={"approval"},
    )

    assert report.classification == "permanently_violating"
    assert report.alternatives[0].changed_propositions == ["delete"]
    assert report.environment_requirements == [["approval"]]
    assert all(
        "approval" not in alternative.changed_propositions
        for alternative in report.alternatives
    )


def test_uncontrollable_violation_is_reported_as_unenforceable() -> None:
    report = LTLfMonitor("G safe").preview(
        set(), environment_propositions={"safe"}, controllable_propositions=set()
    )

    assert report.classification == "permanently_violating"
    assert report.enforceable is False
    assert report.alternatives == []


def test_strategy_synthesis_finds_a_winning_controller() -> None:
    report = LTLfMonitor("F done").synthesize_strategy({"done"})

    assert report.status == "realizable"
    assert report.strategy_backend == "explicit_progression"
    assert report.valuation_backend == "explicit"
    assert report.turn_semantics == "agent_then_environment"
    assert report.termination_control == "agent"
    assert report.winning_state_count == report.reachable_state_count
    initial = next(move for move in report.strategy if move.residual == "F(done)")
    assert initial.true_agent_propositions == ["done"]
    assert initial.rank == 1
    assert report.counterstrategy == []


def test_strategy_backend_is_replaceable_without_changing_the_report() -> None:
    class NamedBackend(ExplicitProgressionBackend):
        name = "test_progression"

    report = LTLfMonitor("F done").synthesize_strategy(
        {"done"}, backend=NamedBackend()
    )

    assert report.status == "realizable"
    assert report.strategy_backend == "test_progression"


def test_environment_goal_has_only_a_cooperative_strategy() -> None:
    report = LTLfMonitor("F approved").synthesize_strategy(set())

    assert report.status == "cooperative_only"
    assert report.winning_state_count < report.cooperative_state_count
    initial = next(item for item in report.counterstrategy if item.residual == "F(approved)")
    assert initial.responses[0].true_environment_propositions == []
    assert report.cooperative_strategy[0].rank == 1


def test_impossible_formula_is_unachievable() -> None:
    report = LTLfMonitor("false").synthesize_strategy(set())

    assert report.status == "unachievable"
    assert report.cooperative_state_count == 0
    assert report.cooperative_strategy == []


def test_strategy_synthesis_respects_state_bound() -> None:
    with pytest.raises(LTLfComplexityError, match="strategy game states"):
        LTLfMonitor("F (a & X b)").synthesize_strategy({"a", "b"}, max_states=1)
