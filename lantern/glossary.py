"""Project glossary of names and terms. Only entries used in a batch are sent."""

from __future__ import annotations

import json
from pathlib import Path

from lantern.models import GlossaryEntry


def load_glossary(path: Path | None) -> list[GlossaryEntry]:
    if path is None:
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "entries" in data:
        items = data["entries"]
    elif isinstance(data, dict):
        return [GlossaryEntry(source=str(key), target=str(value)) for key, value in data.items()]
    else:
        items = data
    entries: list[GlossaryEntry] = []
    for item in items:
        entries.append(
            GlossaryEntry(
                source=str(item["source"]),
                target=str(item["target"]),
                note=str(item.get("note") or ""),
            )
        )
    return entries


def select_glossary(entries: list[GlossaryEntry], texts: list[str]) -> list[GlossaryEntry]:
    """Keep terms that actually occur in this batch, longest source first."""

    blob = "\n".join(texts)
    chosen = [entry for entry in entries if entry.source and entry.source in blob]
    chosen.sort(key=lambda entry: len(entry.source), reverse=True)
    return chosen
