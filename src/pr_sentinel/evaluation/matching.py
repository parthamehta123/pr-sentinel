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

**A hit needs the right concern, not just the right line.** Matching on location
alone credited any finding that landed near a labelled line, whatever it said —
the tests agent noting "no test covers this" on a line labelled for SQL injection
scored as a hit on the injection. Measured over three runs, 56.6% of hits were a
different agent describing a different concern. That inflated precision from a
true 0.40 to a reported 0.98 and made every per-agent number meaningless. A
finding must now agree with the label's *family* of concern; landing nearby while
talking about something else makes it unlabelled, which is what it is.

**Attribution asks who contributed, not who won.** The aggregator merges findings
from several agents and keeps the highest-severity one as primary, so a merged
finding carries that agent's name and category. Comparing a label against those
alone scored the security agent at 0.000 on six advisory cases it had found every
one of — it had simply been merged under correctness each time.

**A trap is not reached by a finding that touches a real defect.** Traps sit
beside the defects they contrast with, often three lines away, and a finding whose
span covers both is aimed at the bug. Counting it as a false positive punishes
describing the real problem.

**Three outcomes, not two.** Most of what a competent reviewer could say about a
diff is neither a required finding nor a mistake. An `ALLOW` label marks an
observation that is legitimate but optional — it is not needed for recall and does
not count against precision. Without it the set had to either demand every true
observation, penalising restraint, or treat it as noise, which is a lie about
true code.

**A finding may satisfy several labels.** The aggregator folds a repeated
recommendation into one comment carrying every location as evidence. That comment
does cover all of those defects, and scoring it as covering only the first would
penalise the system for the exact consolidation it is supposed to perform.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.enums import Category, family_of
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


def _covers(finding: Finding, label: Label) -> bool:
    """Strictly: does this finding actually talk about that line?

    No snapping tolerance. The tolerance exists because models miscount their own
    line inside a hunk, and it is the right call when deciding whether a real
    defect was found. Applied to a trap it manufactures false positives: measured
    over the recorded runs, twelve of thirteen "false positives" were findings
    three to five lines away, aimed squarely at the defective function below the
    clean one and dragged onto it by the tolerance.
    """
    if finding.file_path == label.file_path and (finding.line_start <= label.line <= finding.line_end):
        return True
    return any(
        e.file_path == label.file_path
        and e.line_start is not None
        and e.line_start <= label.line <= (e.line_end or e.line_start)
        for e in finding.evidence
    )


def _traps(finding: Finding, trap: Label) -> bool:
    """A trap fires only on the line it declares clean, and only within its scope."""
    if not _covers(finding, trap):
        return False
    if trap.agent is not None and str(finding.agent) != trap.agent:
        return False
    return not (trap.category is not None and str(finding.category) != trap.category)


def _same_concern(finding: Finding, label: Label) -> bool:
    """Is the finding about the kind of thing the label describes?

    A label with no category makes no claim, so location is enough. Otherwise the
    families must agree — `injection` and `authz` are both security and count;
    `test_coverage` on the same line does not.

    A label may name alternatives as `a|b`, because one defect genuinely has more
    than one fair reading. A traceback returned to a client is information
    disclosure and an error-handling mistake; rejecting the second framing cost a
    correct finding at confidence 0.99.
    """
    if label.category is None:
        return True
    families = set()
    for name in label.category.split("|"):
        try:
            families.add(family_of(Category(name)))
        except ValueError:
            return True
    # A merged finding is about every concern its contributors named, not only
    # the one belonging to whichever of them happened to win the merge.
    found = finding.categories or [str(finding.category)]
    return any(family_of(Category(c)) in families for c in found)


def classify(
    findings: list[Finding],
    expected: list[Label],
    forbidden: list[Label],
    allowed: list[Label] | None = None,
    permitted_concerns: list[tuple[str, str]] | None = None,
) -> list[Match]:
    matches: list[Match] = []
    for finding in findings:
        covered = [x for x in expected if _locates(finding, x) and _same_concern(finding, x)]
        if covered:
            primary = covered[0]
            matches.append(
                Match(
                    finding=finding,
                    label=primary,
                    kind="hit",
                    labels=covered,
                    agent_correct=primary.agent is None
                    or primary.agent in (finding.agreeing or [str(finding.agent)]),
                    category_correct=primary.category is None
                    or bool(
                        set(finding.categories or [str(finding.category)]) & set(primary.category.split("|"))
                    ),
                    severity_sufficient=SEVERITY_ORDER.index(str(finding.severity))
                    >= primary.min_severity_rank,
                )
            )
            continue

        # A finding that reaches a labelled defect is aimed at it, whatever
        # framing it chose. Counting it as a false positive because its span also
        # covers the clean sibling three lines above punishes describing the real
        # bug. Measured: all three "false positives" in the first 47-case run were
        # exactly this — correct findings spanning both.
        aimed_at_a_defect = any(_locates(finding, x) for x in expected)
        trap = None if aimed_at_a_defect else next((x for x in forbidden if _traps(finding, x)), None)
        if trap is not None:
            matches.append(Match(finding=finding, label=trap, kind="false_positive", labels=[trap]))
            continue

        permitted = next(
            (x for x in (allowed or []) if _locates(finding, x) and _same_concern(finding, x)),
            None,
        )
        if permitted is not None:
            matches.append(Match(finding=finding, label=permitted, kind="allowed", labels=[permitted]))
            continue

        if (str(finding.agent), str(finding.category)) in set(permitted_concerns or []):
            matches.append(Match(finding=finding, label=None, kind="allowed"))
            continue

        matches.append(Match(finding=finding, label=None, kind="unlabelled"))
    return matches


def missed(expected: list[Label], matches: list[Match]) -> list[Label]:
    found = {id(lab) for m in matches if m.kind == "hit" for lab in m.labels}
    return [label for label in expected if id(label) not in found]
