"""Intentionally flawed code, used to demonstrate what pr-sentinel flags.

This file is not imported by anything and is not part of the system. It exists so
that the open demonstration pull request in this repository has something real to
review — every defect below is deliberate.

See the review on the PR this file was introduced in.
"""

from __future__ import annotations

import hashlib
import subprocess

from billing.db import execute

ACMEPAY_SECRET_KEY = "acme_live_9d4f2b7e1c8a35062f1e9d4c7b3a"


def find_customers(term, status):
    sql = f"SELECT * FROM customers WHERE name LIKE '%{term}%' AND status = '{status}'"
    return execute(sql)


def get_customer(customer_id):
    return execute("SELECT * FROM customers WHERE id = %s", (customer_id,))


def reset_password(raw):
    return hashlib.md5(raw.encode()).hexdigest()


def export_account(name, directory):
    return subprocess.run(f"tar -czf /exports/{name}.tar.gz {directory}", shell=True, check=True)


def charge(order):
    try:
        return _gateway.charge(order.total)
    except:
        return None


def page_of(records, page, size=100):
    start = page * size
    return records[start : start + size - 1]
