"""Deciding whether a produced finding is the finding a label describes.

Two choices worth stating.

**Unlabelled findings.** A finding matching no label is not automatically wrong —
a model can spot a real defect nobody labelled — but treating all of them as
correct makes precision meaningless. They are counted separately and precision is
reported twice, strictly and leniently, with the truth somewhere between.

**A trap can be scoped.** An unscoped `CLEAN` label claims that nothing at that
line is a finding, which is a very strong claim and one that is genuinely hard to
earn — two rounds of correcting the "clean" cases in this set still left real
defects in them, which the models duly found. A scoped trap claims only that a
*particular* reading is wrong: flagging this line as an injection is a false
positive, while observing that it lacks a test is not. Traps in this set are
scoped where the case is testing one specific reflex.

**A finding may satisfy several labels.** The aggregator folds a repeated
recommendation into one comment carrying every location as evidence. That comment
does cover all of those defects, and scoring it as covering only the first would
penalise the system for the exact consolidation it is supposed to perform.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.models import Finding
from .fixtures import SEVERITY_ORDER, Label

# Models cite a line a little off more often than they invent a location, and the
# grounding filter has already snapped anything wilder than this.
LINE_TOLERANCE = 3


@dataclass
class Match:
    finding: Finding
    label: Label | None  # the primary label, for reporting
    kind: str  # "hit" | "false_positive" | "unlabelled"
    labels: list[Label] = field(default_factory=list)  # every label this covers
    agent_correct: bool = False
    category_correct: bool = False
    severity_sufficient: bool = False


def _near(path: str, lo: int, hi: int, label: Label) -> bool:
    if path != label.file_path:
        return False
    if lo <= label.line <= hi:
        return True
    return min(abs(lo - label.line), abs(hi - label.line)) <= LINE_TOLERANCE


def _locates(finding: Finding, label: Label) -> bool:
    """True if the finding's own range, or any location it cites, covers the label."""
    if _near(finding.file_path, finding.line_start, finding.line_end, label):
        return True
    return any(
        _near(e.file_path, e.line_start, e.line_end or e.line_start, label)
        for e in finding.evidence
        if e.line_start is not None
    )


def _traps(finding: Finding, trap: Label) -> bool:
    """A trap fires only within its scope, if it declares one."""
    if not _locates(finding, trap):
        return False
    if trap.agent is not None and str(finding.agent) != trap.agent:
        return False
    return not (trap.category is not None and str(finding.category) != trap.category)


def classify(findings: list[Finding], expected: list[Label], forbidden: list[Label]) -> list[Match]:
    matches: list[Match] = []
    for finding in findings:
        covered = [x for x in expected if _locates(finding, x)]
        if covered:
            primary = covered[0]
            matches.append(
                Match(
                    finding=finding,
                    label=primary,
                    kind="hit",
                    labels=covered,
                    agent_correct=primary.agent is None or str(finding.agent) == primary.agent,
                    category_correct=primary.category is None or str(finding.category) == primary.category,
                    severity_sufficient=SEVERITY_ORDER.index(str(finding.severity))
                    >= primary.min_severity_rank,
                )
            )
            continue

        trap = next((x for x in forbidden if _traps(finding, x)), None)
        if trap is not None:
            matches.append(Match(finding=finding, label=trap, kind="false_positive", labels=[trap]))
            continue

        matches.append(Match(finding=finding, label=None, kind="unlabelled"))
    return matches


def missed(expected: list[Label], matches: list[Match]) -> list[Label]:
    found = {id(lab) for m in matches if m.kind == "hit" for lab in m.labels}
    return [label for label in expected if id(label) not in found]
