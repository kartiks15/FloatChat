"""Bounded, read-only database access for the dashboard and MCP tools.

Only pass application-owned SQL to ``fetch_rows``. Model/user-provided SQL must
never reach this function; the public MCP interface exposes structured tools.
Use a SELECT-only PostgreSQL role as an additional permission boundary.
"""

import os

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

MAX_RESULT_ROWS = 5000


class DatabaseConfigurationError(ValueError):
    """Required database configuration is missing or invalid."""


class QueryLimitError(ValueError):
    """A result is too large to return without silently losing rows."""


def _bounded_setting(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise DatabaseConfigurationError(f"{name} must be an integer.") from exc
    if not minimum <= value <= maximum:
        raise DatabaseConfigurationError(f"{name} must be between {minimum} and {maximum}.")
    return value


def get_db_config() -> dict:
    """Read connection settings at call time without logging credentials."""
    password = os.getenv("DB_PASS", "")
    if not password:
        raise DatabaseConfigurationError("Set DB_PASS to the database reader password.")
    return {
        "dbname": os.getenv("DB_NAME", "floatchat"),
        "user": os.getenv("DB_USER", "floatchat_reader"),
        "password": password,
        "host": os.getenv("DB_HOST", "localhost"),
        "port": _bounded_setting("DB_PORT", 5432, 1, 65535),
        "connect_timeout": _bounded_setting("DB_CONNECT_TIMEOUT_SECONDS", 5, 1, 30),
        "sslmode": os.getenv("DB_SSLMODE", "prefer"),
        "application_name": "floatchat_reader",
    }


def connect_readonly():
    """Create a read-only connection with bounded query and lock durations."""
    timeout = _bounded_setting("DB_STATEMENT_TIMEOUT_MS", 10000, 100, 60000)
    conn = psycopg2.connect(
        **get_db_config(),
        options=(
            f"-c statement_timeout={timeout} -c lock_timeout=3000 "
            "-c idle_in_transaction_session_timeout=15000 "
            "-c default_transaction_read_only=on"
        ),
    )
    try:
        conn.set_session(readonly=True, autocommit=False)
    except Exception:
        conn.close()
        raise
    return conn


def fetch_rows(sql: str, params=None, *, max_rows: int = 1000) -> list[dict]:
    """Fetch trusted application SQL through a bounded server-side cursor.

    A result exceeding the requested cap raises instead of returning incomplete
    data. Callers should add an explicit SQL LIMIT when showing a sample.
    """
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or not 1 <= max_rows <= MAX_RESULT_ROWS:
        raise ValueError(f"max_rows must be an integer between 1 and {MAX_RESULT_ROWS}.")
    conn = connect_readonly()
    try:
        with conn.cursor(name="floatchat_read", cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchmany(max_rows + 1)
            if len(rows) > max_rows:
                raise QueryLimitError(f"Result exceeds {max_rows} rows; narrow the date or region filters.")
            return [dict(row) for row in rows]
    finally:
        conn.close()
