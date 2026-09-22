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

from ..domain.enums import SEVERITY_ORDER, AgentType, Severity
from ..domain.models import AgentVerdict, Finding
from ..logging import get_logger

log = get_logger(__name__)

TITLE_SIMILARITY_THRESHOLD = 0.45


def aggregate(verdicts: list[AgentVerdict]) -> tuple[list[Finding], float]:
    """Return (merged findings, overall confidence)."""
    findings = [f for v in verdicts if v.ok for f in v.findings]
    merged = _merge(findings)
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
    if not a.overlaps(b):
        return False
    if a.category == b.category:
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
