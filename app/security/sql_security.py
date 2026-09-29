"""SQL security.

Generated SQL passes four independent gates before it can touch data:

1. **Lexical gate** — strips comments/strings, rejects statement stacking and
   any forbidden keyword.
2. **AST gate** — SQLGlot parses the statement; anything that is not a single
   SELECT (optionally with CTEs) is rejected, and every referenced table is
   checked against an allow-list.
3. **Authorizer gate** — the SQLite connection denies writes at engine level
   (see app/database/connection.py).
4. **Read-only connection** — the file is opened with `mode=ro`.

Gates 1 and 2 live here. Gate 2 degrades to a stricter lexical analysis when
SQLGlot is not installed, and this is reported by `validator_backend()`.
"""

from __future__ import annotations

import re

from app.utils.exceptions import SQLSecurityError
from app.utils.logger import get_logger

log = get_logger("security.sql")

try:  # pragma: no cover - depends on environment
    import sqlglot
    from sqlglot import exp

    SQLGLOT_AVAILABLE = True
except Exception:  # pragma: no cover
    sqlglot = None
    exp = None
    SQLGLOT_AVAILABLE = False

FORBIDDEN_KEYWORDS = [
    "drop", "delete", "update", "insert", "alter", "create", "truncate", "replace",
    "attach", "detach", "vacuum", "reindex", "grant", "revoke", "commit", "rollback",
    "savepoint", "pragma", "load_extension", "readfile", "writefile", "edit",
    "sqlite_master_writable", "exec", "execute", "shell", "system", "copy", "into outfile",
]

FORBIDDEN_RE = re.compile(r"\b(" + "|".join(re.escape(k) for k in FORBIDDEN_KEYWORDS) + r")\b", re.I)
COMMENT_RE = re.compile(r"(--[^\n]*|/\*.*?\*/)", re.S)
STRING_RE = re.compile(r"'([^']|'')*'|\"([^\"]|\"\")*\"")
MAX_SQL_LENGTH = 6000


def validator_backend() -> str:
    return "sqlglot-ast" if SQLGLOT_AVAILABLE else "lexical-strict"


def _strip_literals(sql: str) -> str:
    """Remove comments and string literals so keyword scanning cannot be fooled."""
    without_comments = COMMENT_RE.sub(" ", sql)
    return STRING_RE.sub("''", without_comments)


def _lexical_gate(sql: str) -> str:
    if not sql or not sql.strip():
        raise SQLSecurityError("No SQL was produced for this question.")
    if len(sql) > MAX_SQL_LENGTH:
        raise SQLSecurityError("The generated query is too long to run safely.")

    scannable = _strip_literals(sql)
    statements = [s for s in scannable.split(";") if s.strip()]
    if len(statements) > 1:
        raise SQLSecurityError(
            "Only a single SELECT statement can be run.", "statement stacking detected"
        )

    body = statements[0].strip()
    first_word = body.split()[0].lower() if body.split() else ""
    if first_word not in {"select", "with"}:
        raise SQLSecurityError(
            "AInsight only runs read-only SELECT queries.", f"statement starts with '{first_word}'"
        )

    match = FORBIDDEN_RE.search(body)
    if match:
        raise SQLSecurityError(
            "That query was blocked because it is not a read-only analytical query.",
            f"forbidden keyword '{match.group(1)}'",
        )
    if re.search(r"\bsqlite_(master|schema|temp_master)\b", body, re.I):
        raise SQLSecurityError("Queries against internal SQLite catalogues are not allowed.")
    return sql.strip().rstrip(";")


def _ast_gate(sql: str, allowed_tables: set[str] | None) -> str:
    """Parse with SQLGlot and enforce single-SELECT plus table allow-list."""
    try:
        expressions = sqlglot.parse(sql, read="sqlite")
    except Exception as exc:
        raise SQLSecurityError("The generated SQL could not be parsed.", str(exc)) from exc

    expressions = [e for e in expressions if e is not None]
    if len(expressions) != 1:
        raise SQLSecurityError("Only a single statement can be run.", f"{len(expressions)} statements")

    tree = expressions[0]
    if not isinstance(tree, (exp.Select, exp.Union, exp.Subquery)) and not (
        isinstance(tree, exp.Expression) and tree.find(exp.Select) and tree.args.get("with")
    ):
        if not isinstance(tree, exp.Select):
            raise SQLSecurityError(
                "AInsight only runs SELECT queries.", f"root node {type(tree).__name__}"
            )

    unsafe_nodes = (
        exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter,
        exp.Command, exp.Pragma, exp.Attach, exp.Detach,
    )
    available = tuple(n for n in unsafe_nodes if n is not None)
    for node in tree.walk():
        if isinstance(node, available):
            raise SQLSecurityError(
                "That query was blocked because it is not read-only.",
                f"node {type(node).__name__}",
            )

    if allowed_tables is not None:
        cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
        allowed = {t.lower() for t in allowed_tables} | cte_names
        for table in tree.find_all(exp.Table):
            name = (table.name or "").lower()
            if name and name not in allowed:
                raise SQLSecurityError(
                    f"The query references '{table.name}', which is not an available table.",
                    "table not in allow-list",
                )
    return tree.sql(dialect="sqlite")


def _lexical_table_gate(sql: str, allowed_tables: set[str] | None) -> None:
    if allowed_tables is None:
        return
    body = _strip_literals(sql)
    cte_names = {m.lower() for m in re.findall(r"\bwith\s+([A-Za-z_][\w]*)\s+as\b", body, re.I)}
    referenced = {
        m.strip('"`[]').lower()
        for m in re.findall(r"\b(?:from|join)\s+([A-Za-z_][\w]*|\"[^\"]+\")", body, re.I)
    }
    allowed = {t.lower() for t in allowed_tables} | cte_names
    unknown = referenced - allowed
    if unknown:
        raise SQLSecurityError(
            f"The query references unknown tables: {', '.join(sorted(unknown))}.",
            "table not in allow-list",
        )


def validate_sql(sql: str, allowed_tables: set[str] | None = None) -> str:
    """Validate and normalise generated SQL. Raises SQLSecurityError if unsafe."""
    checked = _lexical_gate(sql)
    if SQLGLOT_AVAILABLE:
        normalised = _ast_gate(checked, allowed_tables)
    else:
        _lexical_table_gate(checked, allowed_tables)
        normalised = checked
    log.info("SQL validated via %s", validator_backend())
    return normalised


def enforce_limit(sql: str, max_rows: int) -> str:
    """Append a LIMIT when the query does not already constrain its output."""
    body = _strip_literals(sql)
    if re.search(r"\blimit\s+\d+", body, re.I):
        return sql
    return f"{sql.rstrip().rstrip(';')} LIMIT {int(max_rows)}"


def is_safe(sql: str, allowed_tables: set[str] | None = None) -> bool:
    try:
        validate_sql(sql, allowed_tables)
        return True
    except SQLSecurityError:
        return False
