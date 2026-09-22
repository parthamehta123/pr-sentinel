"""The ingress contract, exercised through the real ASGI app."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pr_sentinel.ingress import routes
from pr_sentinel.ingress.security import compute_signature

SECRET = "unit-test-secret"

PAYLOAD = {
    "action": "opened",
    "pull_request": {
        "number": 42,
        "title": "Add charge",
        "draft": False,
        "head": {"sha": "a" * 40},
        "base": {"sha": "b" * 40},
        "user": {"login": "dev"},
    },
    "repository": {"id": 7, "full_name": "acme/payments", "private": True},
}


@pytest.fixture
def client(monkeypatch):
    """A bare app around the router: no lifespan, so no database or Redis."""
    enqueued: list = []
    seen: set[str] = set()

    async def fake_seen(delivery_id: str) -> bool:
        if delivery_id in seen:
            return True
        seen.add(delivery_id)
        return False

    async def fake_record(job, status="accepted") -> bool:
        return True

    async def fake_enqueue(job):
        enqueued.append(job)
        return "job-1"

    monkeypatch.setattr(routes, "seen_delivery", fake_seen)
    monkeypatch.setattr(routes.review_repo, "record_delivery", fake_record)
    monkeypatch.setattr(routes, "enqueue_review", fake_enqueue)

    app = FastAPI()
    app.include_router(routes.router)
    c = TestClient(app)
    c.enqueued = enqueued  # type: ignore[attr-defined]
    return c


def _post(client, payload=PAYLOAD, delivery="d-1", event="pull_request", sign=True, secret=SECRET):
    body = json.dumps(payload).encode()
    headers = {
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery,
        "Content-Type": "application/json",
    }
    if sign:
        headers["X-Hub-Signature-256"] = compute_signature(secret, body)
    return client.post("/webhooks/github", content=body, headers=headers)


def test_unsigned_request_is_rejected_and_nothing_is_enqueued(client):
    assert _post(client, sign=False).status_code == 401
    assert client.enqueued == []


def test_wrong_secret_is_rejected(client):
    assert _post(client, secret="wrong").status_code == 401
    assert client.enqueued == []


def test_signed_pull_request_is_accepted_and_enqueued(client):
    resp = _post(client)
    assert resp.status_code == 202
    assert resp.json()["detail"] == "accepted"
    assert len(client.enqueued) == 1
    assert client.enqueued[0].pr_number == 42


def test_redelivery_is_deduplicated(client):
    """INVARIANT-2: GitHub retries; the reviewer must not."""
    assert _post(client, delivery="dup").status_code == 202
    second = _post(client, delivery="dup")
    assert second.status_code == 200
    assert second.json()["detail"] == "duplicate"
    assert len(client.enqueued) == 1


def test_malformed_json_after_valid_signature_is_400_not_500(client):
    body = b"{not json"
    resp = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "d-bad",
            "X-Hub-Signature-256": compute_signature(SECRET, body),
        },
    )
    assert resp.status_code == 400
    assert client.enqueued == []


def test_ping_is_answered(client):
    assert _post(client, event="ping").status_code == 200


def test_irrelevant_events_are_acknowledged_not_processed(client):
    assert _post(client, event="star").status_code == 202
    assert client.enqueued == []


def test_irrelevant_actions_are_ignored(client):
    payload = {**PAYLOAD, "action": "labeled"}
    assert _post(client, payload=payload, delivery="d-lab").status_code == 202
    assert client.enqueued == []


def test_draft_pull_requests_are_skipped(client):
    payload = json.loads(json.dumps(PAYLOAD))
    payload["pull_request"]["draft"] = True
    resp = _post(client, payload=payload, delivery="d-draft")
    assert resp.status_code == 202
    assert resp.json()["reason"] == "draft"
    assert client.enqueued == []


def test_missing_delivery_header_is_rejected(client):
    body = json.dumps(PAYLOAD).encode()
    resp = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": compute_signature(SECRET, body),
        },
    )
    assert resp.status_code == 400


def test_structurally_wrong_payload_is_400(client):
    payload = {"action": "opened", "pull_request": {}, "repository": {}}
    assert _post(client, payload=payload, delivery="d-broken").status_code == 400
