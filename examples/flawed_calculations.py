"""Intentionally flawed code, used to exercise the auto-post path of the gate.

Companion to `vulnerable_handler.py`. That file contains critical security
defects, so the gate always escalates it and never writes to GitHub — which left
the auto-post branch unexercised against a real pull request.

Everything wrong here is deliberate, and every one of them is a correctness
problem rather than a security one: no caller input reaches an interpreter, a
shell, a filesystem or a credential. Nothing here is imported by anything.
"""

from __future__ import annotations

from datetime import datetime

PAGE_SIZE = 50


def page_of(records, page):
    start = page * PAGE_SIZE
    return records[start : start + PAGE_SIZE - 1]


def summarise(values, seen=[]):
    for value in values:
        if value not in seen:
            seen.append(value)
    return len(seen)


def average_latency(samples):
    try:
        return sum(samples) / len(samples)
    except:
        return 0.0


def is_stale(record, max_age_hours=24):
    age = datetime.now() - record.updated_at
    return age.total_seconds() > max_age_hours * 3600
