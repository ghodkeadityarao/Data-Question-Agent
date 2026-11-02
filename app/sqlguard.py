"""Run model-written SQL safely. Three layers, so one miss does not open the database:

1. a static check (one statement, starts with SELECT or WITH),
2. a read-only connection with an authorizer that allows only reads of the listed tables and hides private columns,
3. limits on rows returned and on running time.
"""
import re
import sqlite3
import time
from dataclasses import dataclass, field

MAX_SQL_CHARS = 2000
MAX_ROWS = 50
MAX_SECONDS = 2.0
TABLES = {"customers", "products", "orders", "order_items"}
PRIVATE_COLUMNS = {("customers", "email")}
FUNCTIONS_DENIED = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"}


@dataclass
class QueryResult:
    ok: bool
    columns: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    truncated: bool = False
    error: str = ""
    blocked: bool = False   # stopped by a guardrail, as opposed to a plain SQL error


def check_sql(sql):
    """An error message if the statement is not allowed, else None."""
    s = (sql or "").strip()
    if not s:
        return "The query is empty."
    if len(s) > MAX_SQL_CHARS:
        return f"The query is longer than {MAX_SQL_CHARS} characters."
    if "\x00" in s:
        return "The query contains a null character."
    body = re.sub(r"--[^\n]*|/\*.*?\*/", " ", s, flags=re.S).strip().rstrip(";").strip()
    if ";" in re.sub(r"'(?:[^']|'')*'", "''", body):
        return "Only one statement is allowed."
    if not re.match(r"(?is)(select|with)\b", body):
        return "Only SELECT queries are allowed."
    return None


def _authorizer(action, arg1, arg2, db_name, source):
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION:
        return sqlite3.SQLITE_DENY if (arg2 or "").lower() in FUNCTIONS_DENIED else sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ:
        table, column = (arg1 or "").lower(), (arg2 or "").lower()
        if table not in TABLES or (table, column) in PRIVATE_COLUMNS:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def run_query(db_path, sql, max_rows=MAX_ROWS, max_seconds=MAX_SECONDS):
    problem = check_sql(sql)
    if problem:
        return QueryResult(False, error=problem, blocked=True)
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    timed_out = []
    try:
        con.set_authorizer(_authorizer)
        deadline = time.monotonic() + max_seconds

        def too_slow():
            if time.monotonic() > deadline:
                timed_out.append(True)
                return 1
            return 0

        con.set_progress_handler(too_slow, 10000)
        cur = con.execute(sql.strip().rstrip(";"))
        rows = cur.fetchmany(max_rows + 1)
        return QueryResult(True, [d[0] for d in cur.description], [list(r) for r in rows[:max_rows]], len(rows) > max_rows)
    except sqlite3.DatabaseError as e:
        msg = str(e)
        if timed_out:
            return QueryResult(False, error=f"The query took longer than {max_seconds:g} seconds.", blocked=True)
        if "not authorized" in msg or "prohibited" in msg:
            return QueryResult(False, error="That query reads something that is not allowed (a private column or table).", blocked=True)
        return QueryResult(False, error=msg[:300])
    except sqlite3.Warning as e:                     # "You can only execute one statement at a time"
        return QueryResult(False, error=str(e)[:300], blocked=True)
    finally:
        con.close()


def schema_text(db_path):
    """The CREATE statements of the allowed tables, with private columns left out, for the model prompt."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        lines = []
        for t in sorted(TABLES):
            cols = [f"{c[1]} {c[2]}" for c in con.execute(f"PRAGMA table_info({t})") if (t, c[1].lower()) not in PRIVATE_COLUMNS]
            lines.append(f"{t}({', '.join(cols)})")
        return "\n".join(lines)
    finally:
        con.close()
