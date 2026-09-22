"""Cost guard.

Two ceilings, checked before every model call: a per-review cap that stops one
pathological 40 000-line PR from eating the day, and a daily cap that stops a
webhook storm from eating the month. Hitting either is an escalation, not a
silent degradation — a human should know the reviewer went quiet.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from ..config import get_settings
from ..db.repositories import metrics
from ..logging import get_logger

log = get_logger(__name__)


class BudgetExceeded(Exception):
    def __init__(self, scope: str, spent: float, cap: float) -> None:
        super().__init__(f"{scope} budget exhausted: ${spent:.4f} of ${cap:.2f}")
        self.scope = scope
        self.spent = spent
        self.cap = cap


@dataclass
class BudgetGuard:
    """Per-review accountant.

    The daily figure is read once at review start (it is a rollup query, and one
    review cannot meaningfully move it), then tracked locally as calls land.
    """

    review_id: uuid.UUID
    day_spent: float = 0.0
    review_spent: float = 0.0
    enabled: bool = True

    @classmethod
    async def load(cls, review_id: uuid.UUID) -> BudgetGuard:
        try:
            spent = await metrics.spend_today_usd()
        except Exception as exc:
            # Failing open is the right call: a broken rollup query must not stop
            # reviews. It is logged so the gap is visible.
            log.warning("budget.read_failed", error=str(exc))
            spent = 0.0
        return cls(review_id=review_id, day_spent=spent)

    def check(self, estimated_usd: float = 0.0) -> None:
        if not self.enabled:
            return
        s = get_settings()
        if self.review_spent + estimated_usd > s.review_cost_cap_usd:
            raise BudgetExceeded("review", self.review_spent, s.review_cost_cap_usd)
        if self.day_spent + self.review_spent + estimated_usd > s.daily_cost_cap_usd:
            raise BudgetExceeded("daily", self.day_spent + self.review_spent, s.daily_cost_cap_usd)

    def charge(self, usd: float) -> None:
        self.review_spent += usd

    @property
    def remaining_review_usd(self) -> float:
        return max(0.0, get_settings().review_cost_cap_usd - self.review_spent)
