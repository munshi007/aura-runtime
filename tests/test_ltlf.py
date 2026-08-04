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
