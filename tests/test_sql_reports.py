"""Execute report grouping locally; adapt only Athena's array operations."""
import json
from pathlib import Path
import re
import sqlite3

import pytest

from scripts.verify_aws import sql_statements
from src.validate import source_system_label

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("filename", ["data_quality.sql", "rework_report.sql"])
def test_sql_report_groups_sources_like_python(filename):
    statements = sql_statements((ROOT / "sql" / filename).read_text())
    sql = next(statement for statement in statements if "WITH normalized AS" in statement)
    if filename == "rework_report.sql":
        # SQLite JSON expansion replaces Athena UNNEST. Sample selection is
        # independent of grouping and is outside this regression test's scope.
        sql = sql.replace("CROSS JOIN UNNEST(error_reasons) AS u (reason)",
                          "CROSS JOIN json_each(error_reasons) AS u")
        sql = re.sub(r"\breason\b", "u.value", sql)
        sql = sql.replace("array_join(slice(array_distinct(array_agg(transaction_id)), 1, 5), ' ')",
                          "MIN(transaction_id)")
    values = [None, "", " ", "CARDS", " CARDS ", "\u0085CARDS\u00a0", "\u2003", "CA RDS"]
    # Check the actual regex from SQL against every Python whitespace character.
    pattern = re.search(r"regexp_replace\(source_system,\s*'([^']+)'", sql)[1]
    for codepoint in range(0x110000):
        char = chr(codepoint)
        if char.isspace():
            assert re.sub(pattern, "", char + "CARDS" + char) == "CARDS"
    with sqlite3.connect(":memory:") as connection:
        connection.create_function("regexp_replace", 3,
                                   lambda value, regex, replacement:
                                   None if value is None else re.sub(regex, replacement, value))
        connection.execute("CREATE TABLE rejected_transactions "
                           "(source_system TEXT, transaction_id TEXT, error_reasons TEXT)")
        connection.executemany("INSERT INTO rejected_transactions VALUES (?, ?, ?)",
                               [(value, f"T{i}", json.dumps(["INVALID_AMOUNT"]))
                                for i, value in enumerate(values)])
        rows = connection.execute(sql).fetchall()
    actual = {row[0]: row[2] if filename == "rework_report.sql" else row[1] for row in rows}
    expected = {}
    for value in values:
        label = source_system_label(value)
        expected[label] = expected.get(label, 0) + 1
    assert actual == expected
