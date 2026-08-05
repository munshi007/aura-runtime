import json
from pathlib import Path

from typer.testing import CliRunner

from aura_runtime.cli import app
from aura_runtime.trace_integrity import analyze_otlp_trace_integrity

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
ROOT = "00f067aa0ba902b7"
CHILD = "b7ad6b7169203331"


def payload(spans: list[dict[str, object]]) -> dict[str, object]:
    return {"resourceSpans": [{"scopeSpans": [{"spans": spans}]}]}


def span(
    span_id: str,
    *,
    parent_id: str | None = None,
    start: int = 10,
    end: int = 20,
) -> dict[str, object]:
    value: dict[str, object] = {
        "traceId": TRACE,
        "spanId": span_id,
        "startTimeUnixNano": str(start),
        "endTimeUnixNano": str(end),
    }
    if parent_id is not None:
        value["parentSpanId"] = parent_id
    return value


def test_closed_single_root_trace_is_verification_ready() -> None:
    report = analyze_otlp_trace_integrity(
        payload([span(ROOT, start=0, end=30), span(CHILD, parent_id=ROOT)])
    )

    assert report.verdict == "pass"
    assert report.verification_ready is True
    assert report.trace_count == 1
    assert report.span_count == 2
    assert report.issues == []


def test_missing_parent_makes_evidence_inconclusive() -> None:
    report = analyze_otlp_trace_integrity(payload([span(CHILD, parent_id=ROOT)]))

    assert report.verdict == "inconclusive"
    assert report.verification_ready is False
    assert {issue.code for issue in report.issues} == {"missing_parent", "root_count"}


def test_invalid_identity_duplicate_self_parent_and_duration_fail() -> None:
    bad = span("0000000000000000", parent_id="0000000000000000", start=20, end=10)
    report = analyze_otlp_trace_integrity(payload([bad, bad]))

    assert report.verdict == "fail"
    codes = {issue.code for issue in report.issues}
    assert {
        "invalid_span_id",
        "duplicate_span_id",
        "self_parent",
        "negative_duration",
        "root_count",
    } <= codes


def test_child_outside_parent_interval_is_inconclusive() -> None:
    report = analyze_otlp_trace_integrity(
        payload([span(ROOT, start=10, end=20), span(CHILD, parent_id=ROOT, start=5, end=25)])
    )

    assert report.verdict == "inconclusive"
    assert report.issues[0].code == "outside_parent_interval"


def test_empty_batch_is_inconclusive() -> None:
    report = analyze_otlp_trace_integrity({})

    assert report.verdict == "inconclusive"
    assert report.span_count == 0
    assert report.verification_ready is False


def test_cli_emits_report_and_nonzero_exit_for_unready_batch(tmp_path: Path) -> None:
    source = tmp_path / "trace.json"
    source.write_text(json.dumps(payload([span(CHILD, parent_id=ROOT)])), encoding="utf-8")

    result = CliRunner().invoke(app, ["check-otlp", str(source)])

    assert result.exit_code == 2
    assert '"verdict": "inconclusive"' in result.stdout
    assert '"missing_parent"' in result.stdout
