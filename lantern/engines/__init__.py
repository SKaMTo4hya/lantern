"""Engine adapters.

Each adapter detects a game, extracts translation records, and writes a
translated copy in that engine's own format. Ren'Py and RPG Maker MV/MZ
are implemented.
"""

from lantern.engines.base import Detection, EngineAdapter, GamePaths
from lantern.engines.renpy import RenPyAdapter
from lantern.engines.rpgmaker import RpgMakerAdapter

ADAPTERS: list[EngineAdapter] = [RpgMakerAdapter(), RenPyAdapter()]

_UNRECOGNIZED = (
    "Unrecognized game. Lantern reads Ren'Py projects (a game folder of .rpy scripts) "
    "and RPG Maker MV/MZ projects (data/System.json).\n"
    "KiriKiri is next, then msg-tool formats, then Unity."
)


def detect_project(paths: GamePaths) -> Detection:
    """Pick the first adapter that can extract, else the first that recognizes the game."""

    found: list[Detection] = []
    for adapter in ADAPTERS:
        detection = adapter.detect(paths)
        if detection.engine:
            found.append(detection)
    ready = [detection for detection in found if detection.can_extract]
    if ready:
        return ready[0]
    if found:
        return found[0]
    return Detection(engine=None, can_extract=False, summary=_UNRECOGNIZED)


def get_adapter(name: str) -> EngineAdapter:
    for adapter in ADAPTERS:
        if adapter.name == name:
            return adapter
    known = ", ".join(adapter.name for adapter in ADAPTERS) or "(none)"
    raise KeyError(f"No adapter named {name!r}. Available: {known}")


__all__ = [
    "ADAPTERS",
    "Detection",
    "EngineAdapter",
    "RenPyAdapter",
    "RpgMakerAdapter",
    "detect_project",
    "get_adapter",
]
