from __future__ import annotations

import os

import pytest

# Force — do not setdefault. Developers with a sourced .env otherwise keep their
# real GITHUB_WEBHOOK_SECRET / LLM_PROVIDER, and the webhook tests sign with
# "unit-test-secret" against a different verifier key (401 on every happy path).
os.environ["LLM_PROVIDER"] = "echo"
os.environ["GITHUB_WEBHOOK_SECRET"] = "unit-test-secret"
os.environ["EMBEDDING_PROVIDER"] = "hash"
# EMBEDDING_DIM is deliberately NOT forced here. It has to stay in agreement with
# the vector(N) column the migrations create, and a test-only override is exactly
# how that contract silently breaks. Tests that want a small vector construct the
# embedder with an explicit dimension.
os.environ["APP_ENV"] = "dev"

from pr_sentinel.config import get_settings
from pr_sentinel.domain.models import DiffFile, PullRequestContext
from pr_sentinel.forge.diff import build_diff_file


@pytest.fixture(autouse=True)
def _clean_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings():
    return get_settings()


SAMPLE_PATCH = """@@ -10,6 +10,14 @@ class Handler:
     def __init__(self, db):
         self.db = db
 
+    def charge(self, customer_id, amount):
+        row = self.db.execute(f"SELECT * FROM customers WHERE id = {customer_id}")
+        try:
+            return self._charge(row, amount)
+        except:
+            return None
+
     def refund(self, charge_id):
         return self.db.refunds.create(charge_id)
"""


@pytest.fixture
def diff_file() -> DiffFile:
    return build_diff_file(
        {
            "filename": "billing/handler.py",
            "status": "modified",
            "additions": 8,
            "deletions": 0,
            "patch": SAMPLE_PATCH,
        }
    )


@pytest.fixture
def pr_context(diff_file) -> PullRequestContext:
    return PullRequestContext(
        repo_full_name="acme/payments",
        repo_github_id=7,
        is_private=True,
        number=42,
        title="Add charge",
        author="dev",
        head_sha="a" * 40,
        base_sha="b" * 40,
        files=[diff_file],
    )
