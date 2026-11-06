"""Batch translation with a scene context window, glossary, and one retry.

Successful lines are stored immediately. A later failure does not send them again.
Only the lines or the batch that failed are retried, and only once.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from lantern.glossary import select_glossary
from lantern.mask import mask_text, validate_and_restore
from lantern.models import BatchRequest, GlossaryEntry, PromptLine, Record
from lantern.providers.base import ProviderResponseError, ProviderUnavailable, TranslationProvider
from lantern.store import Store

# Targets that are allowed to keep CJK characters without counting as an echo.
_KEEP_SOURCE_SCRIPT = {
    "japanese",
    "ja",
    "chinese",
    "zh",
    "zh_hans",
    "zh_hant",
    "schinese",
    "tchinese",
    "chinese_s",
    "chinese_t",
}

_LANGUAGE_NAMES = {
    "english": "English",
    "french": "French",
    "german": "German",
    "spanish": "Spanish",
    "portuguese": "Portuguese",
    "italian": "Italian",
    "russian": "Russian",
    "korean": "Korean",
    "chinese": "Chinese",
    "japanese": "Japanese",
    "zh_hans": "Simplified Chinese",
    "zh_hant": "Traditional Chinese",
}


@dataclass
class TranslateStats:
    translated: int = 0
    skipped: int = 0
    flagged: int = 0


@dataclass
class _Outcome:
    ok: bool
    translation: str | None
    error: str | None


class TranslationStopped(Exception):
    """The model host stayed unreachable. Completed lines are already saved."""


def language_name(code: str) -> str:
    return _LANGUAGE_NAMES.get(code.lower(), code)


def translate_project(
    project: Path,
    store: Store,
    provider: TranslationProvider,
    *,
    glossary: list[GlossaryEntry],
    source_language: str,
    target_language: str,
    batch_size: int,
    context_lines: int,
    force: bool = False,
) -> TranslateStats:
    if batch_size < 1:
        raise ValueError("batch size must be at least 1")
    records = store.list_records()
    pending = [record for record in records if _needs_work(record, force)]
    stats = TranslateStats(skipped=len(records) - len(pending))
    failure_log = project / ".lantern" / "failures.jsonl"
    failure_log.parent.mkdir(parents=True, exist_ok=True)
    failure_log.write_text("", encoding="utf-8")

    reject_echo = target_language.lower() not in _KEEP_SOURCE_SCRIPT
    for batch in _batches(pending, batch_size):
        context = _context(records, batch, context_lines)
        try:
            outcomes = _call(provider, batch, context, glossary, source_language, target_language, reject_echo, "")
        except ProviderUnavailable as exc:
            raise TranslationStopped(str(exc)) from exc
        if isinstance(outcomes, ProviderResponseError):
            _flag_many(store, provider, batch, str(outcomes), stats, failure_log)
            continue
        _apply(store, provider, batch, outcomes, stats, failure_log)
        failed = [record for record in batch if not outcomes[record.id].ok]
        if not failed:
            continue
        note = "Previous attempt failed checks. " + "; ".join(
            f"{record.id}: {outcomes[record.id].error}" for record in failed
        )
        try:
            second = _call(
                provider, failed, context, glossary, source_language, target_language, reject_echo, note
            )
        except ProviderUnavailable as exc:
            raise TranslationStopped(str(exc)) from exc
        if isinstance(second, ProviderResponseError):
            _flag_many(store, provider, failed, str(second), stats, failure_log)
            continue
        _apply(store, provider, failed, second, stats, failure_log)
        for record in failed:
            if not second[record.id].ok:
                _flag(store, provider, record, second[record.id].error or "validation failed", stats, failure_log)
    return stats


def _mask_profile(record: Record) -> str:
    if record.engine == "rpgmaker":
        return "rpgmaker"
    return "renpy"


def _line_detail(record: Record) -> str | None:
    parts: list[str] = []
    face = record.rebuild.get("face")
    if face:
        parts.append(f"face {face}")
    command = record.rebuild.get("command")
    if command:
        parts.append(f"command {command}")
    return ", ".join(parts) or None


def _needs_work(record: Record, force: bool) -> bool:
    if record.status == "reviewed":
        return False
    if record.status in {"untranslated", "stale", "flagged"}:
        return True
    return force and record.status == "translated"


def _batches(records: list[Record], batch_size: int) -> list[list[Record]]:
    batches: list[list[Record]] = []
    current: list[Record] = []
    current_key: tuple[str, str] | None = None
    for record in records:
        key = record.scene_key()
        if current and (key != current_key or len(current) >= batch_size):
            batches.append(current)
            current = []
        current_key = key
        current.append(record)
    if current:
        batches.append(current)
    return batches


def _context(records: list[Record], batch: list[Record], limit: int) -> list[dict[str, str]]:
    if limit <= 0 or not batch:
        return []
    key = batch[0].scene_key()
    first = min(record.ordinal for record in batch)
    prior = [record for record in records if record.scene_key() == key and record.ordinal < first]
    prior.sort(key=lambda record: record.ordinal)
    window = prior[-limit:]
    return [
        {
            "speaker": record.speaker or "",
            "source": record.source,
            "translation": record.translation or "",
        }
        for record in window
    ]


def _call(
    provider: TranslationProvider,
    records: list[Record],
    context: list[dict[str, str]],
    glossary: list[GlossaryEntry],
    source_language: str,
    target_language: str,
    reject_echo: bool,
    note: str,
) -> dict[str, _Outcome] | ProviderResponseError:
    """Run one batch. Retry that same batch once if the call itself fails."""

    def attempt() -> dict[str, _Outcome]:
        return _once(
            provider, records, context, glossary, source_language, target_language, reject_echo, note
        )

    try:
        return attempt()
    except ProviderUnavailable:
        try:
            return attempt()
        except ProviderResponseError as exc:
            return exc
    except ProviderResponseError:
        try:
            return attempt()
        except ProviderUnavailable:
            raise
        except ProviderResponseError as exc:
            return exc


def _once(
    provider: TranslationProvider,
    records: list[Record],
    context: list[dict[str, str]],
    glossary: list[GlossaryEntry],
    source_language: str,
    target_language: str,
    reject_echo: bool,
    note: str,
) -> dict[str, _Outcome]:
    chosen = select_glossary(glossary, [record.source for record in records])
    lines = []
    for record in records:
        profile = _mask_profile(record)
        masked = mask_text(record.source, profile)
        lines.append(
            PromptLine(
                id=record.id,
                kind=record.kind,
                speaker=record.speaker,
                source=record.source,
                masked=masked.text,
                detail=_line_detail(record),
            )
        )
    request = BatchRequest(
        lines=lines,
        context=context,
        glossary=chosen,
        source_language=language_name(source_language),
        target_language=language_name(target_language),
        extra_instruction=note,
        profile=_mask_profile(records[0]) if records else "renpy",
    )
    raw = provider.translate_batch(request)
    outcomes: dict[str, _Outcome] = {}
    for record in records:
        if record.id not in raw:
            outcomes[record.id] = _Outcome(False, None, "response omitted this id")
            continue
        checked = validate_and_restore(
            record.source,
            raw[record.id],
            reject_unchanged_cjk=reject_echo,
            profile=_mask_profile(record),
        )
        if checked.ok:
            outcomes[record.id] = _Outcome(True, checked.text, None)
        else:
            outcomes[record.id] = _Outcome(False, None, checked.error)
    return outcomes


def _apply(
    store: Store,
    provider: TranslationProvider,
    records: list[Record],
    outcomes: dict[str, _Outcome],
    stats: TranslateStats,
    failure_log: Path,
) -> None:
    for record in records:
        outcome = outcomes[record.id]
        if not outcome.ok or outcome.translation is None:
            continue
        record.translation = outcome.translation
        record.status = "translated"
        record.error = None
        record.provider = provider.name
        record.model = provider.model
        store.save_result(
            record.id,
            translation=outcome.translation,
            status="translated",
            error=None,
            provider=provider.name,
            model=provider.model,
        )
        stats.translated += 1


def _flag_many(
    store: Store,
    provider: TranslationProvider,
    records: list[Record],
    error: str,
    stats: TranslateStats,
    failure_log: Path,
) -> None:
    for record in records:
        _flag(store, provider, record, error, stats, failure_log)


def _flag(
    store: Store,
    provider: TranslationProvider,
    record: Record,
    error: str,
    stats: TranslateStats,
    failure_log: Path,
) -> None:
    record.status = "flagged"
    record.error = error
    record.provider = provider.name
    record.model = provider.model
    store.save_result(
        record.id,
        translation=record.translation,
        status="flagged",
        error=error,
        provider=provider.name,
        model=provider.model,
    )
    stats.flagged += 1
    with failure_log.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {"id": record.id, "file": record.file, "line": record.line, "error": error, "model": provider.model},
                ensure_ascii=False,
            )
            + "\n"
        )
