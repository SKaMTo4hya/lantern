"""Command line for Lantern.

The original game is only ever read. ``extract`` and ``run`` copy it to ``--out``
and write the database, translations, and ``tl/<language>`` files there.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lantern import __version__
from lantern.engines import detect_project, get_adapter
from lantern.engines.base import GamePaths, resolve_game
from lantern.glossary import load_glossary
from lantern.project import (
    ProjectError,
    clear_previous_build,
    config_path,
    copy_project,
    db_path,
    default_config,
    install_sidecar,
    load_config,
    record_build,
    require_project,
    save_config,
)
from lantern.providers.mock import MockProvider, load_mock_map
from lantern.providers.ollama import OllamaProvider
from lantern.review import export_records, import_edits
from lantern.store import Store
from lantern.translate import TranslationStopped, translate_project

_OVERRIDES = (
    ("language", "language"),
    ("source_language", "source_language"),
    ("provider", "provider"),
    ("model", "model"),
    ("host", "host"),
    ("batch_size", "batch_size"),
    ("context_lines", "context_lines"),
    ("timeout", "timeout"),
)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (ProjectError, ValueError, OSError, UnicodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except TranslationStopped as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("Lines already accepted are saved. Fix the model host and run translate again.", file=sys.stderr)
        return 1
    except BrokenPipeError:
        return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lantern",
        description=(
            "Translate visual novel and RPG Maker scripts with a local model. "
            "The original game is never modified."
        ),
    )
    parser.add_argument("--version", action="version", version=f"lantern {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    detect = sub.add_parser("detect", help="Show which engine adapter matches a game")
    detect.add_argument("game", type=Path)
    detect.set_defaults(func=cmd_detect)

    extract = sub.add_parser("extract", help="Copy a game and store its translatable lines")
    _add_game_out(extract)
    _add_job_options(extract)
    extract.set_defaults(func=cmd_extract)

    translate = sub.add_parser("translate", help="Translate pending lines in a Lantern copy")
    translate.add_argument("project", type=Path, help="Copy created by extract or run")
    _add_job_options(translate)
    translate.add_argument("--force", action="store_true", help="Retranslate lines already marked translated")
    translate.set_defaults(func=cmd_translate)

    build = sub.add_parser("build", help="Write translations into the copy")
    build.add_argument("project", type=Path)
    build.add_argument("--language", default=None)
    build.set_defaults(func=cmd_build)

    export = sub.add_parser("export", help="Write flagged lines to CSV or JSON for review")
    export.add_argument("project", type=Path)
    export.add_argument("-o", "--output", type=Path, required=True)
    export.add_argument("--format", choices=("csv", "json"), default=None, help="Default follows the file suffix")
    export.add_argument("--all", action="store_true", help="Export every line, not only flagged ones")
    export.add_argument("--status", default=None, help="Export one status (untranslated, translated, reviewed, flagged, stale)")
    export.set_defaults(func=cmd_export)

    import_cmd = sub.add_parser("import", help="Load reviewed translations from CSV or JSON")
    import_cmd.add_argument("project", type=Path)
    import_cmd.add_argument("file", type=Path)
    import_cmd.set_defaults(func=cmd_import)

    status = sub.add_parser("status", help="Count lines by status")
    status.add_argument("project", type=Path)
    status.set_defaults(func=cmd_status)

    run = sub.add_parser("run", help="Copy, extract, translate, and build")
    _add_game_out(run)
    _add_job_options(run)
    run.add_argument("--force", action="store_true")
    run.set_defaults(func=cmd_run)
    return parser


def _add_game_out(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("game", type=Path, help="Ren'Py project folder, or its game folder")
    parser.add_argument("--out", type=Path, required=True, help="New folder for the translated copy")


def _add_job_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--language", default=None, help="Target language id, default english")
    parser.add_argument("--source-language", default=None, dest="source_language")
    parser.add_argument("--provider", choices=("ollama", "mock"), default=None)
    parser.add_argument("--model", default=None, help="Ollama model name, default qwen2.5:7b-instruct")
    parser.add_argument("--host", default=None, help="Ollama host, default http://127.0.0.1:11434")
    parser.add_argument("--glossary", type=Path, default=None, help="JSON glossary of names and terms")
    parser.add_argument("--mock-map", type=Path, default=None, dest="mock_map", help="JSON source-to-translation map used by --provider mock")
    parser.add_argument("--batch-size", type=int, default=None, dest="batch_size")
    parser.add_argument("--context", type=int, default=None, dest="context_lines", help="Earlier lines from the same scene sent as context")
    parser.add_argument("--timeout", type=float, default=None, help="Seconds to wait for one Ollama response")


def cmd_detect(args: argparse.Namespace) -> int:
    paths = resolve_game(args.game)
    detection = _detect(paths)
    if detection.engine is None:
        print(detection.summary)
        return 1
    print(f"engine: {detection.engine}")
    print(detection.summary)
    for warning in detection.warnings:
        print(f"warning: {warning}")
    if not detection.can_extract:
        return 2
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    paths, created, code = _open_for_extract(args.game, args.out)
    if paths is None:
        return code
    return _extract(paths, args, fresh=created)


def cmd_translate(args: argparse.Namespace) -> int:
    paths = require_project(args.project)
    config = _apply_overrides(load_config(paths.project), args)
    _attach_sidecars(paths.project, args)
    save_config(paths.project, config)
    stats = _translate(paths, config, force=args.force)
    print(f"result: translated={stats.translated} skipped={stats.skipped} flagged={stats.flagged}")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    paths = require_project(args.project)
    config = load_config(paths.project)
    if args.language:
        config["language"] = args.language
        save_config(paths.project, config)
    written = _build(paths, str(config["language"]))
    if not written:
        print("wrote: (nothing — no accepted translations yet)")
        return 0
    for path in written:
        print(f"wrote: {path.relative_to(paths.project).as_posix()}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    paths = require_project(args.project)
    with Store(db_path(paths.project)) as store:
        records = store.list_records()
    if args.all:
        chosen = records
    elif args.status:
        chosen = [record for record in records if record.status == args.status]
    else:
        chosen = [record for record in records if record.status == "flagged"]
    fmt = args.format or _format_from_suffix(args.output)
    count = export_records(chosen, args.output, fmt)
    print(f"exported: {count} {fmt} -> {args.output}")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    paths = require_project(args.project)
    with Store(db_path(paths.project)) as store:
        updated, warnings = import_edits(store, args.file)
    for warning in warnings:
        print(f"warning: {warning}")
    print(f"imported: {updated}")
    print("Run `lantern build` on this copy to refresh tl files.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    paths = require_project(args.project)
    config = load_config(paths.project)
    with Store(db_path(paths.project)) as store:
        counts = store.counts()
    print(f"project: {paths.project}")
    print(f"language: {config.get('language')}")
    print(f"provider: {config.get('provider')} model: {config.get('model')}")
    for key in ("total", "untranslated", "translated", "reviewed", "flagged", "stale"):
        print(f"{key}: {counts.get(key, 0)}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    paths, created, code = _open_for_extract(args.game, args.out)
    if paths is None:
        return code
    code = _extract(paths, args, fresh=created)
    if code != 0:
        return code
    config = load_config(paths.project)
    stats = _translate(paths, config, force=args.force)
    print(f"result: translated={stats.translated} skipped={stats.skipped} flagged={stats.flagged}")
    written = _build(paths, str(config["language"]))
    for path in written:
        print(f"wrote: {path.relative_to(paths.project).as_posix()}")
    if stats.flagged:
        print("Some lines were flagged. Export them, edit, import, and build again.")
    return 0


def _detect(paths: GamePaths):
    return detect_project(paths)


def _open_for_extract(game: Path, out: Path | None) -> tuple[GamePaths | None, bool, int]:
    source = resolve_game(game)
    detection = _detect(source)
    if detection.engine is None:
        print(detection.summary)
        return None, False, 1
    print(f"engine: {detection.engine}")
    print(detection.summary)
    if not detection.can_extract:
        return None, False, 2
    if out is None:
        if not db_path(source.project).is_file():
            raise ProjectError(
                "Pass --out COPY. Lantern does not extract into the original game folder."
            )
        return source, False, 0
    copy_project(source.project, out)
    print(f"copied: {source.project} -> {out.resolve()}")
    return resolve_game(out), True, 0


def _extract(paths: GamePaths, args: argparse.Namespace, *, fresh: bool) -> int:
    base = default_config() if fresh or not config_path(paths.project).is_file() else load_config(paths.project)
    config = _apply_overrides(base, args)
    detection = _detect(paths)
    if detection.engine:
        config["engine"] = detection.engine
    if config.get("engine") == "rpgmaker":
        from lantern.engines.rpgmaker import ensure_source_snapshot

        ensure_source_snapshot(paths)
    _attach_sidecars(paths.project, args)
    adapter = get_adapter(str(config["engine"]))
    result = adapter.extract(paths)
    with Store(db_path(paths.project)) as store:
        store.replace_extracted(result.records)
        store.set_meta("engine", adapter.name)
        store.set_meta("language", str(config["language"]))
    save_config(paths.project, config)
    for warning in result.warnings:
        print(f"warning: {warning}")
    print(f"extracted: {len(result.files)} files, {len(result.records)} records")
    return 0


def _translate(paths: GamePaths, config: dict[str, object], *, force: bool):
    glossary_path = paths.project / ".lantern" / "glossary.json"
    glossary = load_glossary(glossary_path) if glossary_path.is_file() else []
    provider_name = str(config["provider"])
    if provider_name == "mock":
        map_path = paths.project / ".lantern" / "mock_map.json"
        provider = MockProvider(load_mock_map(map_path if map_path.is_file() else None), model="mock")
    elif provider_name == "ollama":
        provider = OllamaProvider(
            model=str(config["model"]),
            host=str(config["host"]),
            timeout=float(config["timeout"]),
        )
    else:
        raise ProjectError(
            f"Unknown provider {provider_name!r}. Use ollama or mock. "
            "An OpenAI-compatible provider is not included yet."
        )
    with Store(db_path(paths.project)) as store:
        return translate_project(
            paths.project,
            store,
            provider,
            glossary=glossary,
            source_language=str(config["source_language"]),
            target_language=str(config["language"]),
            batch_size=int(config["batch_size"]),
            context_lines=int(config["context_lines"]),
            force=force,
        )


def _build(paths: GamePaths, language: str) -> list[Path]:
    config = load_config(paths.project)
    adapter = get_adapter(str(config.get("engine") or "renpy"))
    clear_previous_build(paths)
    with Store(db_path(paths.project)) as store:
        records = store.list_records()
    written = adapter.build(paths, records, language)
    record_build(paths, written)
    conflicts = paths.project / ".lantern" / "string-conflicts.json"
    if conflicts.is_file():
        print(f"warning: string translations disagree; see {conflicts.relative_to(paths.project).as_posix()}")
    return written


def _apply_overrides(config: dict[str, object], args: argparse.Namespace) -> dict[str, object]:
    updated = dict(config)
    for key, attr in _OVERRIDES:
        value = getattr(args, attr, None)
        if value is not None:
            updated[key] = value
    return updated


def _attach_sidecars(project: Path, args: argparse.Namespace) -> None:
    if getattr(args, "glossary", None):
        install_sidecar(project, "glossary.json", args.glossary)
    if getattr(args, "mock_map", None):
        install_sidecar(project, "mock_map.json", args.mock_map)


def _format_from_suffix(path: Path) -> str:
    if path.suffix.lower() == ".json":
        return "json"
    if path.suffix.lower() == ".csv":
        return "csv"
    raise ProjectError("Pass --format csv or json, or use a .csv or .json file name.")
