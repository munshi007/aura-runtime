"""Content-free structural integrity checks for OTLP trace evidence."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

_TRACE_ID = re.compile(r"^[0-9a-f]{32}$")
_SPAN_ID = re.compile(r"^[0-9a-f]{16}$")


class TraceIssueSeverity(StrEnum):
    WARNING = "warning"
    ERROR = "error"


class TraceIntegrityIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    severity: TraceIssueSeverity
    message: str
    trace_id: str | None = None
    span_ids: list[str] = Field(default_factory=list)


class TraceIntegrityReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    verdict: Literal["pass", "inconclusive", "fail"]
    verification_ready: bool
    trace_count: int
    span_count: int
    issues: list[TraceIntegrityIssue]


def _spans(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        span
        for resource in payload.get("resourceSpans", [])
        for scope in resource.get("scopeSpans", [])
        for span in scope.get("spans", [])
        if isinstance(span, dict)
    ]


def _issue(
    code: str,
    severity: TraceIssueSeverity,
    message: str,
    *,
    trace_id: str | None = None,
    span_ids: list[str] | None = None,
) -> TraceIntegrityIssue:
    return TraceIntegrityIssue(
        code=code,
        severity=severity,
        message=message,
        trace_id=trace_id,
        span_ids=span_ids or [],
    )


def analyze_otlp_trace_integrity(payload: dict[str, Any]) -> TraceIntegrityReport:
    """Check whether an OTLP batch is structurally sufficient for strong verification."""

    spans = _spans(payload)
    issues: list[TraceIntegrityIssue] = []
    by_trace: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)

    if not spans:
        issues.append(
            _issue(
                "empty_batch",
                TraceIssueSeverity.WARNING,
                "the export request contains no spans to verify",
            )
        )

    for span in spans:
        trace_id = str(span.get("traceId") or "")
        span_id = str(span.get("spanId") or "")
        if not _TRACE_ID.fullmatch(trace_id) or set(trace_id) == {"0"}:
            issues.append(
                _issue(
                    "invalid_trace_id",
                    TraceIssueSeverity.ERROR,
                    "trace ID must be 32 lowercase hexadecimal characters and non-zero",
                    trace_id=trace_id or None,
                    span_ids=[span_id] if span_id else [],
                )
            )
        if not _SPAN_ID.fullmatch(span_id) or set(span_id) == {"0"}:
            issues.append(
                _issue(
                    "invalid_span_id",
                    TraceIssueSeverity.ERROR,
                    "span ID must be 16 lowercase hexadecimal characters and non-zero",
                    trace_id=trace_id or None,
                    span_ids=[span_id] if span_id else [],
                )
            )
        by_trace[trace_id].append(span)

    for trace_id, trace_spans in sorted(by_trace.items()):
        span_ids = [str(span.get("spanId") or "") for span in trace_spans]
        counts = Counter(span_ids)
        duplicates = sorted(span_id for span_id, count in counts.items() if count > 1)
        if duplicates:
            issues.append(
                _issue(
                    "duplicate_span_id",
                    TraceIssueSeverity.ERROR,
                    "a span identity appears more than once in the trace batch",
                    trace_id=trace_id,
                    span_ids=duplicates,
                )
            )

        known = set(span_ids)
        by_span_id = {str(span.get("spanId") or ""): span for span in trace_spans}
        roots = []
        for span in trace_spans:
            span_id = str(span.get("spanId") or "")
            parent_id = str(span.get("parentSpanId") or "")
            if not parent_id:
                roots.append(span_id)
            elif parent_id == span_id:
                issues.append(
                    _issue(
                        "self_parent",
                        TraceIssueSeverity.ERROR,
                        "a span cannot be its own parent",
                        trace_id=trace_id,
                        span_ids=[span_id],
                    )
                )
            elif parent_id not in known:
                issues.append(
                    _issue(
                        "missing_parent",
                        TraceIssueSeverity.WARNING,
                        "parent span is absent; the exported batch is not causally closed",
                        trace_id=trace_id,
                        span_ids=[span_id, parent_id],
                    )
                )
            else:
                parent = by_span_id[parent_id]
                try:
                    parent_start = int(parent.get("startTimeUnixNano", 0))
                    parent_end = int(parent.get("endTimeUnixNano", 0))
                    child_start = int(span.get("startTimeUnixNano", 0))
                    child_end = int(span.get("endTimeUnixNano", 0))
                except (TypeError, ValueError):
                    parent_start = parent_end = child_start = child_end = 0
                if child_start < parent_start or child_end > parent_end:
                    issues.append(
                        _issue(
                            "outside_parent_interval",
                            TraceIssueSeverity.WARNING,
                            "child timing falls outside its parent interval; clock skew or "
                            "bad instrumentation may exist",
                            trace_id=trace_id,
                            span_ids=[span_id, parent_id],
                        )
                    )

            try:
                start = int(span.get("startTimeUnixNano", 0))
                end = int(span.get("endTimeUnixNano", 0))
            except (TypeError, ValueError):
                start, end = 0, -1
            if end < start:
                issues.append(
                    _issue(
                        "negative_duration",
                        TraceIssueSeverity.ERROR,
                        "span end time precedes its start time",
                        trace_id=trace_id,
                        span_ids=[span_id],
                    )
                )

        if len(roots) != 1:
            issues.append(
                _issue(
                    "root_count",
                    TraceIssueSeverity.WARNING,
                    "a causally closed trace batch should contain exactly one root span",
                    trace_id=trace_id,
                    span_ids=sorted(roots),
                )
            )

    has_error = any(issue.severity == TraceIssueSeverity.ERROR for issue in issues)
    verdict: Literal["pass", "inconclusive", "fail"]
    if has_error:
        verdict = "fail"
    elif issues:
        verdict = "inconclusive"
    else:
        verdict = "pass"
    return TraceIntegrityReport(
        verdict=verdict,
        verification_ready=not issues and bool(spans),
        trace_count=len(by_trace),
        span_count=len(spans),
        issues=issues,
    )
