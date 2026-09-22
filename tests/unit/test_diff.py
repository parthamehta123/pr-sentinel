"""Diff parsing. Every line number downstream depends on this being exact."""

from __future__ import annotations

from pr_sentinel.forge.diff import (
    build_diff_file,
    diff_query_text,
    fts_query,
    parse_patch,
    render_for_prompt,
)

PATCH = """@@ -10,6 +10,9 @@ class Handler:
     def __init__(self, db):
         self.db = db
 
+    def charge(self, cid):
+        return self.db.execute(f"SELECT 1 WHERE id={cid}")
+
     def refund(self, cid):
"""

MULTI_HUNK = """@@ -1,3 +1,4 @@
 import os
+import sys
 import json
 
@@ -20,5 +21,6 @@ def main():
     run()
+    cleanup()
     return 0
"""


def test_new_file_line_numbers_are_exact():
    hunk = parse_patch(PATCH)[0]
    assert hunk.new_start == 10
    # context: 10, 11, 12 -> added: 13, 14, 15 -> context: 16
    assert hunk.added_lines == [13, 14, 15]
    assert hunk.context_lines == [10, 11, 12, 16]


def test_deleted_lines_do_not_advance_the_new_counter():
    patch = """@@ -5,4 +5,3 @@
 keep
-gone
-also gone
+replacement
"""
    hunk = parse_patch(patch)[0]
    assert hunk.context_lines == [5]
    assert hunk.added_lines == [6]


def test_multiple_hunks_each_restart_from_their_own_header():
    hunks = parse_patch(MULTI_HUNK)
    assert [h.new_start for h in hunks] == [1, 21]
    assert hunks[0].added_lines == [2]
    assert hunks[1].added_lines == [22]


def test_addressable_lines_covers_added_and_context_only():
    f = build_diff_file(
        {"filename": "a.py", "status": "modified", "additions": 3, "deletions": 0, "patch": PATCH}
    )
    lines = f.addressable_lines()
    assert lines == {10, 11, 12, 13, 14, 15, 16}
    assert 9 not in lines and 17 not in lines


def test_no_newline_marker_does_not_shift_numbering():
    patch = "@@ -1,1 +1,2 @@\n old\n+new\n\\ No newline at end of file\n"
    hunk = parse_patch(patch)[0]
    assert hunk.added_lines == [2]


def test_rendered_diff_carries_the_line_numbers_the_model_must_cite():
    f = build_diff_file(
        {"filename": "a.py", "status": "modified", "additions": 3, "deletions": 0, "patch": PATCH}
    )
    text, truncated = render_for_prompt([f])
    assert not truncated
    assert "+++ a.py" in text
    assert "    13 +     def charge(self, cid):" in text


def test_render_truncates_rather_than_blowing_the_context_window():
    big = "@@ -1,1 +1,200 @@\n" + "\n".join(f"+line {i}" for i in range(200))
    f = build_diff_file(
        {"filename": "big.py", "status": "modified", "additions": 200, "deletions": 0, "patch": big}
    )
    text, truncated = render_for_prompt([f], max_bytes=100)
    assert truncated
    assert "diff budget exhausted" in text


def test_binary_files_are_flagged_not_parsed():
    f = build_diff_file({"filename": "logo.png", "status": "modified", "additions": 0, "deletions": 0})
    assert f.binary
    assert f.addressable_lines() == set()


def test_language_is_derived_from_the_extension():
    assert build_diff_file({"filename": "a/b.py", "patch": ""}).language == "python"
    assert build_diff_file({"filename": "a/b.tsx", "patch": ""}).language == "typescript"
    assert build_diff_file({"filename": "a/LICENSE", "patch": ""}).language is None


def test_query_text_is_built_from_added_code_and_paths():
    f = build_diff_file(
        {
            "filename": "billing/handler.py",
            "status": "modified",
            "additions": 3,
            "deletions": 0,
            "patch": PATCH,
        }
    )
    q = diff_query_text([f])
    assert "billing handler" in q
    assert "def charge" in q
    assert "def refund" not in q  # context lines are not what we are searching for


# --- full-text query construction ------------------------------------------
#
# Regression tests for two bugs found by replaying a real 31-file pull request:
# `websearch_to_tsquery` ANDs unquoted terms (so a long query matched nothing),
# and a query that deep raises "tsquery stack too small".


def _file(name: str, added: list[str]):
    patch = f"@@ -1,1 +1,{len(added) + 1} @@\n unchanged\n" + "\n".join(f"+{ln}" for ln in added)
    return build_diff_file(
        {"filename": name, "status": "modified", "additions": len(added), "deletions": 0, "patch": patch}
    )


def test_terms_are_or_joined_not_and_joined():
    """AND across every token demands all of them in one chunk, which matches nothing."""
    q = fts_query(
        [_file("billing/charges.py", ["def charge_customer(customer_id):", "    settle(customer_id)"])]
    )
    assert " or " in q
    assert "&" not in q


def test_the_term_count_is_bounded():
    big = [f"variable_number_{i} = compute_thing_{i}()" for i in range(400)]
    q = fts_query([_file("big.py", big)], max_terms=24)
    assert 0 < len(q.split(" or ")) <= 24


def test_identifiers_from_added_lines_are_included():
    q = fts_query([_file("a.py", ["def charge_customer(customer_id):"])])
    assert "charge_customer" in q and "customer_id" in q


def test_path_segments_are_weighted_into_the_query():
    q = fts_query([_file("billing/handler.py", ["x = 1"])])
    assert "billing" in q and "handler" in q


def test_deleted_and_context_lines_are_not_queried():
    patch = "@@ -1,3 +1,3 @@\n contextsymbol\n-deletedsymbol\n+addedsymbol\n"
    f = build_diff_file(
        {"filename": "a.py", "status": "modified", "additions": 1, "deletions": 1, "patch": patch}
    )
    q = fts_query([f])
    assert "addedsymbol" in q
    assert "deletedsymbol" not in q and "contextsymbol" not in q


def test_common_keywords_are_excluded():
    q = fts_query([_file("a.py", ["def process(self, value, data):", "    return value"])])
    for noise in ("self", "return", "value", "data"):
        assert noise not in q.split(" or ")


def test_short_tokens_are_excluded():
    q = fts_query([_file("a.py", ["ab = cd + efg"])])
    assert "ab" not in q.split(" or ")


def test_a_diff_with_nothing_distinctive_yields_an_empty_query():
    """Callers rely on this: an empty string disables the full-text half rather
    than producing a tsquery that matches everything."""
    assert fts_query([_file("a.py", ["x = 1", "y = 2"])]) == ""


def test_terms_are_safe_to_put_in_a_tsquery():
    q = fts_query([_file("a.py", ["result = call(); DROP TABLE users; -- ' \" & | ! ( )"])])
    assert all(t.replace("_", "").isalnum() for t in q.split(" or ") if t)
