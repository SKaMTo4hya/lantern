"""Batch translation with a scene context window, glossary, and one retry.

Successful lines are stored immediately. A later failure does not send them again.
Only the lines or the batch that failed are retried, and only once.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from lantern.glossary import (
    apply_name,
    collect_character_names,
    merge_glossary,
    select_glossary,
)
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
    dialogue_total: int = 0
    dialogue_selected: int | None = None
    names_translated: int = 0
    seconds: float = 0.0


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
    limit: int | None = None,
    workers: int = 1,
) -> TranslateStats:
    if batch_size < 1:
        raise ValueError("batch size must be at least 1")
    if limit is not None and limit < 1:
        raise ValueError("limit must be at least 1")
    if workers < 1:
        raise ValueError("workers must be at least 1")
    records = store.list_records()
    dialogue = [record for record in records if record.kind == "dialogue"]
    selected = records
    dialogue_selected: int | None = None
    if limit is not None:
        chosen = dialogue[:limit]
        allowed = {record.id for record in chosen}
        selected = [record for record in records if record.id in allowed]
        dialogue_selected = len(chosen)
    import time

    started = time.perf_counter()
    already_done = [record for record in selected if not _needs_work(record, force)]
    glossary, name_count = _seed_character_names(
        project,
        store,
        provider,
        records,
        glossary,
        source_language=source_language,
        target_language=target_language,
        batch_size=batch_size,
        force=force,
    )
    pending = [record for record in selected if _needs_work(record, force)]
    stats = TranslateStats(
        translated=name_count,
        skipped=len(already_done),
        dialogue_total=len(dialogue),
        dialogue_selected=dialogue_selected,
        names_translated=name_count,
    )
    failure_log = project / ".lantern" / "failures.jsonl"
    failure_log.parent.mkdir(parents=True, exist_ok=True)
    failure_log.write_text("", encoding="utf-8")

    reject_echo = target_language.lower() not in _KEEP_SOURCE_SCRIPT
    chains = _scene_chains(_batches(pending, batch_size))
    if workers == 1 or len(chains) <= 1:
        for chain in chains:
            _run_chain(
                store,
                provider,
                records,
                chain,
                glossary,
                source_language,
                target_language,
                reject_echo,
                context_lines,
                stats,
                failure_log,
            )
    else:
        _run_chains_concurrent(
            store,
            provider,
            records,
            chains,
            glossary,
            source_language,
            target_language,
            reject_echo,
            context_lines,
            stats,
            failure_log,
            workers,
        )
    stats.seconds = time.perf_counter() - started
    return stats


def _seed_character_names(
    project: Path,
    store: Store,
    provider: TranslationProvider,
    records: list[Record],
    pins: list[GlossaryEntry],
    *,
    source_language: str,
    target_language: str,
    batch_size: int,
    force: bool,
) -> tuple[list[GlossaryEntry], int]:
    """Translate each character name once, then reuse that wording everywhere.

    Entries in the user's glossary file are pins. They are not sent to the model.
    """

    names = collect_character_names(records)
    if not names:
        return pins, 0
    pinned = {entry.source: entry.target for entry in pins}
    cache_path = project / ".lantern" / "auto-glossary.json"
    cached: dict[str, str] = {}
    if cache_path.is_file() and not force:
        loaded = json.loads(cache_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            cached = {str(key): str(value) for key, value in loaded.items()}
    resolved = {name: pinned[name] for name in names if name in pinned}
    resolved.update({name: cached[name] for name in names if name in cached and name not in resolved})
    missing = [name for name in names if name not in resolved]
    if missing:
        engine = records[0].engine if records else "renpy"
        synthetic = [
            Record(
                id=f"glossary-name:{index}",
                engine=engine,
                kind="glossary-name",
                source=name,
                translation=None,
                speaker=None,
                file="",
                scene="names",
                line=index,
                source_hash="",
                status="untranslated",
                ordinal=index,
            )
            for index, name in enumerate(missing)
        ]
        reject_echo = target_language.lower() not in _KEEP_SOURCE_SCRIPT
        for batch in _batches(synthetic, min(batch_size, 40)):
            outcomes = _resolve(
                provider,
                batch,
                [],
                pins,
                source_language,
                target_language,
                reject_echo,
                "",
            )
            for record in batch:
                outcome = outcomes.get(record.id)
                if outcome and outcome.ok and outcome.translation:
                    resolved[record.source] = outcome.translation
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(resolved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    written = _apply_name_records(store, provider, records, resolved, force)
    return merge_glossary(pins, resolved), written


def _apply_name_records(
    store: Store,
    provider: TranslationProvider,
    records: list[Record],
    names: dict[str, str],
    force: bool,
) -> int:
    from lantern.glossary import character_base

    written = 0
    for record in records:
        if not _needs_name_update(record):
            continue
        key = character_base(record.source)
        if key not in names:
            continue
        if not force and record.status in {"translated", "reviewed"} and record.translation:
            continue
        translation = apply_name(record.source, key, names[key])
        record.translation = translation
        record.status = "translated"
        record.error = None
        record.provider = provider.name
        record.model = provider.model
        store.save_result(
            record.id,
            translation=translation,
            status="translated",
            error=None,
            provider=provider.name,
            model=provider.model,
        )
        written += 1
    return written


def _needs_name_update(record: Record) -> bool:
    from lantern.glossary import _is_character_name_record

    return _is_character_name_record(record)


def _scene_chains(batches: list[list[Record]]) -> list[list[list[Record]]]:
    chains: list[list[list[Record]]] = []
    current: list[list[Record]] = []
    current_key: tuple[str, str] | None = None
    for batch in batches:
        key = batch[0].scene_key()
        if current and key != current_key:
            chains.append(current)
            current = []
        current_key = key
        current.append(batch)
    if current:
        chains.append(current)
    return chains


def _run_chain(
    store: Store,
    provider: TranslationProvider,
    records: list[Record],
    chain: list[list[Record]],
    glossary: list,
    source_language: str,
    target_language: str,
    reject_echo: bool,
    context_lines: int,
    stats: TranslateStats,
    failure_log: Path,
) -> None:
    for batch in chain:
        context = _context(records, batch, context_lines)
        outcomes = _resolve(
            provider, batch, context, glossary, source_language, target_language, reject_echo, ""
        )
        _remember(batch, outcomes)
        _commit_outcomes(store, provider, batch, outcomes, stats, failure_log)


def _run_chains_concurrent(
    store: Store,
    provider: TranslationProvider,
    records: list[Record],
    chains: list[list[list[Record]]],
    glossary: list,
    source_language: str,
    target_language: str,
    reject_echo: bool,
    context_lines: int,
    stats: TranslateStats,
    failure_log: Path,
    workers: int,
) -> None:
    stopped: TranslationStopped | None = None

    def run(chain: list[list[Record]]) -> list[tuple[list[Record], dict[str, _Outcome]]]:
        finished: list[tuple[list[Record], dict[str, _Outcome]]] = []
        for batch in chain:
            context = _context(records, batch, context_lines)
            outcomes = _resolve(
                provider, batch, context, glossary, source_language, target_language, reject_echo, ""
            )
            _remember(batch, outcomes)
            finished.append((batch, outcomes))
        return finished

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run, chain) for chain in chains]
        for future in as_completed(futures):
            try:
                finished = future.result()
            except TranslationStopped as exc:
                stopped = exc
                continue
            for batch, outcomes in finished:
                _commit_outcomes(store, provider, batch, outcomes, stats, failure_log)
    if stopped is not None:
        raise stopped


def _remember(batch: list[Record], outcomes: dict[str, _Outcome]) -> None:
    for record in batch:
        outcome = outcomes.get(record.id)
        if outcome and outcome.ok and outcome.translation is not None:
            record.translation = outcome.translation
            record.status = "translated"


def _resolve(
    provider: TranslationProvider,
    records: list[Record],
    context: list[dict[str, str]],
    glossary: list,
    source_language: str,
    target_language: str,
    reject_echo: bool,
    note: str,
    *,
    single_retry: bool = True,
) -> dict[str, _Outcome]:
    """Translate a batch. On a count or placeholder mismatch, try smaller pieces."""

    if not records:
        return {}
    try:
        result = _call(
            provider,
            records,
            context,
            glossary,
            source_language,
            target_language,
            reject_echo,
            note,
            retry_response=len(records) == 1,
        )
    except ProviderUnavailable as exc:
        raise TranslationStopped(str(exc)) from exc
    if isinstance(result, ProviderResponseError):
        if len(records) == 1:
            return {records[0].id: _Outcome(False, None, str(result))}
        return _split(provider, records, context, glossary, source_language, target_language, reject_echo, note)
    failed = [record for record in records if not result[record.id].ok]
    good = {record.id: result[record.id] for record in records if result[record.id].ok}
    if not failed:
        return good
    if len(records) == 1:
        if not single_retry:
            return result
        return _resolve(
            provider,
            records,
            context,
            glossary,
            source_language,
            target_language,
            reject_echo,
            "Previous attempt failed checks. " + (result[records[0].id].error or "validation failed"),
            single_retry=False,
        )
    if len(failed) == len(records):
        return _split(provider, records, context, glossary, source_language, target_language, reject_echo, note)
    smaller = _resolve(
        provider,
        failed,
        context,
        glossary,
        source_language,
        target_language,
        reject_echo,
        "Previous attempt failed checks.",
        single_retry=False,
    )
    good.update(smaller)
    return good


def _split(
    provider: TranslationProvider,
    records: list[Record],
    context: list[dict[str, str]],
    glossary: list,
    source_language: str,
    target_language: str,
    reject_echo: bool,
    note: str,
) -> dict[str, _Outcome]:
    mid = max(1, len(records) // 2)
    if mid >= len(records):
        mid = len(records) - 1
    left = _resolve(provider, records[:mid], context, glossary, source_language, target_language, reject_echo, note)
    right = _resolve(provider, records[mid:], context, glossary, source_language, target_language, reject_echo, note)
    left.update(right)
    return left


def _commit_outcomes(
    store: Store,
    provider: TranslationProvider,
    records: list[Record],
    outcomes: dict[str, _Outcome],
    stats: TranslateStats,
    failure_log: Path,
) -> None:
    for record in records:
        outcome = outcomes.get(record.id) or _Outcome(False, None, "missing result")
        if outcome.ok and outcome.translation is not None:
            _apply(store, provider, [record], {record.id: outcome}, stats, failure_log)
        else:
            _flag(store, provider, record, outcome.error or "validation failed", stats, failure_log)


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
    retry_response: bool = True,
) -> dict[str, _Outcome] | ProviderResponseError:
    """Run one batch. A single-line format failure is retried once. The host is too."""

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
    except ProviderResponseError as exc:
        if not retry_response:
            return exc
        try:
            return attempt()
        except ProviderUnavailable:
            raise
        except ProviderResponseError as second:
            return second


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
    chosen = select_glossary(
        glossary,
        [*(record.source for record in records), *(record.speaker or "" for record in records)],
    )
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
    for index, record in enumerate(records, start=1):
        if record.id in raw:
            translated = raw[record.id]
        elif str(index) in raw:
            translated = raw[str(index)]
        else:
            outcomes[record.id] = _Outcome(False, None, "response omitted this line")
            continue
        checked = validate_and_restore(
            record.source,
            translated,
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
