"""The retrieval substrate: chunk boundaries and the offline embedder."""

from __future__ import annotations

import itertools
import math

import pytest

from pr_sentinel.llm.embeddings import HashingEmbedder
from pr_sentinel.retrieval.chunking import MAX_CHUNK_LINES, chunk_file, should_index

PY_SOURCE = '''"""Module docstring."""
import os
import sys

CONSTANT = 42


def alpha(x):
    return x + 1


class Beta:
    def method(self):
        return 2


def gamma():
    return 3
'''


def test_python_chunks_split_on_top_level_definitions():
    chunks = chunk_file("m.py", PY_SOURCE)
    symbols = [c.symbol for c in chunks]
    assert "<module>" in symbols
    assert {"alpha", "Beta", "gamma"} <= set(symbols)


def test_nested_definitions_do_not_create_their_own_chunk():
    assert "method" not in [c.symbol for c in chunk_file("m.py", PY_SOURCE)]


def test_chunk_line_ranges_are_contiguous_and_cover_the_file():
    chunks = sorted(chunk_file("m.py", PY_SOURCE), key=lambda c: c.start_line)
    assert chunks[0].start_line == 1
    assert chunks[-1].end_line == len(PY_SOURCE.splitlines())
    for a, b in itertools.pairwise(chunks):
        assert b.start_line == a.end_line + 1


def test_an_oversized_definition_is_windowed_not_truncated():
    body = "\n".join(f"    line_{i} = {i}" for i in range(MAX_CHUNK_LINES * 3))
    chunks = chunk_file("big.py", f"def huge():\n{body}\n")
    assert len(chunks) > 1
    assert all(c.end_line - c.start_line + 1 <= MAX_CHUNK_LINES for c in chunks)
    assert all(c.symbol == "huge" for c in chunks)


def test_a_file_with_no_recognisable_definitions_falls_back_to_windows():
    chunks = chunk_file("data.yml", "\n".join(f"key{i}: value{i}" for i in range(300)))
    assert len(chunks) > 1


def test_typescript_definitions_are_recognised():
    src = "export function alpha() {\n  return 1;\n}\n\nexport class Beta {\n  m() {}\n}\n"
    assert {"alpha", "Beta"} <= {c.symbol for c in chunk_file("m.ts", src)}


def test_content_hash_is_stable_and_content_sensitive():
    a = chunk_file("m.py", PY_SOURCE)[0]
    b = chunk_file("m.py", PY_SOURCE)[0]
    c = chunk_file("m.py", PY_SOURCE.replace("42", "43"))[0]
    assert a.content_hash == b.content_hash
    assert a.content_hash != c.content_hash or a.content == c.content


@pytest.mark.parametrize(
    "path,expected",
    [
        ("src/app.py", True),
        ("src/app.ts", True),
        ("schema.sql", True),
        ("node_modules/x/index.js", False),
        (".venv/lib/thing.py", False),
        ("logo.png", False),
        ("Makefile", False),
        ("__pycache__/a.py", False),
    ],
)
def test_index_inclusion_rules(path, expected):
    assert should_index(path, 1000) is expected


def test_enormous_files_are_skipped():
    assert not should_index("generated.py", 10_000_000)


async def test_hash_embeddings_are_deterministic_and_correctly_sized():
    e = HashingEmbedder(dim=256)
    a = await e.embed_one("def charge(customer_id): ...")
    b = await e.embed_one("def charge(customer_id): ...")
    assert a == b and len(a) == 256


async def test_hash_embeddings_are_unit_length():
    e = HashingEmbedder(dim=256)
    v = await e.embed_one("some source code here")
    assert math.isclose(math.sqrt(sum(x * x for x in v)), 1.0, rel_tol=1e-6)


async def test_hash_embeddings_rank_related_code_above_unrelated():
    e = HashingEmbedder(dim=512)
    query, related, unrelated = await e.embed(
        [
            "def charge_customer(customer_id, amount): db.execute(sql)",
            "def refund_customer(customer_id, amount): db.execute(sql)",
            "const buttonColour = theme.palette.primary.main;",
        ]
    )
    dot = lambda a, b: sum(x * y for x, y in zip(a, b, strict=True))  # noqa: E731
    assert dot(query, related) > dot(query, unrelated)


async def test_the_empty_string_does_not_blow_up():
    assert len(await HashingEmbedder(dim=64).embed_one("")) == 64
