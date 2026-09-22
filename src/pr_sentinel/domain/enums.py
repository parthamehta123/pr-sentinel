from __future__ import annotations

from enum import StrEnum


class AgentType(StrEnum):
    SECURITY = "security"
    CORRECTNESS = "correctness"
    TESTS = "tests"
    DOCS = "docs"


ALL_AGENTS: tuple[AgentType, ...] = tuple(AgentType)


class Severity(StrEnum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    INFO = "info"

    @property
    def weight(self) -> float:
        return {"critical": 1.0, "major": 0.7, "minor": 0.4, "info": 0.15}[self.value]


SEVERITY_ORDER = [Severity.INFO, Severity.MINOR, Severity.MAJOR, Severity.CRITICAL]


class Category(StrEnum):
    INJECTION = "injection"
    AUTHZ = "authz"
    SECRETS = "secrets"
    CRYPTO = "crypto"
    INPUT_VALIDATION = "input_validation"
    LOGIC = "logic"
    CONCURRENCY = "concurrency"
    ERROR_HANDLING = "error_handling"
    RESOURCE_LEAK = "resource_leak"
    API_CONTRACT = "api_contract"
    TEST_COVERAGE = "test_coverage"
    TEST_QUALITY = "test_quality"
    DOCUMENTATION = "documentation"
    READABILITY = "readability"
    CONVENTION = "convention"
    OTHER = "other"


SECURITY_CATEGORIES = frozenset(
    {Category.INJECTION, Category.AUTHZ, Category.SECRETS, Category.CRYPTO, Category.INPUT_VALIDATION}
)

# Categories that describe the same *kind* of concern. Two findings on the same
# lines from the same family are one defect described twice — which is what four
# specialists reading one diff actually produce. Across families they are not: a
# missing docstring and an injection on one line are two different things.
CATEGORY_FAMILY: dict[Category, str] = {
    Category.INJECTION: "security",
    Category.AUTHZ: "security",
    Category.SECRETS: "security",
    Category.CRYPTO: "security",
    Category.INPUT_VALIDATION: "security",
    Category.LOGIC: "correctness",
    Category.CONCURRENCY: "correctness",
    Category.ERROR_HANDLING: "correctness",
    Category.RESOURCE_LEAK: "correctness",
    Category.API_CONTRACT: "correctness",
    Category.TEST_COVERAGE: "testing",
    Category.TEST_QUALITY: "testing",
    Category.DOCUMENTATION: "docs",
    Category.READABILITY: "docs",
    Category.CONVENTION: "docs",
    Category.OTHER: "other",
}


def family_of(category: Category) -> str:
    return CATEGORY_FAMILY.get(category, "other")


class Decision(StrEnum):
    AUTO_POST = "auto_post"
    ESCALATE = "escalate"
    SUPPRESS = "suppress"


class EscalationReason(StrEnum):
    LOW_CONFIDENCE = "low_confidence"
    CRITICAL_SECURITY = "critical_security"
    AGENT_FAILURE = "agent_failure"
    BUDGET_EXCEEDED = "budget_exceeded"
    POLICY = "policy"


class VerdictStatus(StrEnum):
    OK = "ok"
    FAILED = "failed"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"
    BUDGET_DENIED = "budget_denied"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_HUMAN = "awaiting_human"
    POSTED = "posted"
    SUPPRESSED = "suppressed"
    FAILED = "failed"


# Categories where several findings are the *same recommendation* at different
# places rather than distinct defects, and so belong in one comment.
#
# `test_coverage` qualifies: "add a test for this" is one ask however many
# functions it applies to. `documentation` deliberately does not, because the
# category covers both "document this" (a repeated ask) and "this docstring is
# now wrong" (a distinct defect at each site), and collapsing the second would
# bury real problems. Security and correctness categories never qualify — two SQL
# injections in two files are two things to fix.
COLLAPSIBLE_CATEGORIES = frozenset({Category.TEST_COVERAGE})
