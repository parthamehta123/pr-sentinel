"""The grounding filter — the thing that stops a hallucination reaching a human."""

from __future__ import annotations

from pr_sentinel.agents.specialists import SecurityAgent
from pr_sentinel.retrieval.context import ReviewContext


def ctx_for(pr):
    return ReviewContext(pr=pr, diff_text="")


def raw(**over):
    base = {
        "file_path": "billing/handler.py",
        "line_start": 14,
        "line_end": 14,
        "category": "injection",
        "severity": "critical",
        "confidence": 0.9,
        "title": "SQL injection",
        "body": "Use a parameterised query.",
        "rationale": "customer_id is interpolated into the SQL on this line.",
        "evidence": [],
    }
    base.update(over)
    return base


def test_a_finding_inside_the_diff_survives(pr_context):
    result = SecurityAgent()._ground(raw(), ctx_for(pr_context))
    assert result is not None
    finding, snapped = result
    assert finding.line_start == 14 and not snapped


def test_a_finding_far_outside_the_diff_is_dropped(pr_context):
    assert SecurityAgent()._ground(raw(line_start=9999, line_end=9999), ctx_for(pr_context)) is None


def test_a_near_miss_is_snapped_rather_than_discarded(pr_context):
    """Models miscount inside a hunk far more often than they invent a location."""
    addressable = sorted(pr_context.files[0].addressable_lines())
    off_by_two = addressable[0] - 2
    result = SecurityAgent()._ground(raw(line_start=off_by_two, line_end=off_by_two), ctx_for(pr_context))
    assert result is not None
    finding, snapped = result
    assert snapped and finding.line_start == addressable[0]


def test_a_file_not_in_the_diff_is_dropped(pr_context):
    assert SecurityAgent()._ground(raw(file_path="other/module.py"), ctx_for(pr_context)) is None


def test_a_shortened_path_still_matches_exactly_one_file(pr_context):
    result = SecurityAgent()._ground(raw(file_path="handler.py"), ctx_for(pr_context))
    assert result is not None and result[0].file_path == "billing/handler.py"


def test_an_ambiguous_suffix_is_dropped_rather_than_guessed(pr_context):
    from pr_sentinel.forge.diff import build_diff_file

    pr_context.files.append(
        build_diff_file(
            {
                "filename": "shipping/handler.py",
                "status": "modified",
                "additions": 1,
                "deletions": 0,
                "patch": "@@ -1,1 +1,2 @@\n a\n+b\n",
            }
        )
    )
    assert SecurityAgent()._ground(raw(file_path="handler.py"), ctx_for(pr_context)) is None


def test_a_finding_without_a_rationale_is_dropped(pr_context):
    """INVARIANT-3, enforced at the boundary rather than trusted."""
    assert SecurityAgent()._ground(raw(rationale="   "), ctx_for(pr_context)) is None


def test_a_finding_without_a_title_is_dropped(pr_context):
    assert SecurityAgent()._ground(raw(title=""), ctx_for(pr_context)) is None


def test_confidence_is_clamped_into_range(pr_context):
    hot = SecurityAgent()._ground(raw(confidence=1.7), ctx_for(pr_context))
    cold = SecurityAgent()._ground(raw(confidence=-3), ctx_for(pr_context))
    assert hot[0].confidence == 1.0 and cold[0].confidence == 0.0


def test_unknown_category_and_severity_degrade_to_defaults(pr_context):
    finding, _ = SecurityAgent()._ground(raw(category="vibes", severity="apocalyptic"), ctx_for(pr_context))
    assert str(finding.category) == "other"
    assert str(finding.severity) == "minor"


def test_line_end_is_clamped_inside_the_diff(pr_context):
    finding, _ = SecurityAgent()._ground(raw(line_end=99999), ctx_for(pr_context))
    assert finding.line_end <= max(pr_context.files[0].addressable_lines())


def test_unparseable_model_output_yields_no_findings(pr_context):
    findings, _, _, summary = SecurityAgent()._parse("not json at all", ctx_for(pr_context))
    assert findings == [] and "unparseable" in summary


def test_json_wrapped_in_prose_is_salvaged(pr_context):
    text = 'Sure! Here you go:\n{"summary":"ok","findings":[]}\nHope that helps.'
    findings, _, _, summary = SecurityAgent()._parse(text, ctx_for(pr_context))
    assert findings == [] and summary == "ok"


def test_ungrounded_findings_are_counted_not_silently_lost(pr_context):
    import json

    payload = json.dumps({"summary": "s", "findings": [raw(), raw(line_start=8000, line_end=8000)]})
    findings, dropped, _, _ = SecurityAgent()._parse(payload, ctx_for(pr_context))
    assert len(findings) == 1 and dropped == 1
