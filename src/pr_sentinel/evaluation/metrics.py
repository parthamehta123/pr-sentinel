"""Scoring.

Three questions, in descending order of how much they matter:

1. **Is confidence calibrated?** The gate's whole behaviour rests on 0.8 meaning
   roughly 80%. If it does not, every threshold in the system is arbitrary. This
   is measured and almost nobody measures it.
2. **Precision.** What makes the tool worth reading. Reported strictly (unlabelled
   findings count against it) and leniently (they do not), because the truth is
   between and pretending otherwise is how a set gets gamed.
3. **Recall.** Least important of the three: missing a finding costs what the
   status quo already costs, while a wrong finding costs credibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.enums import family_of
from ..domain.models import Finding
from .fixtures import EvalCase, Label
from .matching import Match, classify, missed

CONFIDENCE_BINS = [(0.0, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)]


@dataclass
class CaseResult:
    case_id: str
    matches: list[Match]
    missed: list[Label]
    decision: str
    expected_decision: str | None
    confidence: float
    cost_usd: float
    duration_ms: int
    failed_agents: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def hits(self) -> list[Match]:
        return [m for m in self.matches if m.kind == "hit"]

    @property
    def false_positives(self) -> list[Match]:
        return [m for m in self.matches if m.kind == "false_positive"]

    @property
    def unlabelled(self) -> list[Match]:
        return [m for m in self.matches if m.kind == "unlabelled"]

    @property
    def allowed(self) -> list[Match]:
        return [m for m in self.matches if m.kind == "allowed"]

    @property
    def decision_correct(self) -> bool | None:
        if self.expected_decision is None:
            return None
        return self.decision == self.expected_decision


@dataclass
class Bucket:
    hits: int = 0  # findings that matched a label (several may match one label)
    allowed: int = 0  # legitimate but optional; excluded from precision entirely
    labels_found: int = 0  # distinct labels that at least one finding matched
    concerns: int = 0  # distinct (label, concern family) pairs
    false_positives: int = 0
    unlabelled: int = 0
    misses: int = 0

    @property
    def precision_strict(self) -> float:
        total = self.hits + self.false_positives + self.unlabelled
        return self.hits / total if total else 0.0

    @property
    def precision_lenient(self) -> float:
        total = self.hits + self.false_positives
        return self.hits / total if total else 0.0

    @property
    def recall(self) -> float:
        """Over distinct labels, never over matches.

        Agents routinely produce several findings on one line — the same defect
        described four ways. Counting each as a separate hit in the denominator
        would let a duplicate-happy run report high recall while missing real
        labels, which is exactly backwards.
        """
        total = self.labels_found + self.misses
        return self.labels_found / total if total else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision_strict, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def duplicate_rate(self) -> float:
        """Findings per distinct *concern*, not per label.

        Counting per label over-reports: a `tests` finding and a `docs` finding on
        one line are two different observations that the aggregator is right to
        keep separate, and the label only marks one of them. The denominator is
        therefore (label, concern family) pairs — so this measures what it claims
        to, which is the same concern said more than once.
        """
        return self.hits / self.concerns if self.concerns else 0.0

    def as_dict(self) -> dict:
        return {
            "hits": self.hits,
            "allowed": self.allowed,
            "labels_found": self.labels_found,
            "concerns": self.concerns,
            "duplicate_rate": round(self.duplicate_rate, 2),
            "false_positives": self.false_positives,
            "unlabelled": self.unlabelled,
            "misses": self.misses,
            "precision_strict": round(self.precision_strict, 3),
            "precision_lenient": round(self.precision_lenient, 3),
            "recall": round(self.recall, 3),
            "f1": round(self.f1, 3),
        }


@dataclass
class CalibrationBin:
    low: float
    high: float
    count: int = 0
    mean_confidence: float = 0.0
    hit_rate: float = 0.0

    def as_dict(self) -> dict:
        return {
            "range": f"{self.low:.2f}-{self.high:.2f}",
            "n": self.count,
            "mean_confidence": round(self.mean_confidence, 3),
            "observed_hit_rate": round(self.hit_rate, 3),
            "gap": round(self.hit_rate - self.mean_confidence, 3),
        }


@dataclass
class EvalReport:
    overall: Bucket
    by_agent: dict[str, Bucket]
    calibration: list[CalibrationBin]
    ece: float
    category_agreement: float
    agent_attribution: float
    decision_accuracy: float | None
    total_cost_usd: float
    cost_per_case_usd: float
    cases: list[CaseResult]
    provider: str
    models: dict[str, str]

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "models": self.models,
            "cases": len(self.cases),
            "overall": self.overall.as_dict(),
            "by_agent": {k: v.as_dict() for k, v in self.by_agent.items()},
            "calibration": {
                "bins": [b.as_dict() for b in self.calibration if b.count],
                "expected_calibration_error": round(self.ece, 3),
            },
            "category_agreement": round(self.category_agreement, 3),
            "agent_attribution": round(self.agent_attribution, 3),
            "decision_accuracy": (
                round(self.decision_accuracy, 3) if self.decision_accuracy is not None else None
            ),
            "cost": {
                "total_usd": round(self.total_cost_usd, 4),
                "per_case_usd": round(self.cost_per_case_usd, 4),
            },
            "per_case": [
                {
                    "id": c.case_id,
                    "hits": len(c.hits),
                    "missed": len(c.missed),
                    "false_positives": len(c.false_positives),
                    "unlabelled": len(c.unlabelled),
                    "decision": c.decision,
                    "expected_decision": c.expected_decision,
                    "confidence": round(c.confidence, 3),
                    "cost_usd": round(c.cost_usd, 4),
                    "failed_agents": c.failed_agents,
                    "error": c.error,
                    # Every finding, so a saved run can be diagnosed without
                    # paying to reproduce it. This is what turns "duplicate rate
                    # 2.71" from a number into something you can act on.
                    "findings": [
                        {
                            "match": m.kind,
                            "agent": str(m.finding.agent),
                            "category": str(m.finding.category),
                            "severity": str(m.finding.severity),
                            "confidence": round(m.finding.confidence, 3),
                            "where": f"{m.finding.file_path}:{m.finding.line_start}"
                            + (
                                f"-{m.finding.line_end}" if m.finding.line_end != m.finding.line_start else ""
                            ),
                            "title": m.finding.title,
                            # What a reviewer actually reads. Without it you
                            # cannot tell a thin finding from a thin citation.
                            "body": m.finding.body,
                            "agreeing": m.finding.agreeing,
                            "matched_label": (f"{m.label.file_path}:{m.label.line}" if m.label else None),
                            "matched_labels": [f"{lab.file_path}:{lab.line}" for lab in m.labels],
                            # A consolidated finding covers what it cites, so the
                            # citations have to stay inspectable after the fact.
                            "cites": [
                                f"{e.file_path}:{e.line_start}"
                                for e in m.finding.evidence
                                if e.line_start is not None
                            ],
                        }
                        for m in c.matches
                    ],
                }
                for c in self.cases
            ],
        }


def score_case(case: EvalCase, findings: list[Finding], **meta) -> CaseResult:
    matches = classify(findings, case.expected, case.must_not_find, case.may_find, case.permitted_concerns)
    return CaseResult(
        case_id=case.id,
        matches=matches,
        missed=missed(case.expected, matches),
        expected_decision=case.expected_decision,
        **meta,
    )


def score(results: list[CaseResult], provider: str, models: dict[str, str]) -> EvalReport:
    overall = Bucket()
    by_agent: dict[str, Bucket] = {}

    for result in results:
        seen_labels: set[int] = set()
        seen_concerns: set[tuple[int, str]] = set()
        seen_per_agent: dict[str, set[int]] = {}
        for match in result.matches:
            agent = str(match.finding.agent)
            bucket = by_agent.setdefault(agent, Bucket())
            if match.kind == "hit":
                overall.hits += 1
                bucket.hits += 1
                # A collapsed finding covers every label it carries, not just
                # the first — otherwise consolidation would score as lost recall.
                agent_seen = seen_per_agent.setdefault(agent, set())
                for lab in match.labels:
                    if id(lab) not in seen_labels:
                        seen_labels.add(id(lab))
                        overall.labels_found += 1
                    concern = (id(lab), family_of(match.finding.category))
                    if concern not in seen_concerns:
                        seen_concerns.add(concern)
                        overall.concerns += 1
                    if id(lab) not in agent_seen:
                        agent_seen.add(id(lab))
                        bucket.labels_found += 1
            elif match.kind == "false_positive":
                overall.false_positives += 1
                bucket.false_positives += 1
            elif match.kind == "allowed":
                overall.allowed += 1
                bucket.allowed += 1
            else:
                overall.unlabelled += 1
                bucket.unlabelled += 1
        for label in result.missed:
            overall.misses += 1
            if label.agent:
                by_agent.setdefault(label.agent, Bucket()).misses += 1

    all_matches = [m for r in results for m in r.matches]
    hits = [m for m in all_matches if m.kind == "hit"]

    decided = [r for r in results if r.decision_correct is not None]
    total_cost = sum(r.cost_usd for r in results)

    return EvalReport(
        overall=overall,
        by_agent=dict(sorted(by_agent.items())),
        calibration=_calibration(all_matches),
        ece=_ece(all_matches),
        category_agreement=(sum(1 for m in hits if m.category_correct) / len(hits) if hits else 0.0),
        agent_attribution=(sum(1 for m in hits if m.agent_correct) / len(hits) if hits else 0.0),
        decision_accuracy=(sum(1 for r in decided if r.decision_correct) / len(decided) if decided else None),
        total_cost_usd=total_cost,
        cost_per_case_usd=total_cost / len(results) if results else 0.0,
        cases=results,
        provider=provider,
        models=models,
    )


def _scoreable(matches: list[Match]) -> list[Match]:
    """Findings that calibration can learn from.

    A permitted finding is neither right nor wrong, so it is not evidence either
    way. Counting one as "not a hit" put the tests and docs observations — which
    cluster between 0.55 and 0.75 — into those bins as failures, and reported a
    calibration error of 0.276 for a model that had not changed.
    """
    return [m for m in matches if m.kind != "allowed"]


def _calibration(matches: list[Match]) -> list[CalibrationBin]:
    matches = _scoreable(matches)
    bins = [CalibrationBin(low=lo, high=hi) for lo, hi in CONFIDENCE_BINS]
    for b, (lo, hi) in zip(bins, CONFIDENCE_BINS, strict=True):
        members = [m for m in matches if lo <= m.finding.confidence < hi]
        if not members:
            continue
        b.count = len(members)
        b.mean_confidence = sum(m.finding.confidence for m in members) / len(members)
        # An unlabelled finding counts as "not a hit" here, which biases observed
        # accuracy downward — some unlabelled findings are real. Read a negative
        # gap in a bin with many unlabelled findings with that in mind.
        b.hit_rate = sum(1 for m in members if m.kind == "hit") / len(members)
    return bins


def _ece(matches: list[Match]) -> float:
    """Expected calibration error: mean |confidence - accuracy|, weighted by bin size."""
    matches = _scoreable(matches)
    if not matches:
        return 0.0
    total = len(matches)
    error = 0.0
    for b in _calibration(matches):
        if b.count:
            error += (b.count / total) * abs(b.hit_rate - b.mean_confidence)
    return error
