"""
State tracker using SQLite to provide idempotent, resumable pipeline execution.
Tracks dataset downloads, parsed language maps, and Commons upload status.
"""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class StateTracker:
    """Manages pipeline progress in a local SQLite database."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Create necessary tables if they don't exist."""
        with self._connection() as conn:
            cursor = conn.cursor()

            # Datasets table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS datasets (
                    record_id TEXT PRIMARY KEY,
                    doi TEXT,
                    title TEXT,
                    slug TEXT,
                    status TEXT NOT NULL,
                    download_path TEXT,
                    extracted_path TEXT,
                    metadata_json TEXT,
                    downloaded_at TEXT,
                    error_message TEXT
                )
            """)

            # Maps table (per language)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS maps (
                    map_id TEXT PRIMARY KEY,
                    record_id TEXT,
                    glottocode TEXT,
                    language_name TEXT,
                    dataset_slug TEXT,
                    page_title TEXT NOT NULL,
                    map_path TEXT,
                    content_hash TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT,
                    uploaded_at TEXT,
                    commons_url TEXT,
                    error_message TEXT,
                    FOREIGN KEY (record_id) REFERENCES datasets(record_id)
                )
            """)

            # Indices for quick lookups
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_maps_record ON maps(record_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_maps_status ON maps(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_maps_glottocode ON maps(glottocode)")
            conn.commit()

    @staticmethod
    def now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    # --- Dataset operations ---

    def record_dataset_discovered(self, record_id: str, doi: str, title: str, slug: str, metadata: dict) -> None:
        with self._connection() as conn:
            conn.execute("""
                INSERT INTO datasets (record_id, doi, title, slug, status, metadata_json)
                VALUES (?, ?, ?, ?, 'DISCOVERED', ?)
                ON CONFLICT(record_id) DO UPDATE SET
                    doi=excluded.doi,
                    title=excluded.title,
                    slug=excluded.slug,
                    metadata_json=excluded.metadata_json
            """, (str(record_id), doi, title, slug, json.dumps(metadata)))
            conn.commit()

    def record_dataset_downloaded(self, record_id: str, download_path: Path, extracted_path: Path) -> None:
        with self._connection() as conn:
            conn.execute("""
                UPDATE datasets
                SET status='DOWNLOADED',
                    download_path=?,
                    extracted_path=?,
                    downloaded_at=?,
                    error_message=NULL
                WHERE record_id=?
            """, (str(download_path), str(extracted_path), self.now_iso(), str(record_id)))
            conn.commit()

    def record_dataset_failed(self, record_id: str, error: str) -> None:
        with self._connection() as conn:
            conn.execute("""
                UPDATE datasets
                SET status='FAILED',
                    error_message=?
                WHERE record_id=?
            """, (error, str(record_id)))
            conn.commit()

    def is_dataset_downloaded(self, record_id: str) -> bool:
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM datasets WHERE record_id=? AND status IN ('DOWNLOADED', 'PROCESSED')", (str(record_id),))
            row = cursor.fetchone()
            return row is not None

    def get_dataset(self, record_id: str) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM datasets WHERE record_id=?", (str(record_id),))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_all_datasets(self) -> List[Dict[str, Any]]:
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM datasets ORDER BY record_id")
            return [dict(r) for r in cursor.fetchall()]

    # --- Map operations ---

    @staticmethod
    def compute_map_id(record_id: str, glottocode: str, dataset_slug: str) -> str:
        return f"{record_id}:{dataset_slug}:{glottocode}"

    @staticmethod
    def compute_hash(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def record_map_generated(
        self,
        record_id: str,
        glottocode: str,
        language_name: str,
        dataset_slug: str,
        page_title: str,
        map_path: Path,
        content_hash: str
    ) -> str:
        map_id = self.compute_map_id(record_id, glottocode, dataset_slug)
        with self._connection() as conn:
            conn.execute("""
                INSERT INTO maps (
                    map_id, record_id, glottocode, language_name,
                    dataset_slug, page_title, map_path, content_hash,
                    status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'GENERATED', ?)
                ON CONFLICT(map_id) DO UPDATE SET
                    page_title=excluded.page_title,
                    map_path=excluded.map_path,
                    content_hash=excluded.content_hash,
                    status=CASE WHEN maps.status='UPLOADED' AND maps.content_hash=excluded.content_hash THEN 'UPLOADED' ELSE 'GENERATED' END
            """, (
                map_id, str(record_id), glottocode, language_name,
                dataset_slug, page_title, str(map_path), content_hash,
                self.now_iso()
            ))
            conn.commit()
        return map_id

    def record_map_uploaded(self, map_id: str, commons_url: str) -> None:
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT page_title FROM maps WHERE map_id=?", (map_id,))
            row = cursor.fetchone()
            if row and row["page_title"]:
                conn.execute("""
                    UPDATE maps
                    SET status='UPLOADED',
                        uploaded_at=?,
                        commons_url=?,
                        error_message=NULL
                    WHERE page_title=?
                """, (self.now_iso(), commons_url, row["page_title"]))
            else:
                conn.execute("""
                    UPDATE maps
                    SET status='UPLOADED',
                        uploaded_at=?,
                        commons_url=?,
                        error_message=NULL
                    WHERE map_id=?
                """, (self.now_iso(), commons_url, map_id))
            conn.commit()

    def record_map_upload_failed(self, map_id: str, error: str) -> None:
        with self._connection() as conn:
            conn.execute("""
                UPDATE maps
                SET status='UPLOAD_FAILED',
                    error_message=?
                WHERE map_id=?
            """, (error, map_id))
            conn.commit()

    def is_map_uploaded(self, map_id: str, current_hash: Optional[str] = None) -> bool:
        with self._connection() as conn:
            cursor = conn.cursor()
            if current_hash:
                cursor.execute(
                    "SELECT 1 FROM maps WHERE map_id=? AND status='UPLOADED' AND content_hash=?",
                    (map_id, current_hash)
                )
            else:
                cursor.execute(
                    "SELECT 1 FROM maps WHERE map_id=? AND status='UPLOADED'",
                    (map_id,)
                )
            return cursor.fetchone() is not None

    def get_pending_uploads(self) -> List[Dict[str, Any]]:
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM maps
                WHERE status IN ('GENERATED', 'UPLOAD_FAILED')
                ORDER BY glottocode, record_id
            """)
            return [dict(r) for r in cursor.fetchall()]

    def get_statistics(self) -> Dict[str, Any]:
        with self._connection() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM datasets")
            total_datasets = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM datasets WHERE status='DOWNLOADED'")
            downloaded_datasets = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM maps")
            total_maps = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM maps WHERE status='UPLOADED'")
            uploaded_maps = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM maps WHERE status IN ('GENERATED', 'UPLOAD_FAILED')")
            pending_maps = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(DISTINCT glottocode) FROM maps")
            unique_languages = cursor.fetchone()[0]

            return {
                "total_datasets": total_datasets,
                "downloaded_datasets": downloaded_datasets,
                "total_maps": total_maps,
                "uploaded_maps": uploaded_maps,
                "pending_maps": pending_maps,
                "unique_languages": unique_languages,
            }
