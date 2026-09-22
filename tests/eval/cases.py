"""The golden set, defined as before/after source rather than as hand-written diffs.

Writing a unified diff by hand and then labelling "the injection is on line 41" is
how an eval set quietly rots: one edit shifts every number and the labels silently
stop pointing at the code they describe. Here the diff is *computed* from BEFORE
and AFTER, and labels are attached with marker lines that are stripped before the
diff is generated — so a label cannot drift away from its line, and the marker text
never reaches the model.

`expected_decision` is set only where the gate's precedence rules determine the
outcome regardless of model confidence — a critical security finding always
escalates, and a diff with no findings always suppresses. Everywhere else the
decision turns on confidence, which is a property of the model under test rather
than of the case, so labelling it would be scoring the gate against a guess. Seven
such labels were removed after the first live run showed exactly that.

Markers sit on their own line, immediately above the line they describe:

    #!EXPECT agent=security category=injection severity>=critical :: why this is real
    #!CLEAN :: why flagging this line would be a false positive

`#!` and `//!` are both accepted. `scripts/build_eval_fixtures.py` turns this file
into `tests/eval/golden/*.json`.

On provenance: these cases are hand-authored from patterns that recur in real
review, not mined from production pull requests. That makes them clean and
unambiguous, which is right for a regression gate, and it makes them easier than
reality, which is the honest caveat. Mining labelled cases from real merged PRs is
the next step for this set.
"""

from __future__ import annotations

CASES: list[dict] = []


def case(**kwargs) -> None:
    CASES.append(kwargs)


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------

case(
    id="sec-sql-injection-fstring",
    title="Add customer lookup endpoint",
    summary="An f-string SQL query one line below a correctly parameterised one.",
    expected_decision="escalate",
    context={
        "billing/db.py": '''"""Thin database helpers used across the billing package."""


def execute(sql, params=None):
    """Run a parameterised query. `params` must carry every caller value."""
    return _cursor.execute(sql, params)
''',
    },
    before={
        "billing/queries.py": """from billing.db import execute


def get_customer(customer_id):
    return execute("SELECT * FROM customers WHERE id = %s", (customer_id,))
""",
    },
    after={
        "billing/queries.py": """from billing.db import execute


def get_customer(customer_id):
    #!CLEAN :: correctly parameterised; flagging this is a false positive
    return execute("SELECT * FROM customers WHERE id = %s", (customer_id,))


def search_customers(term, status):
    #!EXPECT agent=security category=injection severity>=critical :: `term` is interpolated straight into the WHERE clause
    sql = f"SELECT * FROM customers WHERE name LIKE '%{term}%' AND status = '{status}'"
    return execute(sql)
""",
    },
)

case(
    id="sec-missing-authz",
    title="Allow updating a saved payment method",
    summary="An endpoint that loads an object by id and mutates it without checking ownership.",
    expected_decision="escalate",
    context={
        "api/auth.py": '''def current_user(request):
    """Return the authenticated user. Authentication only — never authorisation."""
    return request.state.user


def require_owner(user, obj):
    """Raise Forbidden unless `user` owns `obj`. Every object mutation must call this."""
    if obj.user_id != user.id:
        raise Forbidden()
''',
    },
    before={
        "api/payment_methods.py": """from api.auth import current_user, require_owner


def get_payment_method(request, pm_id):
    pm = PaymentMethod.get(pm_id)
    require_owner(current_user(request), pm)
    return pm.to_dict()
""",
    },
    after={
        "api/payment_methods.py": """from api.auth import current_user, require_owner


def get_payment_method(request, pm_id):
    pm = PaymentMethod.get(pm_id)
    require_owner(current_user(request), pm)
    return pm.to_dict()


def update_payment_method(request, pm_id, body):
    user = current_user(request)
    #!EXPECT agent=security category=authz severity>=critical :: loaded by id and mutated with no require_owner call, unlike the getter above
    pm = PaymentMethod.get(pm_id)
    pm.billing_name = body["billing_name"]
    pm.save()
    return pm.to_dict()
""",
    },
)

# The credentials in this case use a fictional `acme_live_` / `acme_test_` prefix.
# Anything shaped like a real vendor key trips GitHub push protection on every
# push and gets reported by every secret scanner downstream — a permanent tax to
# make a point that a made-up prefix makes just as well. Keep it that way.
case(
    id="sec-hardcoded-credential",
    title="Wire up the payments client",
    summary="A credential-shaped literal committed to source, plus a fake one in a test fixture.",
    expected_decision="escalate",
    before={
        "billing/acmepay_client.py": """import os

import acmepay


def client():
    acmepay.api_key = os.environ["ACMEPAY_SECRET_KEY"]
    return acmepay
""",
    },
    after={
        "billing/acmepay_client.py": """import acmepay


#!EXPECT agent=security category=secrets severity>=critical :: a live secret key committed to the repository, replacing an environment lookup
ACMEPAY_SECRET_KEY = "acme_live_7f3c9d2e8b1a45061f2d8c9e3b7a5140"


def client():
    acmepay.api_key = ACMEPAY_SECRET_KEY
    return acmepay
""",
        "tests/fixtures/acmepay.py": '''"""Fixtures for the payments client tests."""

#!CLEAN :: an obvious test double in a fixtures path, not a real credential
TEST_KEY = "acme_test_00000000000000000000000000"


def fake_client():
    return {"api_key": TEST_KEY}
''',
    },
)

case(
    id="sec-weak-password-hash",
    title="Add a password reset path",
    summary="MD5 used for password storage; SHA-256 used correctly for a file checksum.",
    expected_decision=None,  # depends on model confidence, not on the case
    before={
        "auth/passwords.py": """import bcrypt


def hash_password(raw):
    return bcrypt.hashpw(raw.encode(), bcrypt.gensalt()).decode()
""",
    },
    after={
        "auth/passwords.py": """import hashlib

import bcrypt


def hash_password(raw):
    return bcrypt.hashpw(raw.encode(), bcrypt.gensalt()).decode()


def hash_reset_password(raw):
    #!EXPECT agent=security category=crypto severity>=major :: MD5 for password storage, alongside bcrypt in the same module
    return hashlib.md5(raw.encode()).hexdigest()


def checksum(path):
    #!CLEAN :: SHA-256 for file integrity is not password hashing
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()
""",
    },
)

case(
    id="sec-command-injection",
    title="Add an archive export command",
    summary="subprocess with shell=True and an interpolated user-supplied name.",
    expected_decision="escalate",
    before={
        "export/archive.py": """import subprocess


def list_exports(directory):
    return subprocess.run(["ls", directory], capture_output=True, check=True).stdout
""",
    },
    after={
        "export/archive.py": """import subprocess


def list_exports(directory):
    return subprocess.run(["ls", directory], capture_output=True, check=True).stdout


def create_archive(name, directory):
    #!EXPECT agent=security category=injection severity>=critical :: shell=True with `name` interpolated, so a name containing a shell metacharacter runs arbitrary commands
    cmd = f"tar -czf /exports/{name}.tar.gz {directory}"
    return subprocess.run(cmd, shell=True, check=True)
""",
    },
)

case(
    id="sec-ssrf-webhook-url",
    title="Let customers register a webhook target",
    summary="A caller-supplied URL fetched server-side with no allowlist or scheme check.",
    expected_decision=None,  # depends on model confidence, not on the case
    before={
        "integrations/webhooks.py": """import requests

ALLOWED_HOSTS = {"hooks.slack.com", "api.pagerduty.com"}


def deliver(url, payload):
    host = _host_of(url)
    if host not in ALLOWED_HOSTS:
        raise ValueError("destination not allowed")
    return requests.post(url, json=payload, timeout=5)
""",
    },
    after={
        "integrations/webhooks.py": """import requests

ALLOWED_HOSTS = {"hooks.slack.com", "api.pagerduty.com"}


def deliver(url, payload):
    host = _host_of(url)
    if host not in ALLOWED_HOSTS:
        raise ValueError("destination not allowed")
    return requests.post(url, json=payload, timeout=5)


def verify_endpoint(url):
    #!EXPECT agent=security category=input_validation severity>=major :: caller-supplied URL fetched server-side, bypassing the ALLOWED_HOSTS check the sibling function applies
    return requests.get(url, timeout=5).status_code
""",
    },
)


# ---------------------------------------------------------------------------
# Correctness
# ---------------------------------------------------------------------------

case(
    id="cor-contract-break-return-shape",
    title="Return richer data from lookup_user",
    summary="Return type changes from a tuple to a dict; an existing caller still unpacks it.",
    expected_decision=None,  # depends on model confidence, not on the case
    context={
        "reports/weekly.py": '''from users.lookup import lookup_user


def render_row(user_id):
    """Existing caller. Unpacks the tuple lookup_user has always returned."""
    name, email = lookup_user(user_id)
    return f"{name} <{email}>"
''',
    },
    before={
        "users/lookup.py": '''def lookup_user(user_id):
    """Return (name, email) for a user."""
    row = _db.fetch_one("SELECT name, email FROM users WHERE id = %s", (user_id,))
    return row["name"], row["email"]
''',
    },
    after={
        "users/lookup.py": '''def lookup_user(user_id):
    """Return (name, email) for a user."""
    row = _db.fetch_one(
        "SELECT name, email, tier FROM users WHERE id = %s", (user_id,)
    )
    #!EXPECT agent=correctness category=api_contract severity>=major :: the return shape changed from a 2-tuple to a dict; reports/weekly.py still unpacks two values
    return {"name": row["name"], "email": row["email"], "tier": row["tier"]}
''',
    },
)

case(
    id="cor-bare-except",
    title="Make the metrics push non-fatal",
    summary="A bare except that also swallows KeyboardInterrupt and SystemExit.",
    expected_decision=None,  # depends on model confidence, not on the case
    before={
        "telemetry/push.py": """def push_metrics(payload):
    return _client.send(payload)
""",
    },
    after={
        "telemetry/push.py": """def push_metrics(payload):
    try:
        return _client.send(payload)
    #!EXPECT agent=correctness category=error_handling severity>=major :: a bare except also catches KeyboardInterrupt and SystemExit, so the process stops being interruptible
    except:
        return None
""",
    },
)

case(
    id="cor-off-by-one-pagination",
    title="Add pagination to the export endpoint",
    summary="A slice that drops the last record on every page.",
    expected_decision=None,  # depends on model confidence, not on the case
    before={
        "export/pages.py": """PAGE_SIZE = 100


def page_of(records, page):
    start = page * PAGE_SIZE
    return records[start : start + PAGE_SIZE]
""",
    },
    after={
        "export/pages.py": """PAGE_SIZE = 100


def page_of(records, page):
    start = page * PAGE_SIZE
    return records[start : start + PAGE_SIZE]


def page_of_sorted(records, page, key):
    ordered = sorted(records, key=key)
    start = page * PAGE_SIZE
    #!EXPECT agent=correctness category=logic severity>=major :: the end index is one short, so the last record of every page is silently dropped
    return ordered[start : start + PAGE_SIZE - 1]
""",
    },
)

case(
    id="cor-resource-leak-error-path",
    title="Add a CSV importer",
    summary="A file handle that leaks on the validation-failure path.",
    expected_decision=None,  # depends on model confidence, not on the case
    before={
        "importer/csv_import.py": """import csv


def read_rows(path):
    with open(path) as fh:
        return list(csv.reader(fh))
""",
    },
    after={
        "importer/csv_import.py": """import csv


def read_rows(path):
    with open(path) as fh:
        return list(csv.reader(fh))


def read_validated(path, expected_header):
    #!EXPECT agent=correctness category=resource_leak severity>=major :: the handle is opened without a context manager and is never closed on the early-return path
    fh = open(path)
    reader = csv.reader(fh)
    header = next(reader)
    if header != expected_header:
        return None
    rows = list(reader)
    fh.close()
    return rows
""",
    },
)

case(
    id="cor-check-then-act-race",
    title="Add a per-tenant rate limiter",
    summary="Check-then-act on shared state across an await.",
    expected_decision=None,  # depends on model confidence, not on the case
    before={
        "limits/ratelimit.py": """import asyncio

_counters = {}
_lock = asyncio.Lock()


async def consume(tenant, cost):
    async with _lock:
        _counters[tenant] = _counters.get(tenant, 0) + cost
        return _counters[tenant]
""",
    },
    after={
        "limits/ratelimit.py": """import asyncio

_counters = {}
_lock = asyncio.Lock()
LIMIT = 1000


async def consume(tenant, cost):
    async with _lock:
        _counters[tenant] = _counters.get(tenant, 0) + cost
        return _counters[tenant]


async def try_consume(tenant, cost):
    current = _counters.get(tenant, 0)
    if current + cost > LIMIT:
        return False
    #!EXPECT agent=correctness category=concurrency severity>=major :: the limit is read and then acted on across an await without the lock the sibling function holds, so concurrent callers both pass the check
    await asyncio.sleep(0)
    _counters[tenant] = current + cost
    return True
""",
    },
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

case(
    id="tst-untested-error-branch",
    title="Reject expired coupons",
    summary="A new error branch; the test added in the same PR only covers the happy path.",
    expected_decision=None,  # depends on model confidence, not on the case
    before={
        "billing/coupons.py": """def apply_coupon(order, coupon):
    order.total -= coupon.amount
    return order
""",
        "tests/test_coupons.py": """def test_apply_coupon_reduces_total():
    order = Order(total=100)
    assert apply_coupon(order, Coupon(amount=10)).total == 90
""",
    },
    after={
        "billing/coupons.py": """class CouponExpired(Exception):
    pass


def apply_coupon(order, coupon):
    #!EXPECT agent=tests category=test_coverage severity>=major :: the new CouponExpired branch has no test; the only test added covers the unexpired path
    if coupon.expires_at < now():
        raise CouponExpired(coupon.code)
    order.total -= coupon.amount
    return order
""",
        "tests/test_coupons.py": """def test_apply_coupon_reduces_total():
    order = Order(total=100)
    assert apply_coupon(order, Coupon(amount=10, expires_at=tomorrow())).total == 90
""",
    },
)

case(
    id="tst-assertion-free-test",
    title="Add tests for the invoice renderer",
    summary="One test asserts on a mock instead of behaviour; another asserts nothing at all.",
    expected_decision=None,  # depends on model confidence, not on the case
    before={
        "tests/test_invoice.py": """def test_renders_total():
    assert render(Invoice(total=100)).endswith("100.00")
""",
    },
    after={
        "tests/test_invoice.py": """def test_renders_total():
    assert render(Invoice(total=100)).endswith("100.00")


def test_renders_line_items(mocker):
    fmt = mocker.patch("invoice.format_line")
    render(Invoice(lines=[Line(1), Line(2)]))
    #!EXPECT agent=tests category=test_quality severity>=minor :: asserts the mock was called rather than anything about the rendered output, so it breaks on refactors and catches no bugs
    assert fmt.call_count == 2


def test_renders_empty_invoice():
    #!EXPECT agent=tests category=test_quality severity>=major :: no assertion at all; this test cannot fail
    render(Invoice(lines=[]))
""",
    },
)


# ---------------------------------------------------------------------------
# Documentation
# ---------------------------------------------------------------------------

case(
    id="doc-stale-docstring",
    title="Return usage broken down by day",
    summary="The implementation changes; the docstring above it now describes something else.",
    expected_decision=None,  # depends on model confidence, not on the case
    before={
        "usage/report.py": '''def monthly_usage(account_id):
    """Return the total number of API calls this month as an integer."""
    return _db.scalar("SELECT count(*) FROM calls WHERE account_id = %s", (account_id,))
''',
    },
    after={
        "usage/report.py": '''def monthly_usage(account_id):
    """Return the total number of API calls this month as an integer."""
    #!EXPECT agent=docs category=documentation severity>=minor :: the docstring above still promises an integer total; this now returns a per-day mapping
    rows = _db.fetch("SELECT day, count(*) FROM calls WHERE account_id = %s GROUP BY day", (account_id,))
    return {row["day"]: row["count"] for row in rows}
''',
    },
)


# ---------------------------------------------------------------------------
# Negative control — nothing here should be reported
# ---------------------------------------------------------------------------

case(
    id="neg-clean-extract-method",
    title="Extract the discount calculation",
    summary=(
        "A pure refactor with a test and a docstring. Any finding here is a false "
        "positive, and this case is the one that stops the set rewarding noise."
    ),
    expected_decision="suppress",
    before={
        "billing/totals.py": '''def order_total(order):
    """Return the order total after discounts, as a Decimal."""
    subtotal = sum(line.amount for line in order.lines)
    discount = Decimal("0")
    if order.customer.tier == "gold":
        discount = subtotal * Decimal("0.10")
    return subtotal - discount
''',
    },
    after={
        "billing/totals.py": '''#!CLEAN :: a documented, tested pure extraction; nothing here is a defect
def discount_for(customer, subtotal):
    """Return the discount owed to `customer` on `subtotal`, as a Decimal."""
    if customer.tier == "gold":
        return subtotal * Decimal("0.10")
    return Decimal("0")


def order_total(order):
    """Return the order total after discounts, as a Decimal."""
    subtotal = sum(line.amount for line in order.lines)
    return subtotal - discount_for(order.customer, subtotal)
''',
        "tests/test_totals.py": """#!CLEAN :: a real assertion on real behaviour
def test_gold_customers_get_ten_percent():
    assert discount_for(Customer(tier="gold"), Decimal("100")) == Decimal("10")


def test_standard_customers_get_nothing():
    assert discount_for(Customer(tier="standard"), Decimal("100")) == Decimal("0")
""",
    },
)
