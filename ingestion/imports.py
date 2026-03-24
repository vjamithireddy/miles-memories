import logging
from typing import Any

from app.db import get_conn

from ingestion.common import basename, file_sha256

logger = logging.getLogger(__name__)


def create_import(import_type: str, source_name: str, file_path: str) -> int:
    file_hash = file_sha256(file_path)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, import_status, created_at FROM imports WHERE file_hash = %s",
                (file_hash,),
            )
            existing = cur.fetchone()
            if existing:
                logger.warning(
                    "Re-ingesting file hash %s (import_id=%s status=%s created_at=%s)",
                    file_hash,
                    existing[0],
                    existing[1],
                    existing[2],
                )
            cur.execute(
                """
                INSERT INTO imports (import_type, source_name, filename, file_path, file_hash, import_status, started_at)
                VALUES (%s, %s, %s, %s, %s, 'processing', NOW())
                ON CONFLICT (file_hash)
                DO UPDATE SET started_at = NOW(), import_status = 'processing', error_message = NULL
                RETURNING id
                """,
                (import_type, source_name, basename(file_path), file_path, file_hash),
            )
            return int(cur.fetchone()[0])


def complete_import(import_id: int) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE imports
                SET import_status = 'completed', completed_at = NOW(), error_message = NULL
                WHERE id = %s
                """,
                (import_id,),
            )


def fail_import(import_id: int, message: str) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE imports
                SET import_status = 'failed', completed_at = NOW(), error_message = %s
                WHERE id = %s
                """,
                (message[:1000], import_id),
            )


def get_import_summary(import_id: int) -> dict[str, Any]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, import_type, import_status, created_at, completed_at FROM imports WHERE id = %s",
                (import_id,),
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError(f"Import {import_id} not found")
            return {
                "id": row[0],
                "import_type": row[1],
                "status": row[2],
                "created_at": str(row[3]),
                "completed_at": str(row[4]) if row[4] else None,
            }
