"""Project glossary of names and terms. Only entries used in a batch are sent."""

from __future__ import annotations

import json
import re
from pathlib import Path

from lantern.models import GlossaryEntry, Record

_PORTRAIT_TAG = re.compile(r"<e\d+>", re.IGNORECASE)
_NAME_BREAK = re.compile(r"[。！？?!，,、]")


def load_glossary(path: Path | None) -> list[GlossaryEntry]:
    if path is None or not path.is_file():
        return []
    raw = path.read_text(encoding="utf-8-sig").strip()
    if not raw:
        return []
    if raw[0] not in "{[":
        return _load_glossary_lines(raw)
    data = json.loads(raw)
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


def _load_glossary_lines(raw: str) -> list[GlossaryEntry]:
    """One ``source=target`` pair per line. ``#`` starts a comment."""

    entries: list[GlossaryEntry] = []
    for line in raw.splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if "=" not in text:
            raise ValueError(f"Glossary line has no '=': {line}")
        source, target = text.split("=", 1)
        source = source.strip()
        target = target.strip()
        if source:
            entries.append(GlossaryEntry(source=source, target=target))
    return entries


def character_base(text: str) -> str:
    """Name without a portrait tag such as ``<e1>``."""

    return _PORTRAIT_TAG.sub("", text or "").strip()


def looks_like_character_name(text: str) -> bool:
    """Short display names, not script variables or whole sentences."""

    base = character_base(text)
    if not base or len(base) > 12 or _NAME_BREAK.search(base):
        return False
    return any("\u3040" <= char <= "\u9fff" for char in base)


def collect_character_names(records: list[Record]) -> list[str]:
    """Actor names and dialogue speakers, in first-seen order."""

    found: list[str] = []
    seen: set[str] = set()

    def add(text: str | None) -> None:
        base = character_base(text or "")
        if not looks_like_character_name(base) or base in seen:
            return
        seen.add(base)
        found.append(base)

    for record in records:
        if record.kind == "name" and _is_character_name_record(record):
            add(record.source)
        if record.speaker:
            add(record.speaker)
    return found


def _is_character_name_record(record: Record) -> bool:
    if record.rebuild.get("command") == 101:
        return True
    pointer = record.rebuild.get("pointer") or []
    return bool(pointer) and record.file.endswith("Actors.json") and pointer[-1] == "name"


def apply_name(source: str, base: str, target: str) -> str:
    """Swap the bare name and keep a portrait tag if the line had one."""

    if source == base:
        return target
    if base and base in source:
        return source.replace(base, target)
    return target


def merge_glossary(pins: list[GlossaryEntry], auto: dict[str, str]) -> list[GlossaryEntry]:
    """User pins win. Auto names fill in everything else."""

    pinned = {entry.source for entry in pins}
    merged = list(pins)
    for source, target in auto.items():
        if source not in pinned:
            merged.append(GlossaryEntry(source=source, target=target, note="character name"))
    return merged
