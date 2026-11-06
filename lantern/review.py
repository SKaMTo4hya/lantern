"""Export flagged lines for review and import the edited translations."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from lantern.models import Record
from lantern.store import Store

_COLUMNS = ("id", "kind", "status", "file", "line", "scene", "speaker", "source", "translation", "error")
_IMPORT_STATUSES = {"reviewed", "translated", "flagged", "untranslated"}


def export_records(records: list[Record], path: Path, fmt: str) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        payload = [_as_dict(record) for record in records]
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return len(records)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(_COLUMNS))
        writer.writeheader()
        for record in records:
            writer.writerow(_as_dict(record))
    return len(records)


def import_edits(store: Store, path: Path) -> tuple[int, list[str]]:
    rows = _read_rows(path)
    updated = 0
    warnings: list[str] = []
    for row in rows:
        record_id = str(row.get("id") or "").strip()
        if not record_id:
            warnings.append("skipped a row with no id")
            continue
        if "translation" not in row or row.get("translation") is None:
            warnings.append(f"{record_id}: no translation column, skipped")
            continue
        translation = str(row.get("translation") or "")
        status = str(row.get("status") or "reviewed").strip() or "reviewed"
        if status not in _IMPORT_STATUSES:
            warnings.append(f"{record_id}: status {status!r} is not used; marking reviewed")
            status = "reviewed"
        if not store.import_edit(record_id, translation, status):
            warnings.append(f"{record_id}: no matching record")
            continue
        updated += 1
    return updated, warnings


def _as_dict(record: Record) -> dict[str, object]:
    return {
        "id": record.id,
        "kind": record.kind,
        "status": record.status,
        "file": record.file,
        "line": record.line,
        "scene": record.scene or "",
        "speaker": record.speaker or "",
        "source": record.source,
        "translation": record.translation or "",
        "error": record.error or "",
    }


def _read_rows(path: Path) -> list[dict[str, object]]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict) and "records" in data:
            data = data["records"]
        if not isinstance(data, list):
            raise ValueError(f"{path} must be a JSON list of records")
        return [dict(item) for item in data]
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]
