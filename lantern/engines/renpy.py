"""Ren'Py adapter.

Reads ``.rpy`` dialogue, menu choices, and ``_()`` strings. Writes
``game/tl/<language>/`` files whose dialogue ids match Ren'Py's translator,
so the game can load them without a patched interpreter.

``.rpyc`` bytecode and ``.rpa`` archives are reported and left untouched.
Decompile or unpack those with the Ren'Py SDK or UnRen, then run Lantern again.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from lantern.engines.base import Detection, ExtractResult, GamePaths
from lantern.engines.renpy_script import collect_explicit_ids, logical_lines, parse_script, read_bytes
from lantern.engines.renpy_text import IdentifierState, quote_unicode, render_say_code
from lantern.models import Record

_RPYC_ONLY = (
    "No .rpy source scripts were found, only compiled .rpyc files.\n"
    "Lantern does not decompile .rpyc bytecode and will not patch those files.\n"
    "Decompile to .rpy first (Ren'Py's unrpyc, or UnRen), then run Lantern on the result."
)

_RPA_ONLY = (
    "No .rpy source scripts were found. Files are packed in .rpa archives.\n"
    "Lantern does not unpack .rpa archives.\n"
    "Unpack the archives, then run Lantern on the extracted .rpy scripts."
)

_RPYC_WARN = (
    "{path}: compiled script has no matching .rpy. "
    "Lantern does not decompile .rpyc. Decompile it if this file contains dialogue."
)

_RPA_WARN = (
    "{path}: Ren'Py archive present. Lantern does not unpack .rpa files. "
    "If this archive contains scripts, unpack it and extract again."
)


def _ignored(rel: str) -> bool:
    parts = rel.split("/")
    return "tl" in parts or "cache" in parts


class RenPyAdapter:
    name = "renpy"

    def detect(self, paths: GamePaths) -> Detection:
        rpy, rpyc, rpa = self._scan(paths.game)
        has_runtime = (paths.project / "renpy").is_dir()
        if not rpy and not rpyc and not rpa and not has_runtime:
            return Detection(engine=None, can_extract=False, summary="")
        if not rpy:
            parts = []
            if rpyc:
                parts.append(_RPYC_ONLY)
            if rpa:
                parts.append(_RPA_ONLY)
            if not parts:
                parts.append("This looks like Ren'Py, but there are no .rpy scripts to read.")
            return Detection(engine="renpy", can_extract=False, summary="\n\n".join(parts))
        warnings = self._companion_warnings(paths.game, rpy, rpyc, rpa)
        summary = f"Ren'Py project with {len(rpy)} .rpy file(s)."
        return Detection(engine="renpy", can_extract=True, summary=summary, warnings=warnings)

    def extract(self, paths: GamePaths) -> ExtractResult:
        detection = self.detect(paths)
        if detection.engine != "renpy":
            raise RuntimeError("not a Ren'Py project")
        if not detection.can_extract:
            raise RuntimeError(detection.summary)
        rpy, _rpyc, _rpa = self._scan(paths.game)
        ids = IdentifierState()
        loaded: list[tuple[str, str, str | None]] = []
        for path in rpy:
            rel = path.relative_to(paths.game).as_posix()
            text, encoding = read_bytes(path.read_bytes())
            loaded.append((rel, text, encoding))
            ids.preexisting.update(collect_explicit_ids(logical_lines(text)))

        records: list[Record] = []
        warnings = list(detection.warnings)
        ordinal = 0
        files: list[str] = []
        for rel, text, encoding in loaded:
            files.append(rel)
            if encoding:
                warnings.append(f"{rel}: decoded as {encoding}")
            found, parse_warnings, ordinal = parse_script(rel, text, ids, ordinal)
            records.extend(found)
            warnings.extend(parse_warnings)
        warnings.extend(_dedupe_ids(records))
        return ExtractResult(records=records, warnings=warnings, files=files)

    def build(self, paths: GamePaths, records: list[Record], language: str) -> list[Path]:
        if not _valid_language(language):
            raise ValueError(
                f"Language {language!r} is not a Ren'Py language name. "
                "Use a Python identifier such as english, french, or zh_hans."
            )
        dialogue: dict[str, list[Record]] = defaultdict(list)
        strings: list[Record] = []
        for record in records:
            if record.engine != "renpy":
                continue
            if record.status not in {"translated", "reviewed"} or not record.translation:
                continue
            if record.kind == "dialogue":
                dialogue[record.file].append(record)
            elif record.kind in {"menu", "string"}:
                strings.append(record)

        written: list[Path] = []
        for rel, group in sorted(dialogue.items()):
            group.sort(key=lambda record: (record.line, record.ordinal))
            target = paths.game / "tl" / language / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_render_dialogue(group, language), encoding="utf-8")
            written.append(target)

        chosen, conflicts = _choose_strings(strings)
        if chosen:
            target = paths.game / "tl" / language / "lantern_strings.rpy"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_render_strings(chosen, language), encoding="utf-8")
            written.append(target)
        conflict_path = paths.project / ".lantern" / "string-conflicts.json"
        if conflicts:
            import json

            conflict_path.parent.mkdir(parents=True, exist_ok=True)
            conflict_path.write_text(json.dumps(conflicts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        elif conflict_path.exists():
            conflict_path.unlink()
        return written

    def _scan(self, game: Path) -> tuple[list[Path], list[Path], list[Path]]:
        rpy: list[Path] = []
        rpyc: list[Path] = []
        rpa: list[Path] = []
        if not game.is_dir():
            return rpy, rpyc, rpa
        for path in game.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(game).as_posix()
            if _ignored(rel):
                continue
            if path.suffix == ".rpy":
                rpy.append(path)
            elif path.suffix == ".rpyc":
                rpyc.append(path)
            elif path.suffix == ".rpa":
                rpa.append(path)
        rpy.sort(key=lambda path: path.relative_to(game).as_posix())
        rpyc.sort(key=lambda path: path.relative_to(game).as_posix())
        rpa.sort(key=lambda path: path.relative_to(game).as_posix())
        return rpy, rpyc, rpa

    def _companion_warnings(self, game: Path, rpy: list[Path], rpyc: list[Path], rpa: list[Path]) -> list[str]:
        stems = {path.relative_to(game).with_suffix("").as_posix() for path in rpy}
        warnings = []
        for path in rpyc:
            stem = path.relative_to(game).with_suffix("").as_posix()
            if stem not in stems:
                warnings.append(_RPYC_WARN.format(path=path.relative_to(game).as_posix()))
        for path in rpa:
            warnings.append(_RPA_WARN.format(path=path.relative_to(game).as_posix()))
        return warnings


def _valid_language(language: str) -> bool:
    if not language or language[0].isdigit():
        return False
    return all(char.isalnum() or char == "_" for char in language)


def _dedupe_ids(records: list[Record]) -> list[str]:
    warnings: list[str] = []
    seen: dict[str, int] = {}
    for record in records:
        count = seen.get(record.id, 0) + 1
        seen[record.id] = count
        if count == 1:
            continue
        new_id = f"{record.id}_{count}"
        warnings.append(
            f"Duplicate translation id {record.id} at {record.file}:{record.line}; stored as {new_id}."
        )
        record.id = new_id
        if record.kind == "dialogue":
            record.rebuild["renpy_id"] = new_id
    return warnings


def _render_dialogue(records: list[Record], language: str) -> str:
    chunks = [
        f"# Lantern dialogue translation ({language}).",
        "# `lantern build` overwrites this file. Review edits belong in the export, then import.",
        "",
    ]
    for record in records:
        rebuild = record.rebuild
        chunks.append(f"# {rebuild.get('comment_file', 'game/' + record.file)}:{record.line}")
        chunks.append(f"translate {language} {rebuild['renpy_id']}:")
        chunks.append("")
        for code in rebuild.get("original_codes", []):
            chunks.append(f"    # {code}")
        for code in _translated_codes(record):
            chunks.append(f"    {code}")
        chunks.append("")
    return "\n".join(chunks)


def _translated_codes(record: Record) -> list[str]:
    codes: list[str] = []
    what = record.translation if record.translation is not None else record.source
    for statement in record.rebuild.get("statements", []):
        if statement.get("type") == "voice":
            codes.append(statement["code"])
            continue
        codes.append(
            render_say_code(
                who=statement.get("who"),
                attributes=list(statement.get("attributes") or []),
                temporary_attributes=list(statement.get("temporary_attributes") or []),
                what=what,
                interact=bool(statement.get("interact", True)),
                explicit_id=statement.get("explicit_id"),
                arguments=statement.get("arguments"),
                with_=statement.get("with"),
            )
        )
    return codes


def _choose_strings(records: list[Record]) -> tuple[list[Record], list[dict[str, object]]]:
    """Ren'Py string translation is one replacement per source string, game-wide."""

    groups: dict[str, list[Record]] = defaultdict(list)
    for record in records:
        groups[record.source].append(record)
    chosen: list[Record] = []
    conflicts: list[dict[str, object]] = []
    for source, group in groups.items():
        group.sort(key=lambda record: record.ordinal)
        reviewed = [record for record in group if record.status == "reviewed"]
        pool = reviewed or group
        translations = {record.translation for record in pool}
        if len(translations) > 1:
            conflicts.append(
                {
                    "source": source,
                    "chosen": pool[0].translation,
                    "values": sorted(str(value) for value in translations),
                    "ids": [record.id for record in group],
                }
            )
        chosen.append(pool[0])
    chosen.sort(key=lambda record: (record.file, record.line, record.ordinal))
    return chosen, conflicts


def _render_strings(records: list[Record], language: str) -> str:
    lines = [
        f"# Lantern string translations ({language}).",
        "# Menu choices and _() strings. One old/new pair per distinct source string.",
        "",
        f"translate {language} strings:",
        "",
    ]
    for record in records:
        comment = record.rebuild.get("comment_file", f"game/{record.file}")
        lines.append(f"    # {comment}:{record.line}")
        lines.append(f'    old "{quote_unicode(record.source)}"')
        lines.append(f'    new "{quote_unicode(record.translation or "")}"')
        lines.append("")
    return "\n".join(lines)
