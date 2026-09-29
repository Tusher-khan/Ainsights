"""SQL security and query validation."""

import pytest

from app.security.sql_security import enforce_limit, is_safe, validate_sql, validator_backend
from app.utils.exceptions import SQLSecurityError

TABLES = {"orders"}

SAFE = [
    'SELECT SUM("Sales") AS total FROM orders',
    'SELECT Region, SUM(Sales) AS total FROM orders GROUP BY Region ORDER BY total DESC LIMIT 5',
    'WITH monthly AS (SELECT * FROM orders) SELECT COUNT(*) FROM monthly',
    "SELECT strftime('%Y-%m', \"Order Date\") AS period, AVG(Profit) FROM orders GROUP BY period",
]

DANGEROUS = [
    "DROP TABLE orders",
    "DELETE FROM orders",
    "UPDATE orders SET Sales = 0",
    "INSERT INTO orders VALUES (1)",
    "ALTER TABLE orders ADD COLUMN x TEXT",
    "CREATE TABLE evil (x INT)",
    "TRUNCATE TABLE orders",
    "ATTACH DATABASE '/etc/passwd' AS leak",
    "DETACH DATABASE main",
    "PRAGMA table_info(orders)",
    "SELECT 1; DROP TABLE orders",
    "SELECT * FROM orders; DELETE FROM orders",
    "SELECT * FROM orders -- harmless\n; DROP TABLE orders",
    "SELECT * FROM sqlite_master",
    "SELECT load_extension('evil.so')",
    "SELECT readfile('/etc/passwd')",
]


def test_backend_is_reported():
    assert validator_backend() in {"sqlglot-ast", "lexical-strict"}


@pytest.mark.parametrize("sql", SAFE)
def test_safe_analytical_sql_passes(sql):
    assert is_safe(sql, TABLES)


@pytest.mark.parametrize("sql", DANGEROUS)
def test_dangerous_sql_is_blocked(sql):
    assert not is_safe(sql, TABLES)
    with pytest.raises(SQLSecurityError):
        validate_sql(sql, TABLES)


def test_unknown_table_is_blocked():
    with pytest.raises(SQLSecurityError):
        validate_sql("SELECT * FROM secrets", TABLES)


def test_cte_alias_is_not_treated_as_unknown_table():
    validate_sql("WITH t AS (SELECT * FROM orders) SELECT * FROM t", TABLES)


def test_limit_is_appended_when_absent():
    assert enforce_limit("SELECT * FROM orders", 100).endswith("LIMIT 100")


def test_existing_limit_is_preserved():
    sql = "SELECT * FROM orders LIMIT 5"
    assert enforce_limit(sql, 100) == sql


def test_empty_sql_is_rejected():
    with pytest.raises(SQLSecurityError):
        validate_sql("", TABLES)
