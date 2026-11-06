"""Local Ollama chat provider. Game text is sent only to the configured host."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from lantern.models import BatchRequest
from lantern.providers.base import ProviderResponseError, ProviderUnavailable, TranslationProvider

_SYSTEM = """You translate visual novel script lines.
Reply with one JSON object and nothing else:
{"translations":[{"n":1,"translation":"..."}]}
Rules:
- n is the line number from the input. It starts at 1.
- Return one item for every input line, and no extras. Keep the same count.
- Translate only "lines". Context is read-only.
- Write the translation in the requested target language.
- Copy every placeholder exactly. Placeholders look like ⟪TAG000⟫, ⟪VAR000⟫, and ⟪FMT000⟫.
- Keep placeholders in the same order. Do not translate, add, drop, or reorder them.
- Keep the same number of line breaks as the masked line.
- When a glossary entry's source text appears, use its target wording, including names.
- Do not add notes or quotation marks that were not in the line.
"""


def build_user_payload(request: BatchRequest) -> str:
    body = {
        "source_language": request.source_language,
        "target_language": request.target_language,
        "glossary": [
            {"source": entry.source, "target": entry.target, "note": entry.note}
            for entry in request.glossary
        ],
        "context": request.context,
        "lines": [
            {
                "n": index,
                "speaker": line.speaker or "",
                "detail": line.detail or "",
                "text": line.masked,
            }
            for index, line in enumerate(request.lines, start=1)
        ],
    }
    if request.extra_instruction:
        body["retry_note"] = request.extra_instruction
    return json.dumps(body, ensure_ascii=False)


_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_THINK_OPEN = re.compile(r"<think>.*\Z", re.IGNORECASE | re.DOTALL)


def strip_think_blocks(content: str) -> str:
    """Drop Qwen-style thinking traces so they cannot sit in front of the JSON."""

    text = _THINK_BLOCK.sub("", content)
    text = _THINK_OPEN.sub("", text)
    return text.strip()


def build_chat_payload(model: str, request: BatchRequest) -> dict:
    """Body for Ollama ``POST /api/chat``.

    ``think`` is false because Qwen3 reasons by default, and that trace makes
    a translation batch too slow to use on an 8 GB GPU.
    """

    return {
        "model": model,
        "stream": False,
        "format": "json",
        "think": False,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": build_user_payload(request)},
        ],
        "options": {"temperature": 0},
    }


def parse_translations(content: str) -> dict[str, str]:
    text = strip_think_blocks(content)
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"model did not return JSON: {exc}") from exc

    if isinstance(data, dict):
        items = data.get("translations") or data.get("lines") or data.get("results")
    elif isinstance(data, list):
        items = data
    else:
        items = None
    if not isinstance(items, list):
        raise ValueError("JSON has no translations list")

    output: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("a translation entry is not an object")
        key = item.get("id", item.get("n"))
        if key is None:
            raise ValueError("a translation entry is missing n")
        value = item.get("translation", item.get("text", ""))
        output[str(key)] = "" if value is None else str(value)
    return output


class OllamaProvider(TranslationProvider):
    name = "ollama"

    def __init__(self, model: str, host: str = "http://127.0.0.1:11434", timeout: float = 180):
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout

    def translate_batch(self, request: BatchRequest) -> dict[str, str]:
        payload = build_chat_payload(self.model, request)
        request_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        http_request = urllib.request.Request(
            self.host + "/api/chat",
            data=request_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise ProviderUnavailable(
                f"Ollama returned HTTP {exc.code} for model {self.model!r} at {self.host}. {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise ProviderUnavailable(
                f"Cannot reach Ollama at {self.host}. Start it and pull the model "
                f"(`ollama pull {self.model}`). {reason}"
            ) from exc

        try:
            message = json.loads(raw)["message"]["content"]
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderResponseError("Ollama response did not include message content") from exc
        try:
            return parse_translations(message)
        except ValueError as exc:
            raise ProviderResponseError(str(exc)) from exc
