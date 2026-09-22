"""INVARIANT-1: nothing happens before the signature checks out."""

from __future__ import annotations

import pytest

from pr_sentinel.ingress.security import compute_signature, verify_signature

SECRET = "unit-test-secret"
BODY = b'{"action":"opened","number":1}'


def test_valid_signature_is_accepted():
    assert verify_signature(SECRET, BODY, compute_signature(SECRET, BODY))


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "sha256=",
        "deadbeef",  # no algorithm prefix
        "sha1=" + compute_signature(SECRET, BODY)[7:],  # wrong algorithm
        compute_signature(SECRET, BODY).upper().replace("SHA256=", "sha256="),
    ],
)
def test_malformed_headers_are_rejected(header):
    assert not verify_signature(SECRET, BODY, header)


def test_tampered_body_is_rejected():
    sig = compute_signature(SECRET, BODY)
    assert not verify_signature(SECRET, BODY + b" ", sig)
    assert not verify_signature(SECRET, b'{"action":"closed"}', sig)


def test_wrong_secret_is_rejected():
    assert not verify_signature("other-secret", BODY, compute_signature(SECRET, BODY))


def test_empty_secret_never_validates():
    """An unconfigured deployment must fail closed, not accept everything."""
    assert not verify_signature("", BODY, compute_signature("", BODY))


def test_signature_is_deterministic_and_prefixed():
    sig = compute_signature(SECRET, BODY)
    assert sig == compute_signature(SECRET, BODY)
    assert sig.startswith("sha256=")
    assert len(sig) == len("sha256=") + 64
