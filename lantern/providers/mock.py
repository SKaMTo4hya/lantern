"""Deterministic provider for tests and for trying the CLI without a model.

Translations come from a JSON object of source text to target text. Tags in
the mapped sentence are masked again so the real restore path still runs.
Unmapped lines are echoed, which the validator then flags when they are Japanese.
"""

from __future__ import annotations

import json
from pathlib import Path

from lantern.mask import mask_text
from lantern.models import BatchRequest
from lantern.providers.base import TranslationProvider


class MockProvider(TranslationProvider):
    name = "mock"

    def __init__(self, mapping: dict[str, str] | None = None, model: str = "mock"):
        self.mapping = dict(mapping or {})
        self.model = model
        self.calls: list[list[str]] = []

    def translate_batch(self, request: BatchRequest) -> dict[str, str]:
        self.calls.append([line.id for line in request.lines])
        output: dict[str, str] = {}
        for line in request.lines:
            english = self.mapping.get(line.source)
            if english is None:
                output[line.id] = line.masked
            else:
                output[line.id] = mask_text(english, request.profile).text
        return output


def load_mock_map(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must be a JSON object of source text to translation")
    return {str(key): str(value) for key, value in data.items()}
