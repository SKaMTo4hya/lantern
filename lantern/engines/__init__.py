"""Engine adapters.

Each adapter detects a game, extracts translation records, and writes a
translated copy in that engine's own format. The MVP ships Ren'Py only.
"""

from lantern.engines.base import Detection, EngineAdapter
from lantern.engines.renpy import RenPyAdapter

ADAPTERS: list[EngineAdapter] = [RenPyAdapter()]


def get_adapter(name: str) -> EngineAdapter:
    for adapter in ADAPTERS:
        if adapter.name == name:
            return adapter
    known = ", ".join(adapter.name for adapter in ADAPTERS) or "(none)"
    raise KeyError(f"No adapter named {name!r}. Available: {known}")


__all__ = ["ADAPTERS", "Detection", "EngineAdapter", "RenPyAdapter", "get_adapter"]
