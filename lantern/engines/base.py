"""Adapter interface. Engine-specific parsing stays behind this."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from lantern.models import Record


@dataclass
class GamePaths:
    """``project`` is the folder that contains ``game/``. ``game`` is that child."""

    project: Path
    game: Path


@dataclass
class Detection:
    engine: str | None
    can_extract: bool
    summary: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class ExtractResult:
    records: list[Record]
    warnings: list[str]
    files: list[str]


class EngineAdapter(Protocol):
    name: str

    def detect(self, paths: GamePaths) -> Detection:
        """Return a detection with engine=None when this adapter does not match."""

    def extract(self, paths: GamePaths) -> ExtractResult:
        """Read a copy of the game and return records. Do not write translations."""

    def build(self, paths: GamePaths, records: list[Record], language: str) -> list[Path]:
        """Write translated assets into the copy. Return the files written."""


def resolve_game(path: Path) -> GamePaths:
    """Accept either the project folder or the ``game`` folder inside it."""

    path = path.expanduser().resolve()
    if (path / "game").is_dir():
        return GamePaths(project=path, game=path / "game")
    if path.name == "game" and path.is_dir():
        return GamePaths(project=path.parent, game=path)
    return GamePaths(project=path, game=path / "game")
