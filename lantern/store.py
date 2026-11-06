"""SQLite cache of translation records.

A rerun skips a line when its id is already translated or reviewed and the
source hash still matches. Flagged and stale lines stay eligible.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lantern.models import Record

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id TEXT PRIMARY KEY,
    engine TEXT NOT NULL,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    translation TEXT,
    speaker TEXT,
    file TEXT NOT NULL,
    scene TEXT,
    line INTEGER NOT NULL,
    source_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    rebuild_json TEXT NOT NULL,
    error TEXT,
    provider TEXT,
    model TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_records_status ON records(status);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return default if row is None else str(row["value"])

    def replace_extracted(self, records: list[Record]) -> None:
        """Insert new lines, invalidate changed lines, drop lines that vanished."""

        now = utcnow()
        existing = {record.id: record for record in self.list_records()}
        seen: set[str] = set()
        with self._conn:
            for record in records:
                seen.add(record.id)
                old = existing.get(record.id)
                if old is None:
                    self._insert(record, now)
                    continue
                status = old.status
                translation = old.translation
                error = old.error
                provider = old.provider
                model = old.model
                if old.source_hash != record.source_hash:
                    status = "stale"
                    error = "source changed since the last translation"
                self._conn.execute(
                    """
                    UPDATE records SET
                        engine = ?, kind = ?, source = ?, translation = ?, speaker = ?,
                        file = ?, scene = ?, line = ?, source_hash = ?, status = ?,
                        ordinal = ?, rebuild_json = ?, error = ?, provider = ?,
                        model = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        record.engine,
                        record.kind,
                        record.source,
                        translation,
                        record.speaker,
                        record.file,
                        record.scene,
                        record.line,
                        record.source_hash,
                        status,
                        record.ordinal,
                        json.dumps(record.rebuild, ensure_ascii=False),
                        error,
                        provider,
                        model,
                        now,
                        record.id,
                    ),
                )
            if seen:
                placeholders = ",".join("?" for _ in seen)
                self._conn.execute(f"DELETE FROM records WHERE id NOT IN ({placeholders})", tuple(seen))
            else:
                self._conn.execute("DELETE FROM records")

    def _insert(self, record: Record, now: str) -> None:
        self._conn.execute(
            """
            INSERT INTO records (
                id, engine, kind, source, translation, speaker, file, scene, line,
                source_hash, status, ordinal, rebuild_json, error, provider, model, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.engine,
                record.kind,
                record.source,
                record.translation,
                record.speaker,
                record.file,
                record.scene,
                record.line,
                record.source_hash,
                record.status,
                record.ordinal,
                json.dumps(record.rebuild, ensure_ascii=False),
                record.error,
                record.provider,
                record.model,
                now,
            ),
        )

    def list_records(self) -> list[Record]:
        rows = self._conn.execute("SELECT * FROM records ORDER BY ordinal, id").fetchall()
        return [self._row(row) for row in rows]

    def pending(self, *, force: bool = False) -> list[Record]:
        records = self.list_records()
        wanted = {"untranslated", "stale", "flagged"}
        if force:
            wanted.add("translated")
        return [record for record in records if record.status in wanted]

    def counts(self) -> dict[str, int]:
        counts = {status: 0 for status in ("untranslated", "translated", "reviewed", "flagged", "stale")}
        for row in self._conn.execute("SELECT status, COUNT(*) AS n FROM records GROUP BY status"):
            counts[str(row["status"])] = int(row["n"])
        counts["total"] = sum(counts.values())
        return counts

    def save_result(
        self,
        record_id: str,
        *,
        translation: str | None,
        status: str,
        error: str | None,
        provider: str | None,
        model: str | None,
    ) -> None:
        self._conn.execute(
            """
            UPDATE records
            SET translation = ?, status = ?, error = ?, provider = ?, model = ?, updated_at = ?
            WHERE id = ?
            """,
            (translation, status, error, provider, model, utcnow(), record_id),
        )
        self._conn.commit()

    def import_edit(self, record_id: str, translation: str, status: str) -> bool:
        cursor = self._conn.execute(
            """
            UPDATE records
            SET translation = ?, status = ?, error = NULL, updated_at = ?
            WHERE id = ?
            """,
            (translation, status, utcnow(), record_id),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def _row(self, row: sqlite3.Row) -> Record:
        rebuild: dict[str, Any] = json.loads(row["rebuild_json"]) if row["rebuild_json"] else {}
        return Record(
            id=row["id"],
            engine=row["engine"],
            kind=row["kind"],
            source=row["source"],
            translation=row["translation"],
            speaker=row["speaker"],
            file=row["file"],
            scene=row["scene"],
            line=int(row["line"]),
            source_hash=row["source_hash"],
            status=row["status"],
            ordinal=int(row["ordinal"]),
            rebuild=rebuild,
            error=row["error"],
            provider=row["provider"],
            model=row["model"],
        )
