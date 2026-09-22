"""Webhook authentication.

INVARIANT-1: nothing is parsed, logged, enqueued or looked up before the
signature checks out. The order matters for more than tidiness — parsing
attacker-controlled JSON, or writing an attacker-chosen repo name into the
delivery ledger, are both real work done on an unauthenticated request.
"""

from __future__ import annotations

import hashlib
import hmac

SIGNATURE_HEADER = "X-Hub-Signature-256"
DELIVERY_HEADER = "X-GitHub-Delivery"
EVENT_HEADER = "X-GitHub-Event"


def compute_signature(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(secret: str, body: bytes, header_value: str | None) -> bool:
    """Constant-time comparison. A `==` here leaks the signature one byte at a time."""
    if not header_value or not secret:
        return False
    if not header_value.startswith("sha256="):
        return False
    return hmac.compare_digest(compute_signature(secret, body), header_value)
