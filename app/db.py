from contextlib import contextmanager
from typing import Iterator

from psycopg import Connection, connect

from app.settings import get_database_url


@contextmanager
def get_conn() -> Iterator[Connection]:
    conn = connect(get_database_url())
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
