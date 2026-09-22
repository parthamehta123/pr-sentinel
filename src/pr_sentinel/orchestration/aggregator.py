"""Fan-in: merge four independent opinions into one review.

Two agents noticing the same thing from different angles is the strongest signal
this system produces — much stronger than one agent being confident. So
agreement raises confidence (noisy-OR), while a duplicate from the *same* agent
raises nothing, because a model repeating itself is not evidence.

Everything here is deterministic. There is no LLM in the merge step: an
aggregator that reasons is an aggregator that can hallucinate a finding no
specialist made, and then nobody owns it.
"""

from __future__ import annotations

from ..config import get_settings
from ..domain.enums import (
    COLLAPSIBLE_CATEGORIES,
    SEVERITY_ORDER,
    AgentType,
    Category,
    Severity,
    family_of,
)
from ..domain.models import AgentVerdict, Evidence, Finding
from ..logging import get_logger

log = get_logger(__name__)

TITLE_SIMILARITY_THRESHOLD = 0.45


def aggregate(verdicts: list[AgentVerdict]) -> tuple[list[Finding], float]:
    """Return (merged findings, overall confidence)."""
    findings = [f for v in verdicts if v.ok for f in v.findings]
    merged = _collapse_repeated(_merge(findings))
    merged.sort(key=lambda f: (-SEVERITY_ORDER.index(f.severity), -f.confidence, f.file_path))
    return merged, overall_confidence(merged, verdicts)


def _merge(findings: list[Finding]) -> list[Finding]:
    clusters: list[list[Finding]] = []
    for finding in findings:
        for cluster in clusters:
            if any(_same_issue(finding, other) for other in cluster):
                cluster.append(finding)
                break
        else:
            clusters.append([finding])
    return [_collapse(c) for c in clusters]


def _same_issue(a: Finding, b: Finding) -> bool:
    """Two findings describing one defect.

    Matching on identical category was far too strict. Measured against real
    models it produced 3.5 findings per labelled defect: four specialists looking
    at one interpolated SQL string call it `injection`, `input_validation`,
    `logic` and `test_coverage`, and none of those strings equal each other. On
    the same lines, within the same family of concern, it is one comment.

    Across families it is not — a missing docstring and an injection on the same
    line are two different things, and merging them would bury one.
    """
    if not a.overlaps(b):
        return False
    if a.category == b.category:
        return True
    if a.category is not Category.OTHER and b.category is not Category.OTHER:
        if family_of(a.category) == family_of(b.category):
            return True
    return _title_similarity(a.title, b.title) >= TITLE_SIMILARITY_THRESHOLD


def _title_similarity(a: str, b: str) -> float:
    """Jaccard over content words. Crude, transparent, and good enough — the line
    overlap check has already done most of the work."""
    stop = {"the", "a", "an", "is", "in", "of", "to", "on", "and", "for", "this", "that", "with"}
    ta = {w for w in _words(a) if w not in stop}
    tb = {w for w in _words(b) if w not in stop}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _words(text: str) -> set[str]:
    return {w for w in "".join(c.lower() if c.isalnum() else " " for c in text).split() if len(w) > 2}


def _collapse(cluster: list[Finding]) -> Finding:
    if len(cluster) == 1:
        return cluster[0]

    # Keep the most severe, breaking ties on confidence — that finding's wording
    # is the one a human will read.
    primary = max(cluster, key=lambda f: (SEVERITY_ORDER.index(f.severity), f.confidence))
    agents = {f.agent for f in cluster}

    confidence = primary.confidence
    if len(agents) > 1:
        # Noisy-OR across distinct agents: two independent 0.7s become 0.91.
        product = 1.0
        for agent in agents:
            best = max(f.confidence for f in cluster if f.agent is agent)
            product *= 1.0 - best
        confidence = min(0.99, 1.0 - product)

    merged = primary.model_copy(
        update={
            "confidence": confidence,
            "agreeing": sorted(str(a) for a in agents),
            "categories": sorted({str(f.category) for f in cluster}),
            "line_start": min(f.line_start for f in cluster),
            "line_end": max(f.line_end for f in cluster),
            "evidence": _dedupe_evidence(cluster),
            "body": _merge_bodies(primary, cluster),
        }
    )
    for other in cluster:
        if other.id != primary.id:
            other.merged_into = primary.id
    return merged


def _merge_bodies(primary: Finding, cluster: list[Finding]) -> str:
    extra = [
        f"\n\n_Also raised by the {f.agent} agent:_ {f.title}"
        for f in cluster
        if f.id != primary.id and f.agent is not primary.agent
    ]
    return primary.body + "".join(dict.fromkeys(extra))


def _dedupe_evidence(cluster: list[Finding]) -> list:
    seen: set[tuple] = set()
    out = []
    for f in cluster:
        for e in f.evidence:
            key = (e.file_path, e.line_start, e.excerpt[:80])
            if key not in seen:
                seen.add(key)
                out.append(e)
    return out[:6]


def _collapse_repeated(findings: list[Finding]) -> list[Finding]:
    """Fold a repeated recommendation into one finding, across the whole diff.

    `_merge` only joins findings that overlap on the same lines, which is right
    for two agents describing one defect and useless for one agent making the
    same request about eight different functions. A human reviewer says "this
    needs tests" once and lists what; this does the same.

    Only categories in COLLAPSIBLE_CATEGORIES take part, and only above a
    configured count — one or two specific asks are more useful left in place,
    anchored where the work is.
    """
    threshold = get_settings().collapse_repeated_after
    groups: dict[tuple[AgentType, Category], list[Finding]] = {}
    passthrough: list[Finding] = []

    for finding in findings:
        if finding.category in COLLAPSIBLE_CATEGORIES:
            groups.setdefault((finding.agent, finding.category), []).append(finding)
        else:
            passthrough.append(finding)

    out = passthrough
    for group in groups.values():
        if len(group) < threshold:
            out.extend(group)
            continue
        out.append(_fold(group))
    return out


def _fold(group: list[Finding]) -> Finding:
    """One finding standing for several, with every location kept as evidence."""
    primary = max(group, key=lambda f: (SEVERITY_ORDER.index(f.severity), f.confidence))
    others = [f for f in group if f.id != primary.id]

    listing = "\n".join(
        f"- `{f.file_path}:{f.line_start}` — {f.title}"
        for f in sorted(others, key=lambda f: (f.file_path, f.line_start))
    )
    body = (
        f"{primary.body}\n\n"
        f"The same gap appears at {len(others)} other place(s) in this change:\n\n{listing}\n\n"
        "_Collapsed into one comment: this is one piece of work, not "
        f"{len(group)} separate ones._"
    )

    # Every folded location travels as evidence, so nothing is lost — the detail
    # is in the comment and in the database, just not in eight separate threads.
    evidence = list(primary.evidence)
    for f in others:
        evidence.append(
            Evidence(
                kind="diff",
                file_path=f.file_path,
                line_start=f.line_start,
                line_end=f.line_end,
                excerpt=f.title[:200],
            )
        )

    for f in others:
        f.merged_into = primary.id

    return primary.model_copy(
        update={
            "body": body,
            "evidence": evidence[:12],
            "confidence": max(f.confidence for f in group),
            "title": f"{primary.title} (and {len(others)} more)",
        }
    )


def overall_confidence(findings: list[Finding], verdicts: list[AgentVerdict]) -> float:
    """How much the review as a whole should be trusted.

    Severity-weighted mean over findings, then discounted by the share of the
    panel that failed. A review where the security agent timed out is not a
    confident review, whatever the three survivors said.
    """
    if verdicts:
        ok = sum(1 for v in verdicts if v.ok)
        panel_health = ok / len(verdicts)
    else:
        panel_health = 0.0

    if not findings:
        # Nothing to say is a confident outcome only if everyone reported in.
        return round(panel_health, 3)

    weight_sum = sum(f.severity.weight for f in findings)
    weighted = sum(f.confidence * f.severity.weight for f in findings) / weight_sum
    return round(weighted * panel_health, 3)


def summarise(findings: list[Finding], verdicts: list[AgentVerdict]) -> str:
    counts: dict[Severity, int] = {}
    for f in findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1
    parts = [f"{counts[s]} {s}" for s in reversed(SEVERITY_ORDER) if s in counts]
    head = ", ".join(parts) if parts else "no findings"
    failed = [str(v.agent) for v in verdicts if not v.ok]
    tail = f" (agents unavailable: {', '.join(failed)})" if failed else ""
    return f"{head}{tail}"


def agent_breakdown(verdicts: list[AgentVerdict]) -> dict[AgentType, int]:
    return {v.agent: len(v.findings) for v in verdicts}
