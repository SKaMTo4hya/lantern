"""Provider interface. Implementations return masked translations keyed by id."""

from __future__ import annotations

from abc import ABC, abstractmethod

from lantern.models import BatchRequest


class ProviderError(Exception):
    """Base error for a translation provider."""


class ProviderUnavailable(ProviderError):
    """The model host cannot be used (down, missing model, timeout).

    The job stops after one retry. Lines in the failed batch stay untranslated
    so a later run can resume. They are not marked as bad translations.
    """


class ProviderResponseError(ProviderError):
    """The host answered, but the body was not usable translations."""


class TranslationProvider(ABC):
    name: str
    model: str

    @abstractmethod
    def translate_batch(self, request: BatchRequest) -> dict[str, str]:
        """Return ``{record_id: masked_translation}`` for the request lines.

        Placeholders such as ``⟪TAG000⟫`` must be copied unchanged.
        """
