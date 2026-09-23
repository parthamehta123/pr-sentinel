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
    body="Adds a name/status search beside the existing id lookup so support can find customers without knowing the id.",
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
    #!ALLOW agent=correctness category=api_contract :: same defect read against execute()'s documented params contract — the values are spliced into the statement and params is omitted
    sql = f"SELECT * FROM customers WHERE name LIKE '%{term}%' AND status = '{status}'"
    return execute(sql)
""",
    },
)

case(
    id="sec-missing-authz",
    title="Allow updating a saved payment method",
    summary="An endpoint that loads an object by id and mutates it without checking ownership.",
    body="Lets a signed-in user update the payment method they have saved on file.",
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
    body="Wires the AcmePay client so charges can go out. Includes a test fixture for the payments suite.",
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
#!EXPECT agent=correctness category=logic severity>=minor :: the environment lookup is gone, so every environment now shares one key and rotating it means a deploy
ACMEPAY_SECRET_KEY = "acme_live_7f3c9d2e8b1a45061f2d8c9e3b7a5140"


def client():
    acmepay.api_key = ACMEPAY_SECRET_KEY
    return acmepay
""",
        "tests/fixtures/acmepay.py": '''"""Fixtures for the payments client tests."""

#!CLEAN :: an obvious test double in a fixtures path, not a real credential
TEST_KEY = "acme_test_00000000000000000000000000"


#!ALLOW agent=correctness category=api_contract|test_quality :: the fake's return shape does not match the real client; true of the fixture, and minor
def fake_client():
    return {"api_key": TEST_KEY}
''',
    },
)

case(
    id="sec-weak-password-hash",
    title="Add a password reset path",
    summary="MD5 used for password storage; SHA-256 used correctly for a file checksum.",
    body="Adds a password-reset hashing helper next to the existing password hash, plus a file checksum utility.",
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


#!EXPECT agent=correctness category=api_contract severity>=major :: this returns an MD5 hex digest while hash_password returns a bcrypt hash, so nothing can verify a reset password against the stored format
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
    body="Adds an export command that packs a named directory into a tarball under /exports.",
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
    #!EXPECT agent=correctness category=logic severity>=minor :: neither value is quoted, so a directory containing a space breaks the command even without an attacker
    cmd = f"tar -czf /exports/{name}.tar.gz {directory}"
    return subprocess.run(cmd, shell=True, check=True)
""",
    },
)

case(
    id="sec-ssrf-webhook-url",
    title="Let customers register a webhook target",
    summary="A caller-supplied URL fetched server-side with no allowlist or scheme check.",
    body="Lets tenants register a URL we POST events to when their invoice status changes.",
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
    #!ALLOW agent=correctness category=error_handling :: network failures escape as exceptions rather than a status; defensible either way for a verify helper
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
    body="lookup_user now returns a dict with email and role instead of a bare tuple, so callers get richer data.",
    summary=(
        "Return type changes from a tuple to a dict. An existing caller still unpacks it, "
        "and the docstring above still describes the old shape."
    ),
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
    #!EXPECT agent=docs category=documentation severity>=minor :: the docstring still promises a 2-tuple; the function now returns a dict
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
    body="Metrics push failures should not take down the request — catch and log, then continue.",
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
    body="Pages the export endpoint so large tenants are not dumped in one response.",
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
    body="CSV importer for partner catalogues. Rejects rows that fail validation before writing.",
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
    body="Per-tenant rate limiter so a noisy client cannot starve everyone else.",
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
    body="Reject coupons past their expiry. Happy-path test included.",
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
        "billing/coupons.py": """from datetime import datetime, timezone


class CouponExpired(Exception):
    pass


def apply_coupon(order, coupon):
    #!EXPECT agent=tests category=test_coverage severity>=major :: the new CouponExpired branch has no test; the only test added covers the unexpired path
    #!EXPECT agent=correctness category=api_contract severity>=minor :: apply_coupon now requires expires_at on every coupon, so any caller passing one without it breaks
    if coupon.expires_at < datetime.now(timezone.utc):
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
    body="Tests for the invoice renderer — covers the mock path and the blank-invoice case.",
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
    body="Usage endpoint now returns a per-day breakdown instead of a single total.",
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
    #!EXPECT agent=correctness category=api_contract severity>=major :: the return type changed from an integer to a mapping, so every existing caller of monthly_usage breaks
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
    body="Pulls the discount calculation into its own helper with a docstring and a unit test. Behaviour unchanged.",
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


# ---------------------------------------------------------------------------
# Repetition — the shape the other fourteen cases do not have
# ---------------------------------------------------------------------------
#
# Every other case touches one or two files, so no agent ever has the chance to
# make the same request several times. Measured across three runs, the tests
# agent produced almost exactly one finding per case — which meant the set could
# not see the failure mode it was supposed to be catching. This case gives it
# somewhere to happen: six new public functions across three files, none covered.
#
# All six are labelled. One consolidated comment citing all six locations scores
# the same recall as six separate comments, and a sixth of the posted count — so
# the set rewards saying it once rather than rewarding volume.

case(
    id="tst-repeated-coverage-gap",
    title="Add the reporting exporters",
    body="Reporting exporters for CSV, JSON, and PDF. Public entry points for each format.",
    summary=(
        "Six new public functions across three files, none tested. One comment "
        "naming the gap is the right review; six comments is the failure mode."
    ),
    expected_decision=None,
    context={
        "reports/tests/test_csv.py": '''"""The only existing exporter test in the repository."""


def test_csv_header_matches_columns():
    assert csv_header(["a", "b"]) == "a,b"
''',
    },
    before={
        "reports/csv_export.py": """def csv_header(columns):
    return ",".join(columns)
""",
        "reports/json_export.py": """import json


def dump(rows):
    return json.dumps(rows)
""",
        "reports/pdf_export.py": """def page_size(name):
    return {"a4": (595, 842)}[name]
""",
    },
    after={
        "reports/csv_export.py": '''def csv_header(columns):
    return ",".join(columns)


#!EXPECT agent=tests category=test_coverage severity>=minor :: new public function with no test in this change
#!EXPECT agent=correctness category=logic|injection severity>=major :: csv_rows joins raw values and never calls csv_escape, so any value containing a comma corrupts the output
def csv_rows(rows, columns):
    """Render `rows` as CSV lines, in `columns` order."""
    return [",".join(str(r.get(c, "")) for c in columns) for r in rows]


#!EXPECT agent=tests category=test_coverage severity>=minor :: new public function with no test in this change
def csv_escape(value):
    """Quote a value that contains a comma or a quote."""
    if "," in value or \'"\' in value:
        return \'"\' + value.replace(\'"\', \'""\') + \'"\'
    return value
''',
        "reports/json_export.py": '''import json


def dump(rows):
    return json.dumps(rows)


#!EXPECT agent=tests category=test_coverage severity>=minor :: new public function with no test in this change
def dump_streaming(rows, chunk_size=100):
    """Yield `rows` as JSON arrays of at most `chunk_size` elements."""
    for i in range(0, len(rows), chunk_size):
        yield json.dumps(rows[i : i + chunk_size])


#!EXPECT agent=tests category=test_coverage severity>=minor :: new public function with no test in this change
def dump_pretty(rows, indent=2):
    """Render `rows` as indented JSON."""
    return json.dumps(rows, indent=indent, sort_keys=True)
''',
        "reports/pdf_export.py": '''def page_size(name):
    return {"a4": (595, 842)}[name]


#!EXPECT agent=tests category=test_coverage severity>=minor :: new public function with no test in this change
def margins_for(name):
    """Return (top, right, bottom, left) margins in points for a page size."""
    width, height = page_size(name)
    return (height // 20, width // 20, height // 20, width // 20)


#!EXPECT agent=tests category=test_coverage severity>=minor :: new public function with no test in this change
def usable_width(name):
    """Return the drawable width in points after margins."""
    width, _ = page_size(name)
    return width - 2 * (width // 20)
''',
    },
)


# ===========================================================================
# Second tranche — added because precision saturated at 1.000 on the first.
#
# A set where every labelled defect is obvious stops discriminating once a model
# gets good. These cases are built to be harder in three specific ways: a real
# defect sits next to a plausible look-alike that must NOT be flagged; several are
# in languages other than Python; and more of them have nothing wrong at all.
# ===========================================================================


# --- security --------------------------------------------------------------

case(
    id="sec-path-traversal",
    title="Serve export files by name",
    summary="One handler normalises and confines the path; the one added next to it does not.",
    body="Serves export files by name. Preview path reuses the same layout as download.",
    expected_decision="escalate",
    before={
        "exports/serve.py": """import os

EXPORT_ROOT = "/srv/exports"


def _confined(name):
    path = os.path.realpath(os.path.join(EXPORT_ROOT, name))
    if not path.startswith(EXPORT_ROOT + os.sep):
        raise PermissionError(name)
    return path


def read_export(name):
    path = _confined(name)
    with open(path, "rb") as fh:
        return fh.read()
""",
    },
    after={
        "exports/serve.py": """import os

EXPORT_ROOT = "/srv/exports"


def _confined(name):
    path = os.path.realpath(os.path.join(EXPORT_ROOT, name))
    if not path.startswith(EXPORT_ROOT + os.sep):
        raise PermissionError(name)
    return path


def read_export(name):
    #!CLEAN :: routes through the confinement helper; this one is correct
    path = _confined(name)
    with open(path, "rb") as fh:
        return fh.read()


def read_export_preview(name, limit=4096):
    #!EXPECT agent=security category=injection|logic severity>=critical :: joins the caller's name with no realpath and no confinement check, so ../ escapes EXPORT_ROOT
    path = os.path.join(EXPORT_ROOT, name)
    with open(path, "rb") as fh:
        return fh.read(limit)
""",
    },
)

case(
    id="sec-timing-unsafe-compare",
    title="Add a second webhook provider",
    summary="A signature compared with ==, one line below one compared with compare_digest.",
    body="Adds AcmePay webhook verification alongside the existing Stripe helper.",
    # Labelled >=major, so the gate's critical-security rule cannot fire and the
    # outcome turns on the model's severity rating. Not structurally determined.
    expected_decision=None,
    before={
        "webhooks/verify.py": """import hashlib
import hmac


def verify_stripe(secret, body, header):
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)
""",
    },
    after={
        "webhooks/verify.py": """import hashlib
import hmac


def verify_stripe(secret, body, header):
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    #!CLEAN :: constant-time comparison, which is the correct form
    return hmac.compare_digest(expected, header)


def verify_acmepay(secret, body, header):
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    #!EXPECT agent=security category=crypto severity>=major :: == on a signature is not constant time and leaks the expected value a byte at a time, unlike the compare_digest call above
    #!ALLOW agent=correctness category=api_contract :: == returns False for a None or bytes header where compare_digest would raise TypeError. This is a property of the replacement, not a defect in the code under review, so it is a fair remark and a weak one.
    return expected == header
""",
    },
)

case(
    id="sec-jwt-unverified",
    title="Read the tenant id straight from the token",
    summary="Signature verification disabled to avoid a key lookup.",
    body="Reads the tenant id from the bearer token without a round-trip to the key store.",
    expected_decision="escalate",
    before={
        "auth/tokens.py": """import jwt

from auth.keys import public_key


def claims_for(token):
    return jwt.decode(token, public_key(), algorithms=["RS256"])
""",
    },
    after={
        "auth/tokens.py": """import jwt

from auth.keys import public_key


def claims_for(token):
    return jwt.decode(token, public_key(), algorithms=["RS256"])


def tenant_of(token):
    #!EXPECT agent=security category=authz|logic severity>=critical :: signature verification disabled, so any caller can forge a tenant id and the value is then trusted
    unverified = jwt.decode(token, options={"verify_signature": False})
    return unverified["tenant_id"]
""",
    },
)

case(
    id="sec-terraform-public-bucket",
    title="Add the public assets bucket",
    summary="Terraform, not Python. One bucket is deliberately public; the next is not meant to be.",
    body="Public assets bucket for the marketing site, next to the existing private one.",
    expected_decision="escalate",
    before={
        "infra/storage.tf": """resource "aws_s3_bucket" "assets" {
  bucket = "acme-public-assets"
}

resource "aws_s3_bucket_public_access_block" "assets" {
  bucket                  = aws_s3_bucket.assets.id
  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}
""",
    },
    after={
        "infra/storage.tf": """resource "aws_s3_bucket" "assets" {
  bucket = "acme-public-assets"
}

resource "aws_s3_bucket_public_access_block" "assets" {
  bucket = aws_s3_bucket.assets.id
  #!CLEAN :: a bucket named public-assets, intentionally public; this is the design
  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket" "invoices" {
  bucket = "acme-customer-invoices"
}

resource "aws_s3_bucket_public_access_block" "invoices" {
  bucket = aws_s3_bucket.invoices.id
  #!EXPECT agent=security category=authz severity>=critical :: customer invoices made world-readable by copying the public-assets block
  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}
""",
    },
)

case(
    id="sec-cors-wildcard-credentials",
    title="Open CORS for the new dashboard",
    summary="TypeScript. Wildcard origin combined with credentials.",
    body="Opens CORS so the new dashboard origin can call the API with cookies.",
    expected_decision="escalate",
    before={
        "api/src/cors.ts": """import cors from "cors";

const ALLOWED = ["https://app.acme.com", "https://admin.acme.com"];

export const corsMiddleware = cors({
  origin: ALLOWED,
  credentials: true,
});
""",
    },
    after={
        "api/src/cors.ts": """import cors from "cors";

const ALLOWED = ["https://app.acme.com", "https://admin.acme.com"];

export const corsMiddleware = cors({
  origin: ALLOWED,
  credentials: true,
});

//!EXPECT agent=security category=authz severity>=critical :: a wildcard origin together with credentials lets any site read authenticated responses
//!EXPECT agent=correctness category=logic severity>=major :: browsers reject a wildcard origin combined with credentials, so this configuration does not work at all
export const dashboardCors = cors({
  origin: "*",
  credentials: true,
});
""",
    },
)


# --- correctness -----------------------------------------------------------

case(
    id="cor-async-blocking-call",
    title="Fetch the tenant plan during request handling",
    summary="A synchronous HTTP call inside an async handler, beside an awaited one.",
    body="Fetches plan limits during request handling so the UI can show remaining quota.",
    expected_decision=None,
    before={
        "api/plans.py": """import httpx


async def fetch_plan(client: httpx.AsyncClient, tenant_id):
    response = await client.get(f"/plans/{tenant_id}")
    return response.json()
""",
    },
    after={
        "api/plans.py": """import httpx
import requests


async def fetch_plan(client: httpx.AsyncClient, tenant_id):
    #!CLEAN :: awaited async client, which is the correct form here
    response = await client.get(f"/plans/{tenant_id}")
    return response.json()


async def fetch_plan_limits(tenant_id):
    #!EXPECT agent=correctness category=concurrency severity>=major :: a synchronous request inside a coroutine blocks the whole event loop for the duration of the call
    #!ALLOW agent=security category=injection|input_validation :: tenant_id is interpolated into an internal URL path with no encoding, so a value carrying ../ or ? reaches arbitrary paths on billing.internal
    response = requests.get(f"https://billing.internal/limits/{tenant_id}", timeout=5)
    return response.json()
""",
    },
)

case(
    id="cor-mutable-default-arg",
    title="Add batch helpers to the notifier",
    summary="A mutable default argument next to a correct None default.",
    body="Batch helpers for the notifier — optional extra headers on send and on enqueue.",
    expected_decision=None,
    before={
        "notify/batch.py": """def send(recipients, extra_headers=None):
    headers = dict(extra_headers or {})
    headers["X-Batch"] = "1"
    return _dispatch(recipients, headers)
""",
    },
    after={
        "notify/batch.py": """def send(recipients, extra_headers=None):
    #!CLEAN :: None sentinel with a fresh dict per call; this is the correct pattern
    headers = dict(extra_headers or {})
    headers["X-Batch"] = "1"
    return _dispatch(recipients, headers)


#!EXPECT agent=correctness category=logic severity>=major :: the default list is created once at import and shared by every call, so failures accumulate across calls
def send_with_retries(recipients, attempted=[]):
    for recipient in recipients:
        if not _dispatch([recipient], {}):
            attempted.append(recipient)
    return attempted
""",
    },
)

case(
    id="cor-retry-non-idempotent",
    title="Retry flaky gateway calls",
    summary="A retry decorator applied to a charge, and correctly applied to a read.",
    body="Retries flaky gateway calls. Applied to the charge path and the balance read.",
    expected_decision=None,
    context={
        "billing/gateway.py": '''def charge(card_token, amount_cents):
    """POST /charges. NOT idempotent: each call creates a new charge.

    The gateway supports an Idempotency-Key header; pass one to make retries safe.
    """
    return _post("/charges", {"card": card_token, "amount": amount_cents})


def get_charge(charge_id):
    """GET /charges/{id}. Safe to repeat."""
    return _get(f"/charges/{charge_id}")
''',
    },
    before={
        "billing/resilient.py": """from billing.gateway import get_charge
from reliability import retry


@retry(attempts=3)
def fetch_charge(charge_id):
    return get_charge(charge_id)
""",
    },
    after={
        "billing/resilient.py": """from billing.gateway import charge, get_charge
from reliability import retry


@retry(attempts=3)
def fetch_charge(charge_id):
    #!CLEAN :: a GET, safe to repeat; retrying this is correct
    return get_charge(charge_id)


@retry(attempts=3)
#!EXPECT agent=correctness category=logic severity>=critical :: charge() is not idempotent and no Idempotency-Key is passed, so a timeout followed by a retry bills the customer twice
def charge_card(card_token, amount_cents):
    return charge(card_token, amount_cents)
""",
    },
)

case(
    id="cor-timezone-naive-comparison",
    title="Expire stale invitations",
    summary="A naive datetime compared against an aware one.",
    body="Expires invitations that have sat unused past the cutoff.",
    expected_decision=None,
    context={
        "models/invite.py": '''class Invite:
    """`expires_at` is stored as a timezone-aware UTC datetime."""

    expires_at: "datetime"  # always tz-aware, set from datetime.now(timezone.utc)
''',
    },
    before={
        "invites/expiry.py": """from datetime import datetime, timezone


def is_expired(invite):
    return invite.expires_at < datetime.now(timezone.utc)
""",
    },
    after={
        "invites/expiry.py": """from datetime import datetime, timezone


def is_expired(invite):
    #!CLEAN :: aware-to-aware comparison, which is correct
    return invite.expires_at < datetime.now(timezone.utc)


def expires_within(invite, hours):
    #!EXPECT agent=correctness category=logic severity>=major :: datetime.now() is naive while expires_at is tz-aware, so this raises TypeError at runtime
    cutoff = datetime.now().replace(microsecond=0)
    delta = invite.expires_at - cutoff
    return delta.total_seconds() < hours * 3600
""",
    },
)

case(
    id="cor-typescript-null-deref",
    title="Simplify the account banner",
    summary="TypeScript. Optional chaining removed from a value that is genuinely optional.",
    body="Simplifies the account banner now that the account payload is always present on this page.",
    expected_decision=None,
    context={
        "web/src/types.ts": """export interface Account {
  id: string;
  /** Absent until the customer completes onboarding. */
  billingContact?: { name: string; email: string };
}
""",
    },
    before={
        "web/src/banner.ts": """import type { Account } from "./types";

export function bannerFor(account: Account): string {
  return account.billingContact?.name ?? "No billing contact";
}
""",
    },
    after={
        "web/src/banner.ts": """import type { Account } from "./types";

export function bannerFor(account: Account): string {
  return account.billingContact?.name ?? "No billing contact";
}

export function contactEmail(account: Account): string {
  //!EXPECT agent=correctness category=logic severity>=major :: billingContact is optional and absent before onboarding, so this throws on any account that has not completed it
  return account.billingContact.email.toLowerCase();
}
""",
    },
)


# --- tests and documentation ----------------------------------------------

case(
    id="tst-mock-patches-wrong-target",
    title="Test the notification retry path",
    body="Covers the notification retry path with a patched sender.",
    summary=(
        "A test that patches where the symbol is defined rather than where it is "
        "looked up, so the real function still runs and the test proves nothing."
    ),
    expected_decision=None,
    context={
        "notify/sender.py": '''from notify.transport import deliver


def send_with_retry(message, attempts=3):
    """Note the `from ... import`: `deliver` is bound into this module's namespace
    at import time, so patching `notify.transport.deliver` does not affect it."""
    for _ in range(attempts):
        if deliver(message):
            return True
    return False
''',
    },
    before={
        "tests/test_sender.py": """def test_send_returns_true_on_first_success(mocker):
    mocker.patch("notify.sender.deliver", return_value=True)
    assert send_with_retry("hello") is True
""",
    },
    after={
        "tests/test_sender.py": """def test_send_returns_true_on_first_success(mocker):
    #!CLEAN :: patches the lookup site, notify.sender.deliver, which is correct
    mocker.patch("notify.sender.deliver", return_value=True)
    assert send_with_retry("hello") is True


def test_send_gives_up_after_three_failures(mocker):
    #!EXPECT agent=tests category=test_quality severity>=major :: patches notify.transport.deliver, but sender imported the name at module load, so the real deliver still runs and this asserts nothing about retries
    mocker.patch("notify.transport.deliver", return_value=False)
    assert send_with_retry("hello", attempts=3) is False
""",
    },
)

case(
    id="tst-time-dependent-flaky",
    title="Test the token expiry window",
    summary="A test that will fail at a midnight boundary and pass every other time.",
    body="Asserts the token is still valid inside the expiry window.",
    expected_decision=None,
    before={
        "tests/test_tokens.py": """from datetime import datetime, timedelta, timezone


def test_token_expires_after_an_hour():
    issued = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert is_expired(issued + timedelta(hours=2), now=issued + timedelta(hours=1, seconds=1))
""",
    },
    after={
        "tests/test_tokens.py": """from datetime import datetime, timedelta, timezone


def test_token_expires_after_an_hour():
    #!CLEAN :: a frozen instant passed in explicitly; deterministic
    issued = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert is_expired(issued + timedelta(hours=2), now=issued + timedelta(hours=1, seconds=1))


def test_token_issued_today_is_valid():
    #!EXPECT agent=tests category=test_quality severity>=minor :: reads the wall clock and asserts on today's date, so it fails when the run crosses midnight
    token = issue_token()
    assert token.issued_at.date() == datetime.now(timezone.utc).date()
    assert not is_expired(token.expires_at)
""",
    },
)

case(
    id="doc-wrong-parameter-name",
    title="Rename the pagination parameter",
    summary="The parameter is renamed; the docstring documenting it is not.",
    body="Renames the pagination parameter from page to offset to match the rest of the API.",
    expected_decision=None,
    before={
        "api/listing.py": '''def list_invoices(tenant_id, page_size=50, cursor=None):
    """List invoices for a tenant.

    Args:
        tenant_id: the tenant to list for.
        page_size: how many invoices to return.
        cursor: opaque continuation token from a previous call.
    """
    return _query(tenant_id, page_size, cursor)
''',
    },
    after={
        "api/listing.py": '''#!EXPECT agent=correctness category=api_contract severity>=major :: page_size is a keyword parameter; renaming it to limit breaks every caller passing it by name
def list_invoices(tenant_id, limit=50, cursor=None):
    """List invoices for a tenant.

    Args:
        tenant_id: the tenant to list for.
    #!EXPECT agent=docs category=documentation severity>=minor :: documents `page_size`, which no longer exists; the parameter is now `limit`
        page_size: how many invoices to return.
        cursor: opaque continuation token from a previous call.
    """
    return _query(tenant_id, limit, cursor)
''',
    },
)


# --- negative controls -----------------------------------------------------
#
# Three of the first sixteen cases had nothing wrong in them. That is too few to
# stop a set rewarding volume, and a reviewer's credibility is spent mostly on
# pull requests where the right answer is to say nothing.

case(
    id="neg-tests-only-addition",
    title="Add tests for the discount rules",
    body="Adds unit tests for the existing discount rules. No production code changes.",
    summary=(
        "A pull request that only adds good tests to existing, unchanged code. "
        "Every agent should be silent; the tests agent especially."
    ),
    expected_decision="suppress",
    context={
        "billing/discounts.py": '''def discount_for(tier, subtotal):
    """Return the discount for `tier` on `subtotal`, as a Decimal."""
    return {"gold": Decimal("0.10"), "silver": Decimal("0.05")}.get(tier, Decimal("0")) * subtotal
''',
    },
    before={
        "tests/test_discounts.py": """from decimal import Decimal


def test_gold_tier():
    assert discount_for("gold", Decimal("100")) == Decimal("10")
""",
    },
    after={
        "tests/test_discounts.py": """from decimal import Decimal


def test_gold_tier():
    assert discount_for("gold", Decimal("100")) == Decimal("10")


#!CLEAN :: a real assertion on real behaviour, covering a previously untested branch
def test_silver_tier():
    assert discount_for("silver", Decimal("100")) == Decimal("5")


def test_unknown_tier_gets_nothing():
    assert discount_for("bronze", Decimal("100")) == Decimal("0")


def test_zero_subtotal_is_zero_discount():
    assert discount_for("gold", Decimal("0")) == Decimal("0")
""",
    },
)

case(
    id="neg-typescript-type-narrowing",
    title="Narrow the event union",
    body="The union already discriminates on `kind`, so the two casts are redundant. Removes them.",
    summary=(
        "TypeScript. A discriminated-union refactor that removes a cast and makes "
        "the code safer. Any finding here is a false positive."
    ),
    expected_decision="suppress",
    before={
        "web/src/events.ts": """type Event =
  | { kind: "click"; x: number; y: number }
  | { kind: "key"; code: string };

export function describe(event: Event): string {
  if (event.kind === "click") {
    return `click at ${(event as { x: number; y: number }).x}`;
  }
  return `key ${(event as { code: string }).code}`;
}
""",
    },
    after={
        "web/src/events.ts": """type Event =
  | { kind: "click"; x: number; y: number }
  | { kind: "key"; code: string };

//!CLEAN :: the discriminant narrows the union, so the casts are no longer needed
export function describe(event: Event): string {
  if (event.kind === "click") {
    return `click at ${event.x}`;
  }
  return `key ${event.code}`;
}
""",
    },
)

case(
    id="neg-dependency-bump",
    title="Bump httpx and record it",
    body="Bump httpx to the latest patch and note it in the changelog.",
    summary=(
        "A routine dependency bump with a changelog entry. Nothing to review, and "
        "the docs agent in particular should not invent something."
    ),
    expected_decision="suppress",
    before={
        "pyproject.toml": """[project]
name = "acme-api"
dependencies = [
  "fastapi>=0.115",
  "httpx>=0.27",
]
""",
        "CHANGELOG.md": """# Changelog

## Unreleased
""",
    },
    after={
        "pyproject.toml": """[project]
name = "acme-api"
dependencies = [
  "fastapi>=0.115",
  #!CLEAN :: a routine version bump; there is nothing here to find
  "httpx>=0.28",
]
""",
        "CHANGELOG.md": """# Changelog

## Unreleased

- Bump httpx to 0.28 for the connection-pool fix in 0.28.0.
""",
    },
)


case(
    id="doc-readme-flag-renamed",
    title="Rename the --workers flag",
    summary="The CLI flag is renamed; the README still documents the old one.",
    body="Renames --workers to --concurrency to match the internal flag name.",
    expected_decision=None,
    before={
        "cli.py": """import argparse


def parser():
    p = argparse.ArgumentParser()
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--verbose", action="store_true")
    return p
""",
        "README.md": """# acme-runner

## Usage

    acme-runner --workers 8 --verbose

- `--workers` — how many worker processes to start.
- `--verbose` — log every task.
""",
    },
    after={
        "cli.py": """import argparse


def parser():
    p = argparse.ArgumentParser()
    #!EXPECT agent=correctness category=api_contract severity>=major :: renaming --workers to --concurrency breaks every existing invocation and any caller reading args.workers
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--verbose", action="store_true")
    return p
""",
        "README.md": """# acme-runner

## Usage

    acme-runner --workers 8 --verbose

#!EXPECT agent=docs category=documentation severity>=major :: the flag is now --concurrency, so the documented invocation above fails outright
- `--workers` — how many worker processes to start.
- `--verbose` — log every task.
- Set `ACME_LOG_FORMAT=json` for structured logs.
""",
    },
)

case(
    id="doc-misleading-name",
    title="Add a cached lookup helper",
    summary="A get_* that mutates, alongside a get_* that does not.",
    body="Adds get_tenant_fresh for callers that need a live fetch into the shared cache.",
    expected_decision=None,
    before={
        "cache/lookup.py": """_CACHE = {}


def get_tenant(tenant_id):
    return _CACHE.get(tenant_id)
""",
    },
    after={
        "cache/lookup.py": """_CACHE = {}


def get_tenant(tenant_id):
    #!CLEAN :: a pure read, which is what the name promises
    return _CACHE.get(tenant_id)


#!EXPECT agent=docs category=readability severity>=minor :: named get_, but it writes to the shared cache and evicts entries; a caller will not expect a read to mutate
def get_tenant_fresh(tenant_id):
    tenant = _fetch(tenant_id)
    _CACHE[tenant_id] = tenant
    #!EXPECT agent=correctness category=logic severity>=major :: the size-bound eviction clears the whole cache, discarding the entry just written and every other tenant's
    #!ALLOW agent=security category=input_validation :: the same clear is reachable from a caller-supplied tenant_id, so a requester can thrash every other tenant's entries out of the shared cache
    if len(_CACHE) > 1000:
        _CACHE.clear()
    return tenant
""",
    },
)


# ===========================================================================
# Third tranche — precision 0.983 and recall 1.000 meant the set had saturated
# again. These attack the two things it could not see.
#
# First: defects that are invisible in the diff alone and only wrong given what
# is elsewhere in the repository. That is what retrieval is for, and almost
# nothing in the set was testing it.
#
# Second: whole cases that look alarming and are correct. Every trap so far has
# been a clean sibling beside a real defect, which is a weaker test than a change
# that reads as a vulnerability from top to bottom and is not one.
# ===========================================================================


# --- only wrong given the rest of the repository ---------------------------

case(
    id="ctx-duplicate-index-migration",
    title="Add an index for the reporting tenant lookup",
    summary=(
        "Creates an index an earlier migration already created, under a different "
        "name. The generic complaints about CREATE INDEX are pre-empted in the "
        "diff — CONCURRENTLY, and a note that the migration is non-transactional "
        "— so duplication is the only thing left to notice, and only migration "
        "0007 shows it."
    ),
    body="Reporting queries filter invoices by tenant and sort by recency. Adding the index.",
    expected_decision=None,
    context={
        "migrations/0007_invoice_indexes.sql": """-- Applied 2025-11-02.
CREATE INDEX invoices_tenant_created_idx
    ON invoices (tenant_id, created_at DESC);

CREATE INDEX invoices_status_idx ON invoices (status);
""",
    },
    before={
        "migrations/0031_reporting.sql": """-- transactional: false (CONCURRENTLY cannot run inside a transaction)
CREATE TABLE report_runs (
    id          BIGSERIAL PRIMARY KEY,
    tenant_id   BIGINT NOT NULL,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
""",
    },
    after={
        "migrations/0031_reporting.sql": """-- transactional: false (CONCURRENTLY cannot run inside a transaction)
CREATE TABLE report_runs (
    id          BIGSERIAL PRIMARY KEY,
    tenant_id   BIGINT NOT NULL,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

#!EXPECT agent=correctness category=logic severity>=minor :: migration 0007 already creates this exact index on invoices (tenant_id, created_at DESC), as invoices_tenant_created_idx; only the name differs, so this doubles write cost and storage for no read benefit
CREATE INDEX CONCURRENTLY IF NOT EXISTS invoices_tenant_recent_idx
    ON invoices (tenant_id, created_at DESC);
""",
    },
)

case(
    id="ctx-changed-default-breaks-caller",
    title="Sync the partner catalogue nightly",
    summary=(
        "A new call site passing timeout=30, which reads as thirty seconds and is "
        "thirty milliseconds. Purely additive, with no other timeout in the diff "
        "to compare against — the unit appears only in the unchanged helper's "
        "docstring."
    ),
    body="Adds the nightly partner catalogue pull. Timeout set generously since their API is slow.",
    expected_decision=None,
    context={
        "clients/http.py": '''def fetch(url, timeout, retries=3):
    """GET `url` and return the decoded body.

    `timeout` is in MILLISECONDS, matching the underlying transport. Callers
    wanting seconds must multiply. Retries share the same budget.
    """
    return _transport.get(url, timeout_ms=timeout, retries=retries)
''',
    },
    before={
        "sync/nightly.py": """from clients.http import fetch


def sync_currency_rates():
    return _rates_provider.refresh()
""",
    },
    after={
        "sync/nightly.py": """from clients.http import fetch


def sync_currency_rates():
    return _rates_provider.refresh()


def sync_partner_catalogue():
    #!EXPECT agent=correctness category=api_contract|logic severity>=major :: fetch takes timeout in milliseconds, so this is 30ms rather than the 30 seconds it reads as, and every call to a slow partner API times out immediately
    return fetch("https://partner.example.com/catalogue", timeout=30)
""",
    },
)


# --- whole cases that look alarming and are correct -------------------------

case(
    id="neg-allowlisted-dynamic-sql",
    title="Allow sorting the invoice list",
    body="Lets the invoice list sort by a fixed set of columns. Sort keys are module literals only.",
    summary=(
        "Dynamic SQL built by string formatting — and correct, because the only "
        "interpolated values are literals the module owns. Documented, validated "
        "and tested in the same change, so there is genuinely nothing to say."
    ),
    expected_decision="suppress",
    before={
        "reports/invoices.py": '''from db import execute


def list_invoices(tenant_id, limit):
    """Return one page of invoices for `tenant_id`, newest first."""
    return execute(
        "SELECT * FROM invoices WHERE tenant_id = %s ORDER BY created_at DESC LIMIT %s",
        (tenant_id, limit),
    )
''',
        "tests/test_invoices.py": """def test_lists_newest_first():
    rows = list_invoices(tenant_id=1, limit=2)
    assert [r["id"] for r in rows] == [20, 19]
""",
    },
    after={
        "reports/invoices.py": '''from db import execute

SORTABLE = {"created_at": "created_at", "amount": "amount_cents", "status": "status"}
DIRECTIONS = {"asc": "ASC", "desc": "DESC"}


def list_invoices(tenant_id, limit, sort="created_at", direction="desc"):
    """Return one page of invoices for `tenant_id`.

    `sort` must be a key of SORTABLE and `direction` a key of DIRECTIONS.
    Anything else raises ValueError before a query is built.
    """
    if sort not in SORTABLE or direction not in DIRECTIONS:
        raise ValueError(f"unsortable: {sort!r} {direction!r}")
    #!CLEAN agent=security :: both interpolated values are module-owned literals chosen by a membership test; nothing caller-controlled reaches the query
    column, order = SORTABLE[sort], DIRECTIONS[direction]
    return execute(
        f"SELECT * FROM invoices WHERE tenant_id = %s ORDER BY {column} {order} LIMIT %s",
        (tenant_id, limit),
    )
''',
        "tests/test_invoices.py": """import pytest


def test_lists_newest_first():
    rows = list_invoices(tenant_id=1, limit=2)
    assert [r["id"] for r in rows] == [20, 19]


def test_sorts_by_amount_ascending():
    rows = list_invoices(tenant_id=1, limit=2, sort="amount", direction="asc")
    assert [r["amount_cents"] for r in rows] == [100, 250]


def test_rejects_an_unknown_sort_key():
    with pytest.raises(ValueError):
        list_invoices(tenant_id=1, limit=2, sort="; DROP TABLE invoices --")


def test_rejects_an_unknown_direction():
    with pytest.raises(ValueError):
        list_invoices(tenant_id=1, limit=2, direction="asc; DROP TABLE invoices --")
""",
    },
)

case(
    id="trap-subprocess-list-args",
    title="Stop shelling out for the archive step",
    body="Stops shelling out for the archive step — list argv, name allowlisted, shell=False. Tests included.",
    summary=(
        "A refactor away from shell=True to a list argv, with the archive name "
        "allowlisted. It reads as command execution and is in fact the fix for one."
    ),
    # Not a pure negative control: the reflex it tests is wrong, but other
    # legitimate observations remain available, so the gate outcome is open.
    expected_decision=None,
    before={
        "export/archive.py": """import subprocess


def create_archive(name, directory):
    return subprocess.run(f"tar -czf /exports/{name}.tar.gz {directory}", shell=True, check=True)
""",
    },
    after={
        "export/archive.py": '''import re
import subprocess

SAFE_NAME = re.compile("[A-Za-z0-9_-]{1,64}")


def create_archive(name, directory):
    """Write /exports/<name>.tar.gz from `directory`."""
    if not SAFE_NAME.fullmatch(name):
        raise ValueError(f"unsafe archive name: {name!r}")
    #!CLEAN agent=security category=injection :: a list argv with shell=False and a -- terminator, and the name allowlisted to a safe charset, so neither value can become a shell token or a tar option — this traps the command-injection reflex only
    #!ALLOW agent=security category=input_validation :: the -- terminator stops option parsing, it does not constrain `directory` to a base path, so a caller passing untrusted input archives whatever that path points at
    #!ALLOW agent=correctness category=api_contract :: shell=True used to word-split and glob `directory`; the list argv passes it literally, which is a real behaviour change for any caller relying on that
    return subprocess.run(
        ["tar", "-czf", f"/exports/{name}.tar.gz", "--", directory],
        shell=False,
        check=True,
    )
''',
        "tests/test_archive.py": """import pytest

from export.archive import create_archive


#!ALLOW agent=tests category=test_quality :: this asserts only that shell is False, never that the argv list is well-formed, so it would pass against a re-introduced f-string in the command
def test_runs_without_a_shell(spy):
    create_archive("nightly", "/srv/data")
    assert spy.last_call.kwargs["shell"] is False


def test_rejects_a_traversing_name():
    with pytest.raises(ValueError):
        create_archive("../../etc/passwd", "/srv/data")
""",
    },
)

case(
    id="trap-md5-for-cache-key",
    title="Key the render cache by template digest",
    body="Keys the render cache on a digest of the template source plus context so identical inputs hit.",
    summary=(
        "MD5, used to key a cache. Not a password, not a signature, not integrity "
        "against an adversary. Flagging it is the reflex this case exists to catch."
    ),
    # Not a pure negative control: the reflex it tests is wrong, but other
    # legitimate observations remain available, so the gate outcome is open.
    expected_decision=None,
    before={
        "render/cache.py": '''_CACHE = {}


def cached_render(template_source, context):
    """Render `template_source` with `context`, memoised on both."""
    key = (template_source, tuple(sorted(context.items())))
    if key not in _CACHE:
        _CACHE[key] = _render(template_source, context)
    return _CACHE[key]
''',
        "tests/test_cache.py": """def test_identical_input_renders_once(counter):
    cached_render("{{ a }}", {"a": 1})
    cached_render("{{ a }}", {"a": 1})
    assert counter.renders == 1
""",
    },
    after={
        "render/cache.py": '''import hashlib
import json

_CACHE = {}


def cached_render(template_source, context):
    """Render `template_source` with `context`, memoised on a digest of both."""
    #!CLEAN agent=security :: MD5 over the template's own source and a canonical encoding of the context; no secret, no adversary, and a collision costs one re-render
    #!ALLOW agent=correctness category=logic|api_contract|error_handling :: json.dumps narrows accepted context values from hashable to JSON-serialisable, and non-string keys either collide under coercion or raise under sort_keys
    payload = json.dumps([template_source, context], sort_keys=True)
    key = hashlib.md5(payload.encode()).hexdigest()
    if key not in _CACHE:
        _CACHE[key] = _render(template_source, context)
    return _CACHE[key]
''',
        "tests/test_cache.py": """def test_identical_input_renders_once(counter):
    cached_render("{{ a }}", {"a": 1})
    cached_render("{{ a }}", {"a": 1})
    assert counter.renders == 1


def test_key_is_by_value_not_by_identity(counter):
    cached_render("{{ a }}", {"a": 1, "b": 2})
    cached_render("{{ a }}", {"b": 2, "a": 1})
    assert counter.renders == 1


def test_different_context_renders_again(counter):
    cached_render("{{ a }}", {"a": 1})
    cached_render("{{ a }}", {"a": 2})
    assert counter.renders == 2
""",
    },
)


# --- the severity boundary, from both sides --------------------------------
#
# ADR-0006 widened `critical` to cover a control removed, weakened or bypassed.
# A widened band needs cases on both sides of it, or the set only rewards
# rating things higher.

case(
    id="sec-removed-authz-check",
    title="Speed up the bulk export endpoint",
    summary="An ownership check deleted for performance. Critical: the control is gone.",
    body="Speeds up bulk export by dropping the per-object ownership loop — callers are already authenticated.",
    expected_decision="escalate",
    context={
        "api/auth.py": '''def require_owner(user, obj):
    """Raise Forbidden unless `user` owns `obj`. Every object read or mutation
    of another tenant's data must go through this."""
    if obj.tenant_id != user.tenant_id:
        raise Forbidden()
''',
    },
    before={
        "api/export.py": """from api.auth import current_user, require_owner


def bulk_export(request, report_ids):
    user = current_user(request)
    reports = [Report.get(rid) for rid in report_ids]
    for report in reports:
        require_owner(user, report)
    return [r.to_dict() for r in reports]
""",
    },
    after={
        "api/export.py": """from api.auth import current_user


def bulk_export(request, report_ids):
    user = current_user(request)
    #!EXPECT agent=security category=authz severity>=critical :: the per-object require_owner loop was deleted, so any authenticated caller can read another tenant's reports by id
    #!ALLOW agent=correctness category=error_handling :: an unknown report id makes Report.get return None, which reaches to_dict and raises. Worth noticing, but NOT introduced here — before this diff the same None hit obj.tenant_id inside require_owner and raised there. The failure moves lines; the outcome does not change.
    reports = [Report.get(rid) for rid in report_ids]
    return [r.to_dict() for r in reports]
""",
    },
)

case(
    id="sec-missing-rate-limit",
    title="Drop the limiter from the password reset endpoint",
    body="Removes the per-hour limiter from password reset; the email provider already throttles.",
    summary=(
        "A rate limit removed. Real, and `major` rather than `critical`: it makes "
        "an attack cheaper without granting anyone access."
    ),
    expected_decision=None,
    before={
        "api/password_reset.py": """from limits import rate_limit


@rate_limit("5/hour")
def request_reset(request, email):
    send_reset_email(email)
    return {"sent": True}
""",
    },
    after={
        "api/password_reset.py": """#!EXPECT agent=security category=input_validation severity>=minor :: the limiter was removed, so reset emails can be sent in unlimited volume to any address; it makes enumeration and mail-bombing cheap without granting access
#!ALLOW agent=correctness category=logic :: same observation under correctness — every call now reaches send_reset_email with no per-caller bound
def request_reset(request, email):
    send_reset_email(email)
    return {"sent": True}
""",
    },
)

case(
    id="sec-verbose-error-leak",
    title="Return the exception text to help debugging",
    summary="Internals leaked to the client. Real, and `major`, not `critical`.",
    body="Returns the exception text on 500s so on-call can see what failed without SSHing in.",
    expected_decision=None,
    before={
        "api/errors.py": """import logging

log = logging.getLogger(__name__)


def handle(exc):
    log.exception("request failed")
    return {"error": "internal error"}, 500
""",
    },
    after={
        "api/errors.py": """import logging
import traceback

log = logging.getLogger(__name__)


def handle(exc):
    log.exception("request failed")
    #!EXPECT agent=security category=input_validation|error_handling severity>=minor :: the traceback goes to the caller, exposing file paths, library versions and internal structure; it helps an attacker without itself granting anything
    return {"error": str(exc), "traceback": traceback.format_exc()}, 500
""",
    },
)


# --- other languages, and the shapes that only exist in them ----------------

case(
    id="sec-workflow-pull-request-target",
    title="Run the size check on forked pull requests",
    body="Run the size check on forked pull requests so external contributors get the same CI signal.",
    summary=(
        "GitHub Actions. `pull_request_target` runs with repository secrets and "
        "this checks out the fork's code before running it."
    ),
    expected_decision="escalate",
    before={
        ".github/workflows/size.yml": """name: size

on:
  pull_request:

jobs:
  measure:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: ./scripts/measure.sh
""",
    },
    after={
        ".github/workflows/size.yml": """name: size

on:
  #!EXPECT agent=security category=authz severity>=critical :: pull_request_target runs with the base repository's secrets, and checking out the fork's head then running its script hands those secrets to anyone who opens a pull request
  #!ALLOW agent=correctness category=logic :: under pull_request_target the workflow file comes from the base branch, so the definition is split from the code it measures. The change is NOT redundant, though: this diff also adds NPM_TOKEN, and plain pull_request does not expose repository secrets to a fork's pull request — which is why the swap is dangerous rather than merely unnecessary.
  pull_request_target:

jobs:
  measure:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          #!ALLOW agent=correctness category=logic :: pinning ref to the PR head measures the fork tip rather than refs/pull/N/merge, so the reported size is no longer the merge result
          ref: ${{ github.event.pull_request.head.sha }}
      - run: ./scripts/measure.sh
        env:
          NPM_TOKEN: ${{ secrets.NPM_TOKEN }}
""",
    },
)

case(
    id="cor-go-shadowed-error",
    title="Add the settlement writer",
    summary="Go. An error shadowed inside an if-scope, so the failure is dropped.",
    body="Settlement writer for the end-of-day batch. Logs and continues on individual row failures.",
    expected_decision=None,
    before={
        "internal/ledger/write.go": """package ledger

func WriteEntry(db *sql.DB, e Entry) error {
	if err := validate(e); err != nil {
		return err
	}
	_, err := db.Exec("INSERT INTO ledger (id, cents) VALUES ($1, $2)", e.ID, e.Cents)
	return err
}
""",
    },
    after={
        "internal/ledger/write.go": """package ledger

func WriteEntry(db *sql.DB, e Entry) error {
	if err := validate(e); err != nil {
		return err
	}
	_, err := db.Exec("INSERT INTO ledger (id, cents) VALUES ($1, $2)", e.ID, e.Cents)
	return err
}

func WriteSettlement(db *sql.DB, s Settlement) error {
	if err := validate(s.Entry); err != nil {
		return err
	}
	//!EXPECT agent=correctness category=error_handling severity>=major :: := declares a new err inside the if-scope, so the outer err stays nil and a failed insert is reported as success
	if _, err := db.Exec("INSERT INTO settlements (id, cents) VALUES ($1, $2)", s.ID, s.Cents); err != nil {
		log.Printf("settlement insert failed: %v", err)
	}
	var err error
	return err
}
""",
    },
)


# --- deletions, and a defect buried in noise -------------------------------

case(
    id="cor-removed-null-guard",
    title="Tidy up the invoice renderer",
    summary="A guard removed during cleanup, on a field that is genuinely optional.",
    body="Tidies the invoice renderer — drops a redundant nil check that the type already covers.",
    expected_decision=None,
    context={
        "models/invoice.py": '''class Invoice:
    """`purchase_order` is optional: only set for customers on invoicing terms,
    which is roughly one account in twenty."""

    purchase_order: "str | None"
''',
    },
    before={
        "render/invoice.py": """def header_lines(invoice):
    lines = [f"Invoice {invoice.number}"]
    if invoice.purchase_order is not None:
        lines.append(f"PO {invoice.purchase_order.upper()}")
    return lines
""",
    },
    after={
        "render/invoice.py": """def header_lines(invoice):
    #!EXPECT agent=correctness category=logic severity>=major :: the None guard was removed, but purchase_order is unset for most accounts, so this raises AttributeError on them
    return [
        f"Invoice {invoice.number}",
        f"PO {invoice.purchase_order.upper()}",
    ]
""",
    },
)

case(
    id="tst-deleted-test-with-fix",
    title="Fix the rounding and drop the failing test",
    summary="The test that would have caught the change is deleted in the same commit.",
    body="Fix the rounding and drop the failing test that was asserting the old behaviour.",
    expected_decision=None,
    before={
        "tests/test_rounding.py": """from decimal import Decimal


def test_rounds_half_to_even():
    assert round_cents(Decimal("2.345")) == Decimal("2.34")


def test_rounds_up_above_half():
    assert round_cents(Decimal("2.346")) == Decimal("2.35")
""",
    },
    after={
        "tests/test_rounding.py": """from decimal import Decimal


#!EXPECT agent=tests category=test_coverage severity>=major :: test_rounds_half_to_even was deleted rather than updated, so the banker's-rounding behaviour it pinned is now unguarded
#!ALLOW agent=correctness category=logic|api_contract :: the title claims a rounding fix but only the test file changed, so no behaviour changed at all — what the deletion removes is the guarantee that half-to-even holds. round_cents itself is not in the diff, so whether callers depend on it cannot be read from here.
def test_rounds_up_above_half():
    assert round_cents(Decimal("2.346")) == Decimal("2.35")
""",
    },
)


# --- a defect buried in mechanical churn -----------------------------------
#
# Generated rather than typed out, because the point is the volume: forty
# functions renamed, and one of them quietly changed as well. Every other case in
# the set is small enough to read in one pass, which is not the review where
# things get missed.

_RENAME_FIELDS = [
    "subtotal",
    "discount",
    "tax",
    "shipping",
    "handling",
    "credit",
    "rebate",
    "surcharge",
    "adjustment",
    "rounding",
    "deposit",
    "refund",
    "chargeback",
    "settlement",
    "payout",
    "fee",
    "commission",
    "levy",
    "duty",
    "tariff",
]


def _rename_module(prefix: str, threshold_op: str) -> str:
    lines = ['"""Line-item calculators. One function per component."""', "", ""]
    for field in _RENAME_FIELDS:
        lines.append(f"def {prefix}_{field}(line):")
        if field == "rounding":
            # The one function whose body changes as well as its name.
            if threshold_op == "MARK":
                lines.append(
                    "    #!EXPECT agent=correctness category=logic severity>=major "
                    ":: the comparison flipped from >= to > inside a forty-function "
                    "rename, so an amount of exactly half a cent now rounds down "
                    "where it used to round up"
                )
                op = ">"
            else:
                op = threshold_op
            lines.append(f"    return 1 if line.remainder_cents {op} 0.5 else 0")
        else:
            lines.append(f"    return line.{field}_cents")
        lines.extend(["", ""])
    return "\n".join(lines).rstrip("\n") + "\n"


case(
    id="cor-needle-in-a-rename",
    title="Rename the line-item calculators",
    body="Renames calc_* helpers to compute_* across the line-item package for consistency.",
    summary=(
        "Forty functions renamed from calc_* to compute_*, and one comparison "
        "flipped along the way. The review where a defect actually gets missed."
    ),
    expected_decision=None,
    before={"billing/lines.py": _rename_module("calc", ">=")},
    after={"billing/lines.py": _rename_module("compute", "MARK")},
)


# ===========================================================================
# Fourth tranche — mined from real merged pull requests.
#
# Every case above this line was written by me, which means I knew where the
# defect was before the reviewer did, and the set kept saturating because of it.
# These are different: each one is a defect that a real maintainer found in real
# code and merged a fix for.
#
# They are built by *inverting* the fix — the change under review is the one that
# puts the bug back. That is not the same as the pull request which originally
# introduced it, and the labels come from what the fix did rather than from what
# a reviewer said at the time. What they do give is a defect whose existence was
# judged by somebody other than me.
#
# The excerpts are short passages from permissively licensed projects, and each
# case records its repository, pull request and licence. `scripts/mine_cases.py`
# finds candidates; choosing and labelling them is by hand, because "the commit
# message says fix" is not the same as "here is the defect and here is where".
# ===========================================================================

case(
    id="real-tornado-cookie-none-guard",
    title="Simplify get_cookie",
    body="Simplify get_cookie — cookies is always a mapping after construction.",
    summary=(
        "Drops the None check on request.cookies. Inverted from the fix for a "
        "crash on a malformed Cookie header."
    ),
    provenance="tornadoweb/tornado#397 (Apache-2.0) — 'Invalid Cookie header crashes get_cookie'",
    expected_decision=None,
    context={
        "tornado/httpserver.py": '''    @property
    def cookies(self):
        """A dictionary of Cookie.Morsel objects.

        Set to None when the Cookie header is present but cannot be parsed, so
        every consumer has to allow for that.
        """
        if self._cookies is None:
            return None
        return self._cookies
''',
    },
    before={
        "tornado/web.py": '''    def get_cookie(self, name, default=None):
        """Gets the value of the cookie with the given name, else default."""
        if self.request.cookies is not None and name in self.request.cookies:
            return self.request.cookies[name].value
        return default
''',
    },
    after={
        "tornado/web.py": '''    def get_cookie(self, name, default=None):
        """Gets the value of the cookie with the given name, else default."""
        #!EXPECT agent=correctness category=logic|input_validation severity>=major :: request.cookies is None when the Cookie header cannot be parsed, so a malformed header turns this lookup into a TypeError instead of returning the default
        if name in self.request.cookies:
            return self.request.cookies[name].value
        return default
''',
    },
)

case(
    id="real-tornado-multipart-boundary",
    title="Tidy the multipart content-type parsing",
    body="Tidy multipart Content-Type parsing; drop a redundant strip on the parameter value.",
    summary=(
        "Drops a .strip() while parsing content-type parameters. Inverted from "
        "the fix for multipart/form-data bodies silently not being parsed."
    ),
    provenance="tornadoweb/tornado#177 (Apache-2.0) — 'Fix for multipart/form-data requests'",
    expected_decision=None,
    before={
        "tornado/httpserver.py": """            elif content_type.startswith("multipart/form-data"):
                fields = content_type.split(";")
                for field in fields:
                    k, sep, v = field.strip().partition("=")
                    if k == "boundary" and v:
                        self._parse_mime_body(v, data)
                        break
""",
    },
    after={
        "tornado/httpserver.py": """            elif content_type.startswith("multipart/form-data"):
                fields = content_type.split(";")
                for field in fields:
                    #!EXPECT agent=correctness category=logic|input_validation severity>=major :: content-type parameters are separated by "; ", so every field after the first keeps a leading space and never equals "boundary"; the body is then silently not parsed
                    k, sep, v = field.partition("=")
                    if k == "boundary" and v:
                        self._parse_mime_body(v, data)
                        break
""",
    },
)

case(
    id="real-urllib3-format-placeholder",
    title="Shorten the parse error message",
    body="Shorten the LocationParseError message; the detail is already in the exception type.",
    summary=(
        "A format placeholder left without its argument. Inverted from the fix "
        "that added the url to the message."
    ),
    provenance="urllib3/urllib3#64 (MIT) — 'Added url to LocationParseError message'",
    expected_decision=None,
    before={
        "urllib3/util.py": '''def get_host(url):
    """Given a url, return its scheme, host and port (None if default)."""
    if ':' in url:
        url, port = url.split(':', 1)

        if not port.isdigit():
            raise LocationParseError("Failed to parse: %s" % url)

        port = int(port)

    return url, port
''',
    },
    after={
        "urllib3/util.py": '''def get_host(url):
    """Given a url, return its scheme, host and port (None if default)."""
    if ':' in url:
        url, port = url.split(':', 1)

        if not port.isdigit():
            #!EXPECT agent=correctness category=logic severity>=minor :: the %s placeholder has no argument, so the error reads literally "Failed to parse: %s" and tells whoever is debugging nothing
            raise LocationParseError("Failed to parse: %s")

        port = int(port)

    return url, port
''',
    },
)

case(
    id="real-aiohttp-stream-single-wait",
    title="Simplify the stream read wait",
    body="Simplify the stream read wait — one wake is enough when the waiter is set.",
    summary=(
        "A while loop turned into a single if. Inverted from the fix for reads "
        "returning empty when the waiter is woken without data."
    ),
    provenance="aio-libs/aiohttp#3527 (Apache-2.0) — 'Fix stream .read() / .readany() / .iter_any()'",
    expected_decision=None,
    context={
        "aiohttp/streams.py": '''    async def _wait(self, func_name: str) -> None:
        """Wake when the feeder calls feed_data() OR feed_eof().

        The waiter is also resolved at the end of a chunk, which can happen with
        no new data in the buffer, so a caller must re-check its condition after
        waking rather than assuming data arrived.
        """
        waiter = self._waiter = self._loop.create_future()
        await waiter
''',
    },
    before={
        "aiohttp/streams.py": """    async def readany(self) -> bytes:
        if self._exception is not None:
            raise self._exception

        while not self._buffer and not self._eof:
            await self._wait('readany')

        return self._read_nowait(-1)
""",
    },
    after={
        "aiohttp/streams.py": """    async def readany(self) -> bytes:
        if self._exception is not None:
            raise self._exception

        #!EXPECT agent=correctness category=concurrency severity>=major :: the waiter also resolves at the end of a chunk with nothing added to the buffer, so a single if returns an empty read instead of waiting again
        if not self._buffer and not self._eof:
            await self._wait('readany')

        return self._read_nowait(-1)
""",
    },
)

case(
    id="real-aiohttp-location-attribute-type",
    title="Assign the parsed location once",
    body="Assign the parsed Location once and reuse it for the header.",
    summary=(
        "A public attribute changes from the string it was given to a URL "
        "object. Inverted from the fix that put it back."
    ),
    provenance="aio-libs/aiohttp#3615 (Apache-2.0) — 'Fix backport of redirect URL fix to 3.5'",
    expected_decision=None,
    context={
        "docs/web_reference.rst": """.. attribute:: HTTPMove.location

   The location the response redirects to, **as the string it was constructed
   with**. Application code compares it against configured strings and passes it
   to ``str.startswith``.
""",
    },
    before={
        "aiohttp/web_exceptions.py": """class _HTTPMove(HTTPRedirection):
    def __init__(self, location, *, headers=None, reason=None,
                 body=None, text=None, content_type=None):
        if not location:
            raise ValueError("HTTP redirects need a location to redirect to.")
        super().__init__(headers=headers, reason=reason,
                         body=body, text=text, content_type=content_type)
        self.headers['Location'] = str(URL(location))
        self.location = location
""",
    },
    after={
        "aiohttp/web_exceptions.py": """class _HTTPMove(HTTPRedirection):
    def __init__(self, location, *, headers=None, reason=None,
                 body=None, text=None, content_type=None):
        if not location:
            raise ValueError("HTTP redirects need a location to redirect to.")
        super().__init__(headers=headers, reason=reason,
                         body=body, text=text, content_type=content_type)
        #!EXPECT agent=correctness category=api_contract severity>=minor :: location is documented as the string it was constructed with; handing callers a URL object instead breaks startswith and any equality check against a configured string
        #!ALLOW agent=security category=authz :: the same type change silently breaks redirect allowlist checks that call str.startswith or compare against configured strings
        self.location = URL(location)
        self.headers['Location'] = str(self.location)
""",
    },
)

case(
    id="real-requests-implicit-relative-import",
    title="Drop the leading dot from the adapters import",
    body="Drop the leading dot from the adapters import; same module, cleaner form.",
    summary=("An implicit relative import. Inverted from the fix that made it explicit."),
    provenance="psf/requests#1011 (Apache-2.0) — 'Fixed relative import'",
    expected_decision=None,
    before={
        "requests/sessions.py": '''from .compat import cookielib, OrderedDict, urljoin, urlparse
from .cookies import cookiejar_from_dict
from .models import Request
from .hooks import default_hooks, dispatch_hook
from .utils import from_key_val_list, default_headers
from .packages.urllib3.poolmanager import PoolManager


from .adapters import HTTPAdapter


def merge_kwargs(local_kwarg, default_kwarg):
    """Merges kwarg dictionaries."""
''',
    },
    after={
        "requests/sessions.py": '''from .compat import cookielib, OrderedDict, urljoin, urlparse
from .cookies import cookiejar_from_dict
from .models import Request
from .hooks import default_hooks, dispatch_hook
from .utils import from_key_val_list, default_headers
from .packages.urllib3.poolmanager import PoolManager


#!ALLOW agent=security category=input_validation|other :: an unqualified import also resolves against sys.path, so any top-level module named adapters shadows the intended one
#!EXPECT agent=correctness category=logic severity>=major :: implicit relative imports were removed in Python 3, so this raises ImportError there while every other import in the module uses the explicit form
from adapters import HTTPAdapter


def merge_kwargs(local_kwarg, default_kwarg):
    """Merges kwarg dictionaries."""
''',
    },
)


# ===========================================================================
# Fifth tranche — mined from published security advisories.
#
# The previous tranche mined ordinary merged fix commits and produced six good
# cases without a single security defect among them, because small fixes skew
# heavily towards correctness. Advisories are the other end of that: every one is
# a security defect somebody took seriously enough to publish, most link to the
# commit that fixed it, and the CWE is an independent label for the class.
#
# Same construction and the same caveat as the tranche above: the change under
# review is the inverted fix, so it reintroduces the vulnerability rather than
# being the commit that originally shipped it. `scripts/mine_advisories.py` finds
# candidates; choosing and labelling them is by hand.
#
# Every one of these is public, fixed, and released. They are here because a
# reviewer that cannot catch a CWE-79 or a CWE-285 is not much of a security
# reviewer, and nothing in the hand-written set was testing either.
# ===========================================================================

case(
    id="cve-jupyter-referer-token-log",
    title="Log the request headers on a 5xx",
    body="Log Host/Accept/Referer/User-Agent on 5xx so we can see what the client sent.",
    summary=(
        "Logs the Referer verbatim. Inverted from the fix for a token-bearing "
        "Referer being written to the log."
    ),
    provenance="GHSA-c3mw-737p-c7g2 (CWE-532, high) jupyter-server/jupyter_server, BSD-3-Clause",
    expected_decision="escalate",
    context={
        "jupyter_server/auth/identity.py": '''def token_authenticated_url(base, token):
    """Build a URL carrying the auth token as a query parameter.

    Notebook URLs are handed out in this form, which means the browser sends the
    token back in the Referer header on every subresource request.
    """
    return f"{base}?token={token}"
''',
    },
    before={
        "jupyter_server/log.py": """    if status >= 500:
        headers = {}
        for header in ["Host", "Accept", "Referer", "User-Agent"]:
            if header in request.headers:
                value = request.headers[header]
                if header == "Referer":
                    value = _scrub_uri(value, extra_param_keys)
                headers[header] = value
        log_method(json.dumps(headers, indent=2))
""",
    },
    after={
        "jupyter_server/log.py": """    if status >= 500:
        headers = {}
        for header in ["Host", "Accept", "Referer", "User-Agent"]:
            if header in request.headers:
                #!EXPECT agent=security category=secrets severity>=major :: notebook URLs carry the auth token as a query parameter, so the browser puts it in the Referer and this writes a live credential into the log on every 5xx
                #!ALLOW agent=correctness category=logic :: same defect under a correctness reading — Referer is logged unscrubbed
                #!ALLOW agent=correctness category=api_contract :: the deleted _scrub_uri call was the only use of extra_param_keys in this fragment, leaving it unread. Whether callers still pass it cannot be told from this diff — no enclosing signature is in view — so the dead-parameter reading is plausible, not established.
                headers[header] = request.headers[header]
        log_method(json.dumps(headers, indent=2))
""",
    },
)

case(
    id="cve-scrapy-s3-plaintext-default",
    title="Simplify the S3 scheme selection",
    body="Simplify S3 scheme selection to a single ternary on is_secure.",
    summary=(
        "A scheme that defaults to plaintext when the flag is absent. Inverted "
        "from the fix for signed S3 requests going over HTTP."
    ),
    provenance="GHSA-76g3-c3x4-crvx (CWE-319, high) scrapy/scrapy, BSD-3-Clause",
    expected_decision="escalate",
    before={
        "scrapy/core/downloader/handlers/s3.py": """    async def download_request(self, request: Request) -> Response:
        p = urlparse_cached(request)
        scheme = "http" if request.meta.get("is_secure") is False else "https"
        bucket = p.hostname
        path = p.path + "?" + p.query if p.query else p.path
        url = f"{scheme}://{bucket}.s3.amazonaws.com{path}"
""",
    },
    after={
        "scrapy/core/downloader/handlers/s3.py": """    async def download_request(self, request: Request) -> Response:
        p = urlparse_cached(request)
        #!EXPECT agent=security category=crypto|input_validation severity>=major :: is_secure is absent on almost every request, so this selects plaintext by default and the AWS signature and payload travel unencrypted
        #!ALLOW agent=correctness category=logic :: same defect under a correctness reading — the default flips to plaintext http whenever is_secure is unset
        scheme = "https" if request.meta.get("is_secure") else "http"
        bucket = p.hostname
        path = p.path + "?" + p.query if p.query else p.path
        url = f"{scheme}://{bucket}.s3.amazonaws.com{path}"
""",
    },
)

case(
    id="cve-mdc-xss-xlink-href",
    title="Trim the sanitiser's attribute list",
    body="Trim the sanitiser attribute list — drop rarely used SVG attributes we do not need.",
    summary=(
        "Drops xlinkhref from the sanitised attributes. Inverted from the fix "
        "for XSS through SVG xlink:href in untrusted markdown."
    ),
    provenance="GHSA-mxm6-v9r6-r94c (CWE-79, high) nuxt-content/mdc, MIT",
    expected_decision="escalate",
    context={
        "src/runtime/parser/utils/props.ts": """// Rendered attributes are taken from author-supplied markdown, which in this
// product includes content submitted by end users. `validateProp` is the only
// thing standing between that markdown and the DOM.
export const unsafeLinkPrefix = ['javascript:', 'data:text/html', 'vbscript:']
""",
    },
    before={
        "src/runtime/parser/utils/props.ts": """export const validateProp = (attribute: string, value: string) => {
  if (typeof value !== 'string') {
    return false
  }

  if (attribute === 'href' || attribute === 'src' || attribute === 'xlinkhref') {
    return isAnchorLinkAllowed(value)
  }

  return true
}
""",
    },
    after={
        "src/runtime/parser/utils/props.ts": """export const validateProp = (attribute: string, value: string) => {
  if (typeof value !== 'string') {
    return false
  }

  //!EXPECT agent=security category=injection severity>=major :: SVG xlink:href executes script exactly as href does, so dropping it from the sanitised list lets attacker-supplied markdown run javascript: in the page
  if (attribute === 'href' || attribute === 'src') {
    return isAnchorLinkAllowed(value)
  }

  return true
}
""",
    },
)

case(
    id="cve-zot-delete-maps-to-push",
    title="Collapse the method-to-action mapping",
    body="Collapse method-to-action mapping: reads pull, everything else push.",
    summary=(
        "Every non-read method maps to push. Inverted from the fix for a push "
        "token being accepted for DELETE."
    ),
    provenance="GHSA-qg67-7m6v-qg25 (CWE-285, high) project-zot/zot, Apache-2.0",
    expected_decision="escalate",
    context={
        "pkg/api/config/config.go": """// Access is granted per repository and per action. The three actions are
// distinct on purpose: an account may be allowed to push new tags without being
// allowed to delete existing ones.
//
//     ActionPull   = "pull"
//     ActionPush   = "push"
//     ActionDelete = "delete"
""",
    },
    before={
        "pkg/api/authn.go": """            vars := mux.Vars(request)
            name := vars["name"]

            var action string
            switch m := request.Method; m {
            case http.MethodHead, http.MethodGet:
                action = "pull"
            case http.MethodPost, http.MethodPatch, http.MethodPut:
                action = "push"
            case http.MethodDelete:
                action = "delete"
            default:
                action = "pull"
            }

            requestedAccess = &ResourceAction{Name: name, Action: action}
""",
    },
    after={
        "pkg/api/authn.go": """            vars := mux.Vars(request)
            name := vars["name"]

            //!EXPECT agent=security category=authz severity>=critical :: DELETE falls into the push branch, so a token granting only push authorises deletion; push and delete are separate actions precisely because they are not the same permission
            //!ALLOW agent=correctness category=logic :: the old default mapped unknown methods (OPTIONS, TRACE) to pull; the new condition sends them down the push branch
            action := "pull"
            if m := request.Method; m != http.MethodGet && m != http.MethodHead {
                action = "push"
            }

            requestedAccess = &ResourceAction{Name: name, Action: action}
""",
    },
)

case(
    id="cve-perses-unvalidated-project-path",
    title="Skip the project name validation",
    body="Skip the project-name identifier check; the path join already scopes under the folder root.",
    summary=(
        "Drops the identifier check on a query parameter that becomes a "
        "filesystem path. Inverted from the fix for path traversal."
    ),
    provenance="GHSA-vr5f-w35q-98jp (CWE-22, high) perses/perses, Apache-2.0",
    expected_decision="escalate",
    context={
        "internal/api/database/file/file.go": """// buildPath joins the storage root with the project and resource names to reach
// the document on disk. It does no validation of its own: callers are required
// to have validated the names before this point.
func (d *DAO) buildPath(project string, kind string) string {
    return filepath.Join(d.Folder, project, kind)
}
""",
    },
    before={
        "internal/api/toolbox/list.go": """func (t *toolbox[T, K, V]) list(ctx echo.Context, parameters apiInterface.Parameters, query V) (any, error) {
    projectQueryParameter := query.GetProjectQueryParam()
    if len(projectQueryParameter) > 0 {
        if err := common.ValidateID(projectQueryParameter); err != nil {
            return nil, apiInterface.HandleBadRequestError("the project name in the query parameter is invalid")
        }
        if len(parameters.Project) > 0 && parameters.Project != projectQueryParameter {
            return nil, apiInterface.HandleBadRequestError("the project name in the path and the query differ")
        }
        parameters.Project = projectQueryParameter
    }
    return t.service.List(ctx, query, parameters)
}
""",
    },
    after={
        "internal/api/toolbox/list.go": """func (t *toolbox[T, K, V]) list(ctx echo.Context, parameters apiInterface.Parameters, query V) (any, error) {
    projectQueryParameter := query.GetProjectQueryParam()
    if len(projectQueryParameter) > 0 {
        //!EXPECT agent=security category=injection severity>=critical :: the project name reaches filepath.Join unvalidated, and buildPath documents that callers must validate it, so ../ in the query parameter escapes the storage root
        if len(parameters.Project) > 0 && parameters.Project != projectQueryParameter {
            return nil, apiInterface.HandleBadRequestError("the project name in the path and the query differ")
        }
        parameters.Project = projectQueryParameter
    }
    return t.service.List(ctx, query, parameters)
}
""",
    },
)

case(
    id="cve-rclone-declared-length-allocation",
    title="Pre-reserve the multipart buffer",
    body="Pre-reserve the multipart buffer from Content-Length so the MD5 pass does not grow page by page.",
    summary=(
        "Allocates from a client-declared length before reading the body. "
        "Inverted from the fix for memory exhaustion."
    ),
    provenance="GHSA-2p48-j3qc-rx9f (CWE-789, high) rclone/rclone, MIT",
    expected_decision=None,
    before={
        "cmd/serve/s3/multipart.go": """func (b *s3Backend) UploadPart(ctx context.Context, bucketName, objectName string,
    partNumber int, contentLength int64, body io.Reader) (string, error) {

    // Buffer the part in a pool-backed RW so we can MD5 it (for the ETag) and
    // stream it once it is this part's turn. The RW grows a page at a time as
    // the body is read.
    rw := multipart.NewRW()
    hasher := md5.New()
    n, err := io.Copy(rw, io.TeeReader(body, hasher))
""",
    },
    after={
        "cmd/serve/s3/multipart.go": """func (b *s3Backend) UploadPart(ctx context.Context, bucketName, objectName string,
    partNumber int, contentLength int64, body io.Reader) (string, error) {

    // Buffer the part in a pool-backed RW so we can MD5 it (for the ETag) and
    // stream it once it is this part's turn.
    //!EXPECT agent=security category=input_validation severity>=major :: contentLength is the value the client declared, so an unauthenticated request announcing a huge part reserves that much memory before a single byte of body is read
    //!ALLOW agent=correctness category=logic|error_handling :: chunked or unknown-length bodies yield contentLength=-1, which reaches Reserve unguarded
    rw := multipart.NewRW().Reserve(contentLength)
    hasher := md5.New()
    n, err := io.Copy(rw, io.TeeReader(body, hasher))
""",
    },
)


# ---------------------------------------------------------------------------
# Tranche 6 — the introducing commits
#
# Every case above that carries a real provenance is an *inverted fix*: I took
# the commit that repaired a defect and ran it backwards. That is convenient and
# it is not what a reviewer sees. The fix knows where the bug is — its diff is
# centred on the defective line, and inverting it produces a small, suspiciously
# well-aimed patch.
#
# These three are the real commits that introduced three of those same defects,
# found by walking back from each fix with `git log -S` on the defective line
# (`git blame` lands on later reformatting commits). The files are the upstream
# files at that commit, unedited except for the label markers, and they live in
# tests/eval/sources/ because they are far too long to sit inline here.
#
# They are deliberately paired with the inverted-fix versions of the same bugs —
# real-tornado-multipart-boundary, real-urllib3-format-placeholder and
# real-requests-implicit-relative-import. Same defect, two framings. A reviewer that
# scores well on the inversion and poorly on the real commit is being flattered
# by the inversion, and the pair is what makes that visible.
# ---------------------------------------------------------------------------

case(
    id="intro-tornado-multipart",
    title="Don't assume 'boundary' is last field in Content-Type header",
    body="Don't assume 'boundary' is the last field in the Content-Type header — scan all parameters.",
    summary=(
        "The real commit, unedited. It is itself a bug fix — it stops assuming "
        "boundary comes last — and it introduced a new bug that took three days "
        "to find. 493-line file, one changed hunk."
    ),
    provenance=(
        "tornadoweb/tornado 9e965556 (Apache-2.0, 2010-11-19) — the commit that "
        "introduced the defect fixed by 263994e8 three days later. Paired with "
        "real-tornado-multipart-boundary, which is 263994e8 inverted."
    ),
    expected_decision=None,
    source_dir="intro-tornado-multipart",
)

case(
    id="intro-urllib3-util-refactor",
    title="Refactor helpers into util.py",
    body="Lift shared helpers into util.py so callers have one place to import from.",
    summary=(
        "The real commit, unedited: a new 125-line module lifted out of several "
        "others. One line in it carries a format placeholder with no argument. "
        "Nothing in the diff points at it — this is the needle case."
    ),
    provenance=(
        "urllib3/urllib3 d8ff66d0 (MIT, 2012-02-05) — introduced the defect fixed "
        "by de20783d seven weeks later. Paired with real-urllib3-format-placeholder."
    ),
    expected_decision=None,
    source_dir="intro-urllib3-util-refactor",
)

case(
    id="intro-requests-poolmanager",
    title="WHOOOOOOOOOOOOOOOO",
    body="""WHOOOOOOOOOOOOOOOO

Mid-refactor: move sending onto an HTTPAdapter and stop owning the PoolManager on Session.""",
    summary=(
        "The real commit, unedited, commit message and all: mid-refactor work in "
        "progress that moves sending onto an adapter. The bad import is one line "
        "among six hunks, two of which are legitimate findings in their own right."
    ),
    provenance=(
        "psf/requests 92d57036 (Apache-2.0, 2012-12-15) — introduced the defect "
        "fixed by ed360dca the same day. Paired with real-requests-implicit-relative-import."
    ),
    expected_decision=None,
    source_dir="intro-requests-poolmanager",
)


case(
    id="intro-tornado-cookies-move",
    title="Move 'cookies' property from RequestHandler to HTTPRequest",
    body="Move the cookies property from RequestHandler onto HTTPRequest so request-level code can read them directly.",
    summary=(
        "The real commit, unedited, and the hardest of the four: a two-file move. "
        "The defective line is in httpserver.py, the code it breaks is in web.py, "
        "and the commit touches both — but the broken caller is not itself changed, "
        "so it never appears in the diff."
    ),
    provenance=(
        "tornadoweb/tornado 4a4d8717 (Apache-2.0, 2011-08-18) — introduced the "
        "defect fixed by f0df94ca ('fix crash on invalid Cookie header') nearly "
        "three months later. Paired with real-tornado-cookie-none-guard."
    ),
    expected_decision=None,
    source_dir="intro-tornado-cookies-move",
)
