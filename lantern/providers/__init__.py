"""Translation providers.

Ollama is the default. The base class is the place an OpenAI-compatible
provider would plug in later; that adapter is not implemented.
"""

from lantern.providers.base import ProviderResponseError, ProviderUnavailable, TranslationProvider
from lantern.providers.mock import MockProvider
from lantern.providers.ollama import OllamaProvider

__all__ = [
    "MockProvider",
    "OllamaProvider",
    "ProviderResponseError",
    "ProviderUnavailable",
    "TranslationProvider",
]
