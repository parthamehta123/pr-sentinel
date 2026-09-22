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
