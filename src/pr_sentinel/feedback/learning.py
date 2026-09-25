"""Feedback learning loop.

When a developer disputes a finding, that feedback is recorded. Over time,
accumulated feedback teaches the reviewer what this team values and what it
doesn't. Three mechanisms:

1. **Preference extraction** — after N disputes with the same pattern (same
   category, same severity), extract a procedural memory rule: "this team
   does not consider X a problem." Minimum evidence threshold (ADR-0007)
   prevents one grumpy afternoon from retraining the reviewer.

2. **Feedback decay** — old feedback loses weight. A dispute from 6 months
   ago about a convention that has since changed shouldn't suppress current
   findings. Decay is exponential with a configurable half-life.

3. **Context injection** — active preferences are injected into agent prompts
   as "team conventions" so the reviewer adapts without prompt rewrites.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime

from ..db import pool
from ..logging import get_logger

log = get_logger(__name__)

# Minimum disputes before a preference is extracted
MIN_EVIDENCE_THRESHOLD = 3
# How many days until feedback weight halves
DECAY_HALF_LIFE_DAYS = 90
# Maximum age of feedback to consider (days)
MAX_FEEDBACK_AGE_DAYS = 365


@dataclass
class TeamPreference:
    pattern: str
    category: str
    severity: str
    dispute_count: int
    last_seen: datetime
    weight: float


def _decay_weight(created_at: datetime, now: datetime | None = None) -> float:
    """Exponential decay: weight halves every DECAY_HALF_LIFE_DAYS days."""
    now = now or datetime.now(UTC)
    age_days = (now - created_at).total_seconds() / 86400
    if age_days > MAX_FEEDBACK_AGE_DAYS:
        return 0.0
    return math.exp(-0.693 * age_days / DECAY_HALF_LIFE_DAYS)


async def extract_preferences(repo_full_name: str) -> list[TeamPreference]:
    """Extract team preferences from accumulated feedback.

    Groups disputed findings by (category, severity pattern) and returns
    preferences where the dispute count meets the minimum evidence threshold.
    """
    rows = await pool.fetch(
        """
        SELECT
            f.category,
            f.severity,
            COUNT(*) as dispute_count,
            MAX(fb.created_at) as last_seen,
            array_agg(fb.created_at) as timestamps
        FROM feedback fb
        JOIN findings f ON f.id = fb.finding_id
        JOIN reviews r ON r.id = fb.review_id
        WHERE fb.verdict = 'disputed'
          AND r.repo = $1
          AND fb.created_at > now() - interval '%s days' % $2
        GROUP BY f.category, f.severity
        HAVING COUNT(*) >= $3
        ORDER BY COUNT(*) DESC
        """,
        repo_full_name,
        MAX_FEEDBACK_AGE_DAYS,
        MIN_EVIDENCE_THRESHOLD,
    )

    preferences = []
    for row in rows:
        # Compute weighted dispute count (recent disputes matter more)
        weighted_count = sum(_decay_weight(ts) for ts in row["timestamps"])
        if weighted_count < MIN_EVIDENCE_THRESHOLD * 0.5:
            continue  # Decayed below threshold

        preferences.append(
            TeamPreference(
                pattern=f"{row['category']}/{row['severity']}",
                category=row["category"],
                severity=row["severity"],
                dispute_count=row["dispute_count"],
                last_seen=row["last_seen"],
                weight=weighted_count,
            )
        )

    return preferences


async def build_convention_context(repo_full_name: str) -> str:
    """Build a conventions block for agent prompts from team preferences.

    This is injected into the base prompt so agents know what this team
    considers noise vs. signal without manual prompt editing.
    """
    prefs = await extract_preferences(repo_full_name)
    if not prefs:
        return ""

    lines = ["## Team conventions (learned from feedback)\n"]
    for pref in prefs:
        lines.append(
            f"- This team has disputed {pref.category}/{pref.severity} findings "
            f"{pref.dispute_count} times. Weight these lower (weight: {pref.weight:.1f})."
        )

    return "\n".join(lines)


async def decay_old_feedback(days_older_than: int = MAX_FEEDBACK_AGE_DAYS) -> int:
    """Mark feedback older than the threshold as decayed.

    Does not delete — the append-only invariant holds. Sets a `decayed` flag
    so preference extraction ignores it without losing the audit trail.
    """
    result = await pool.execute(
        """
        UPDATE feedback
        SET decayed = true
        WHERE created_at < now() - interval '1 day' * $1
          AND decayed = false
        """,
        days_older_than,
    )
    count = int(result.split()[-1]) if result else 0
    if count > 0:
        log.info("feedback.decayed", count=count, older_than_days=days_older_than)
    return count
