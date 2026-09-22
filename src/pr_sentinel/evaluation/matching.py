"""Deciding whether a produced finding is the finding a label describes.

The interesting choice is what to do with a finding that matches nothing. It is
not automatically wrong — a model can spot a real defect nobody labelled — but
treating every unlabelled finding as correct makes precision meaningless. So they
are counted separately and precision is reported twice, strictly and leniently,
with the truth somewhere between.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.models import Finding
from .fixtures import SEVERITY_ORDER, Label

# Models cite a line a little off more often than they invent a location, and the
# grounding filter has already snapped anything wilder than this.
LINE_TOLERANCE = 3


@dataclass
class Match:
    finding: Finding
    label: Label | None
    kind: str  # "hit" | "false_positive" | "unlabelled"
    agent_correct: bool = False
    category_correct: bool = False
    severity_sufficient: bool = False


def _locates(finding: Finding, label: Label) -> bool:
    if finding.file_path != label.file_path:
        return False
    if finding.line_start <= label.line <= finding.line_end:
        return True
    return min(abs(finding.line_start - label.line), abs(finding.line_end - label.line)) <= LINE_TOLERANCE


def classify(findings: list[Finding], expected: list[Label], forbidden: list[Label]) -> list[Match]:
    matches: list[Match] = []
    for finding in findings:
        label = next((x for x in expected if _locates(finding, x)), None)
        if label is not None:
            matches.append(
                Match(
                    finding=finding,
                    label=label,
                    kind="hit",
                    agent_correct=label.agent is None or str(finding.agent) == label.agent,
                    category_correct=label.category is None or str(finding.category) == label.category,
                    severity_sufficient=SEVERITY_ORDER.index(str(finding.severity))
                    >= label.min_severity_rank,
                )
            )
            continue

        trap = next((x for x in forbidden if _locates(finding, x)), None)
        if trap is not None:
            matches.append(Match(finding=finding, label=trap, kind="false_positive"))
            continue

        matches.append(Match(finding=finding, label=None, kind="unlabelled"))
    return matches


def missed(expected: list[Label], matches: list[Match]) -> list[Label]:
    found = {id(m.label) for m in matches if m.kind == "hit"}
    return [label for label in expected if id(label) not in found]
