"""Local Ollama chat provider. Game text is sent only to the configured host."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from lantern.models import BatchRequest
from lantern.providers.base import ProviderResponseError, ProviderUnavailable, TranslationProvider

_NAME_SYSTEM = """You translate proper names and short terms.
Reply with JSON only: {"translations":[{"n":1,"translation":"Frieren"}]}
Return one item for every input name, with the same n and the same count.
"""

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


_TRANSLATION_SCHEMA = {
    "type": "object",
    "properties": {
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "translation": {"type": "string"},
                },
                "required": ["n", "translation"],
            },
        }
    },
    "required": ["translations"],
}


def build_chat_payload(model: str, request: BatchRequest) -> dict:
    """Body for Ollama ``POST /api/chat``.

    ``think`` is false because Qwen3 reasons by default. ``format`` is a JSON
    schema so the reply stays a translations array. The parser still accepts
    the other shapes models return when they ignore the schema.
    """

    system = _NAME_SYSTEM if request.lines and request.lines[0].kind == "glossary-name" else _SYSTEM
    return {
        "model": model,
        "stream": False,
        "format": _TRANSLATION_SCHEMA,
        "think": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": build_user_payload(request)},
        ],
        "options": {"temperature": 0},
    }


def reply_snippet(content: str, limit: int = 180) -> str:
    flat = " ".join(content.split())
    if len(flat) > limit:
        return flat[:limit] + "..."
    return flat


def parse_translations(content: str) -> dict[str, str]:
    data = _load_model_json(content)
    output = _coerce_translations(data)
    if not output:
        raise ValueError("JSON has no translations")
    return output


def _load_model_json(content: str) -> object:
    text = strip_think_blocks(content)
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    elif text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = min((index for index in (text.find("{"), text.find("[")) if index >= 0), default=-1)
    if start < 0:
        raise ValueError(f"model did not return JSON; raw: {reply_snippet(content)}")
    try:
        data, _end = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as exc:
        raise ValueError(f"model did not return JSON: {exc}; raw: {reply_snippet(content)}") from exc
    return data


def _coerce_translations(data: object) -> dict[str, str]:
    if isinstance(data, list):
        return _coerce_list(data)
    if not isinstance(data, dict):
        raise ValueError("JSON has no translations list")
    for key in ("translations", "lines", "results"):
        if key in data:
            value = data[key]
            if isinstance(value, list):
                return _coerce_list(value)
            if isinstance(value, dict):
                keyed = _coerce_digit_map(value)
                if keyed is not None:
                    return keyed
    keyed = _coerce_digit_map(data)
    if keyed is not None:
        return keyed
    raise ValueError("JSON has no translations list")


def _coerce_list(items: list) -> dict[str, str]:
    output: dict[str, str] = {}
    for index, item in enumerate(items, start=1):
        if isinstance(item, str):
            output[str(index)] = item
            continue
        if not isinstance(item, dict):
            output[str(index)] = "" if item is None else str(item)
            continue
        key = item.get("id", item.get("n", item.get("index", index)))
        output[str(key)] = _entry_text(item)
    return output


def _coerce_digit_map(data: dict) -> dict[str, str] | None:
    if not data or not all(str(key).isdigit() for key in data):
        return None
    output: dict[str, str] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            output[str(key)] = _entry_text(value)
        else:
            output[str(key)] = "" if value is None else str(value)
    return output


def _entry_text(item: dict) -> str:
    value = item.get("translation", item.get("text", item.get("value", "")))
    if isinstance(value, dict):
        value = value.get("translation", value.get("text", ""))
    return "" if value is None else str(value)


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
            detail = str(exc)
            if "raw:" not in detail:
                detail = f"{detail}; raw: {reply_snippet(message)}"
            raise ProviderResponseError(detail) from exc
