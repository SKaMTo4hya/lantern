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


_HONORIFICS = (
    "大人",
    "先生",
    "小姐",
    "姑娘",
    "陛下",
    "殿下",
    "前辈",
    "阁下",
    "さん",
    "様",
    "ちゃん",
    "君",
)
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")


def collect_frequent_terms(records: list[Record], *, minimum: int = 4, limit: int = 30) -> list[str]:
    """Repeated short phrases and honorifics, longest phrases first.

    A shorter gram is dropped when a longer gram contains it every time it
    appears. Honorifics such as 大人 stay even inside a longer name.
    """

    counts: dict[str, int] = {}
    honorific_counts = {honorific: 0 for honorific in _HONORIFICS}
    for record in records:
        if record.kind not in {"dialogue", "choice", "description"}:
            continue
        seen_here: set[str] = set()
        for honorific in _HONORIFICS:
            if honorific in record.source:
                honorific_counts[honorific] += 1
        for match in _CJK_RUN.finditer(record.source):
            phrase = match.group(0)
            longest = min(4, len(phrase))
            for size in range(2, longest + 1):
                for start in range(0, len(phrase) - size + 1):
                    gram = phrase[start : start + size]
                    if gram in seen_here:
                        continue
                    seen_here.add(gram)
                    counts[gram] = counts.get(gram, 0) + 1
    chosen = [honorific for honorific in _HONORIFICS if honorific_counts[honorific] >= 2]
    ranked = []
    for gram, count in counts.items():
        if count < minimum or gram in chosen:
            continue
        covered = any(
            len(other) > len(gram) and gram in other and counts[other] >= count for other in counts
        )
        if not covered:
            ranked.append(gram)
    ranked.sort(key=lambda gram: (-len(gram), -counts[gram], gram))
    for gram in ranked:
        if any(gram in other or other in gram for other in chosen):
            continue
        chosen.append(gram)
        if len(chosen) >= limit:
            break
    return chosen[:limit]


def merge_glossary(pins: list[GlossaryEntry], auto: dict[str, str]) -> list[GlossaryEntry]:
    """User pins win. Auto names fill in everything else."""

    pinned = {entry.source for entry in pins}
    merged = list(pins)
    for source, target in auto.items():
        if source not in pinned:
            merged.append(GlossaryEntry(source=source, target=target, note="character name"))
    return merged
