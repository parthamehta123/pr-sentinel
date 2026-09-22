"""SQL statement splitting.

Naively splitting on `;` breaks every PL/pgSQL body in the migrations, which is
how a migration runner silently applies half a schema.
"""

from __future__ import annotations

from pr_sentinel.db.migrate import MIGRATIONS_DIR, split_statements


def test_plain_statements_split_on_semicolons():
    assert len(split_statements("SELECT 1; SELECT 2; SELECT 3;")) == 3


def test_a_semicolon_inside_a_string_is_not_a_boundary():
    stmts = split_statements("INSERT INTO t VALUES ('a;b'); SELECT 1;")
    assert len(stmts) == 2
    assert "'a;b'" in stmts[0]


def test_a_dollar_quoted_function_body_stays_in_one_statement():
    sql = """
    CREATE FUNCTION f() RETURNS TRIGGER AS $$
    BEGIN
        RAISE EXCEPTION 'no; really';
        RETURN NULL;
    END;
    $$ LANGUAGE plpgsql;
    SELECT 1;
    """
    stmts = split_statements(sql)
    assert len(stmts) == 2
    assert "RETURN NULL;" in stmts[0]


def test_tagged_dollar_quotes_are_handled():
    sql = "DO $body$ BEGIN PERFORM 1; END $body$; SELECT 2;"
    assert len(split_statements(sql)) == 2


def test_comments_are_preserved_but_never_emitted_alone():
    stmts = split_statements("-- a note\nSELECT 1;\n-- trailing note\n")
    assert len(stmts) == 1


def test_a_semicolon_inside_a_line_comment_is_ignored():
    assert len(split_statements("SELECT 1 -- what; about this\n;")) == 1


def test_every_shipped_migration_splits_into_runnable_statements():
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    assert files, f"no migrations found in {MIGRATIONS_DIR}"
    for path in files:
        stmts = split_statements(path.read_text())
        assert stmts, f"{path.name} produced no statements"
        for stmt in stmts:
            assert stmt.count("$$") % 2 == 0, f"{path.name}: unbalanced $$ in a split statement"


def test_the_continuous_aggregates_are_separate_statements():
    """They must be, because they cannot run inside a transaction block."""
    sql = (MIGRATIONS_DIR / "003_timeseries.sql").read_text()
    cagg = [s for s in split_statements(sql) if "timescaledb.continuous" in s]
    assert len(cagg) == 2
    for stmt in cagg:
        assert "BEGIN;" not in stmt.upper()
