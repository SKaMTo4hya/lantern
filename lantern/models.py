"""Translation records shared by every engine adapter."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any


STATUSES = ("untranslated", "translated", "reviewed", "flagged", "stale")


def source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Record:
    """One translatable line, keyed by a stable engine id rather than source text.

    The same Japanese sentence may be translated differently in two scenes.
    ``id`` carries file and location identity; ``source_hash`` detects edits.
    """

    id: str
    engine: str
    kind: str
    source: str
    translation: str | None
    speaker: str | None
    file: str
    scene: str | None
    line: int
    source_hash: str
    status: str
    ordinal: int
    rebuild: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    provider: str | None = None
    model: str | None = None

    def scene_key(self) -> tuple[str, str]:
        return (self.file, self.scene or "")


@dataclass
class GlossaryEntry:
    source: str
    target: str
    note: str = ""


@dataclass
class PromptLine:
    id: str
    kind: str
    speaker: str | None
    source: str
    masked: str


@dataclass
class BatchRequest:
    lines: list[PromptLine]
    context: list[dict[str, str]]
    glossary: list[GlossaryEntry]
    source_language: str
    target_language: str
    extra_instruction: str = ""
