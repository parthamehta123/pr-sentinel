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
    #!EXPECT agent=security category=injection severity>=critical :: joins the caller's name with no realpath and no confinement check, so ../ escapes EXPORT_ROOT
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
    return expected == header
""",
    },
)

case(
    id="sec-jwt-unverified",
    title="Read the tenant id straight from the token",
    summary="Signature verification disabled to avoid a key lookup.",
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
    #!EXPECT agent=security category=authz severity>=critical :: signature verification disabled, so any caller can forge a tenant id and the value is then trusted
    unverified = jwt.decode(token, options={"verify_signature": False})
    return unverified["tenant_id"]
""",
    },
)

case(
    id="sec-terraform-public-bucket",
    title="Add the public assets bucket",
    summary="Terraform, not Python. One bucket is deliberately public; the next is not meant to be.",
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
    response = requests.get(f"https://billing.internal/limits/{tenant_id}", timeout=5)
    return response.json()
""",
    },
)

case(
    id="cor-mutable-default-arg",
    title="Add batch helpers to the notifier",
    summary="A mutable default argument next to a correct None default.",
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
        "api/listing.py": '''def list_invoices(tenant_id, limit=50, cursor=None):
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
  switch (event.kind) {
    case "click":
      return `click at ${event.x}, ${event.y}`;
    case "key":
      return `key ${event.code}`;
  }
}
""",
    },
)

case(
    id="neg-dependency-bump",
    title="Bump httpx and record it",
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
    if len(_CACHE) > 1000:
        _CACHE.clear()
    return tenant
""",
    },
)
