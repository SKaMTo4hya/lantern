"""RPG Maker MV and MZ adapter.

Extracts Show Text (401), scrolling text (405), choices (102), the speaker
and face on the preceding Show Text command (101), database names and
descriptions, and System terms. The build patches those strings in a copy
of ``data/*.json``.

Hakubox_Translate, when present, is a flat source-to-translation dictionary.
It looks up the whole joined message and cannot represent two translations of
the same line. Patching the event parameter is what actually changes that
line, and Hakubox then leaves the new text alone because the lookup misses.
The Hakubox JSON files are not rewritten.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from lantern.engines.base import Detection, ExtractResult, GamePaths
from lantern.models import Record, source_hash

_DATABASE_FIELDS: dict[str, tuple[str, ...]] = {
    "Actors.json": ("name", "nickname", "profile"),
    "Classes.json": ("name",),
    "Skills.json": ("name", "description", "message1", "message2"),
    "Items.json": ("name", "description"),
    "Weapons.json": ("name", "description"),
    "Armors.json": ("name", "description"),
    "States.json": ("name", "message1", "message2", "message3", "message4"),
    "Enemies.json": ("name",),
}

_KIND_FOR_FIELD = {
    "name": "name",
    "nickname": "name",
    "profile": "description",
    "description": "description",
    "message1": "description",
    "message2": "description",
    "message3": "description",
    "message4": "description",
}

_HAKUBOX_NOTE = (
    "Hakubox_Translate is installed. Lantern patches data JSON in the copy and "
    "does not rewrite Hakubox-Translate-*.json. That plugin maps each exact "
    "source string to one translation and looks up a whole message at once, so "
    "a dictionary cannot keep the speaker, the face, or a different wording in "
    "another scene. After the patch, Hakubox does not find the new text and "
    "leaves it as written. This copy is a single-language build."
)

_VISIBLE = re.compile(r"\\[A-Za-z]+\[[^\]]*\]|\\[{}.$|^!><\\GgNn]|%\d+|<e\d+>")


class RpgMakerAdapter:
    name = "rpgmaker"

    def detect(self, paths: GamePaths) -> Detection:
        system = paths.game / "data" / "System.json"
        if not system.is_file():
            return Detection(engine=None, can_extract=False, summary="")
        version = _version(paths)
        hakubox = _hakubox_present(paths)
        summary = f"RPG Maker {version.upper()} project ({system.parent.as_posix()})."
        warnings = [_HAKUBOX_NOTE] if hakubox else []
        if hakubox:
            summary += " Hakubox_Translate is present; output is a patched data folder, not its dictionary."
        return Detection(engine="rpgmaker", can_extract=True, summary=summary, warnings=warnings)

    def extract(self, paths: GamePaths) -> ExtractResult:
        detection = self.detect(paths)
        if detection.engine != "rpgmaker":
            raise RuntimeError("not an RPG Maker MV/MZ project")
        if not detection.can_extract:
            raise RuntimeError(detection.summary)
        data_dir = source_data_dir(paths)
        records: list[Record] = []
        warnings = list(detection.warnings)
        files: list[str] = []
        ordinal = 0
        for path in _data_files(data_dir):
            rel = f"data/{path.name}"
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                warnings.append(f"{rel}: skipped ({exc})")
                continue
            found = _extract_file(rel, payload, ordinal)
            if not found and path.name not in {"MapInfos.json"}:
                # Still count maps that only contain tile data.
                pass
            if found or path.name.startswith("Map") or path.name in _DATABASE_FIELDS or path.name in {
                "System.json",
                "CommonEvents.json",
                "Troops.json",
                "MapInfos.json",
            }:
                files.append(rel)
            records.extend(found)
            ordinal += len(found)
        return ExtractResult(records=records, warnings=warnings, files=files)

    def build(self, paths: GamePaths, records: list[Record], language: str) -> list[Path]:
        del language  # The patched copy is one language. Hakubox locale codes are not used.
        accepted = [
            record
            for record in records
            if record.engine == "rpgmaker"
            and record.status in {"translated", "reviewed"}
            and record.translation
        ]
        by_file: dict[str, list[Record]] = {}
        for record in accepted:
            by_file.setdefault(record.file, []).append(record)
        written: list[Path] = []
        for rel, group in sorted(by_file.items()):
            target = paths.game / rel
            if not target.is_file():
                continue
            original = target.read_text(encoding="utf-8-sig")
            payload = json.loads(original)
            changed = False
            for record in group:
                if _assign(payload, record.rebuild.get("pointer") or [], record.translation):
                    changed = True
                for mirror in record.rebuild.get("mirrors") or []:
                    if _assign(payload, mirror, record.translation):
                        changed = True
            if not changed:
                continue
            text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            if original.endswith("\n"):
                text += "\n"
            target.write_text(text, encoding="utf-8")
            written.append(target)
        return written


def ensure_source_snapshot(paths: GamePaths) -> Path:
    """Keep the untranslated data JSON beside the project database.

    Build rewrites ``data/`` in the copy. Later extracts read this snapshot so
    a rebuilt copy is not treated as the source language.
    """

    dest = paths.project / ".lantern" / "source" / "data"
    if (dest / "System.json").is_file():
        return dest
    source = paths.game / "data"
    if not (source / "System.json").is_file():
        raise FileNotFoundError(f"No RPG Maker data folder at {source}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, dest)
    return dest


def source_data_dir(paths: GamePaths) -> Path:
    snap = paths.project / ".lantern" / "source" / "data"
    if (snap / "System.json").is_file():
        return snap
    return paths.game / "data"


def _version(paths: GamePaths) -> str:
    if (paths.game / "js" / "rmmz_core.js").is_file() or (paths.game / "js" / "rmmz_objects.js").is_file():
        return "mz"
    if (paths.game / "js" / "rpg_core.js").is_file():
        return "mv"
    system = paths.game / "data" / "System.json"
    try:
        payload = json.loads(system.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "mz"
    if isinstance(payload, dict) and "advanced" in payload:
        return "mz"
    return "mv"


def _hakubox_present(paths: GamePaths) -> bool:
    if (paths.game / "js" / "plugins" / "Hakubox_Translate.js").is_file():
        return True
    data = paths.game / "data"
    return data.is_dir() and any(data.glob("Hakubox-Translate-*.json"))


def _data_files(data_dir: Path) -> list[Path]:
    ordered: list[Path] = []
    for name in ("System.json", "MapInfos.json", "CommonEvents.json", "Troops.json", *_DATABASE_FIELDS):
        path = data_dir / name
        if path.is_file():
            ordered.append(path)
    maps = [path for path in data_dir.glob("Map*.json") if path.name != "MapInfos.json"]

    def map_key(path: Path) -> tuple[int, str]:
        match = re.fullmatch(r"Map(\d+)\.json", path.name)
        return (int(match.group(1)) if match else 10**9, path.name)

    ordered.extend(sorted(maps, key=map_key))
    return ordered


def _extract_file(rel: str, payload: object, ordinal: int) -> list[Record]:
    name = rel.split("/")[-1]
    if name == "System.json" and isinstance(payload, dict):
        return _extract_system(rel, payload, ordinal)
    if name == "MapInfos.json" and isinstance(payload, list):
        return _extract_map_infos(rel, payload, ordinal)
    if name == "CommonEvents.json" and isinstance(payload, list):
        return _extract_common_events(rel, payload, ordinal)
    if name == "Troops.json" and isinstance(payload, list):
        return _extract_troops(rel, payload, ordinal)
    if name in _DATABASE_FIELDS and isinstance(payload, list):
        return _extract_database(rel, payload, _DATABASE_FIELDS[name], ordinal)
    if name.startswith("Map") and isinstance(payload, dict):
        return _extract_map(rel, payload, ordinal)
    return []


def _extract_system(rel: str, payload: dict, ordinal: int) -> list[Record]:
    records: list[Record] = []

    def add(pointer: list, text: object, kind: str) -> None:
        nonlocal ordinal
        record = _make(
            rel,
            pointer,
            text,
            kind=kind,
            scene="System",
            line=_line_from_pointer(pointer),
            ordinal=ordinal,
        )
        if record:
            records.append(record)
            ordinal += 1

    add(["gameTitle"], payload.get("gameTitle"), "name")
    add(["currencyUnit"], payload.get("currencyUnit"), "term")
    for key in ("elements", "skillTypes", "weaponTypes", "armorTypes", "equipTypes"):
        for index, value in enumerate(payload.get(key) or []):
            add([key, index], value, "term")
    terms = payload.get("terms") or {}
    if isinstance(terms, dict):
        for key in ("basic", "commands", "params"):
            for index, value in enumerate(terms.get(key) or []):
                add(["terms", key, index], value, "term")
        messages = terms.get("messages") or {}
        if isinstance(messages, dict):
            for key, value in messages.items():
                add(["terms", "messages", key], value, "term")
    return records


def _extract_map_infos(rel: str, payload: list, ordinal: int) -> list[Record]:
    records: list[Record] = []
    for index, info in enumerate(payload):
        if not isinstance(info, dict):
            continue
        record = _make(
            rel,
            [index, "name"],
            info.get("name"),
            kind="name",
            scene="MapInfos",
            line=int(info.get("id") or index),
            ordinal=ordinal,
        )
        if record:
            records.append(record)
            ordinal += 1
    return records


def _extract_database(rel: str, payload: list, fields: tuple[str, ...], ordinal: int) -> list[Record]:
    records: list[Record] = []
    label = rel.split("/")[-1].removesuffix(".json")
    for index, row in enumerate(payload):
        if not isinstance(row, dict):
            continue
        scene = str(row.get("name") or f"{label} {row.get('id', index)}")
        for field in fields:
            record = _make(
                rel,
                [index, field],
                row.get(field),
                kind=_KIND_FOR_FIELD.get(field, "name"),
                scene=scene,
                speaker=None if field == "name" else (str(row.get("name") or "") or None),
                line=int(row.get("id") or index),
                ordinal=ordinal,
            )
            if record:
                records.append(record)
                ordinal += 1
    return records


def _extract_common_events(rel: str, payload: list, ordinal: int) -> list[Record]:
    records: list[Record] = []
    for index, event in enumerate(payload):
        if not isinstance(event, dict):
            continue
        scene = str(event.get("name") or f"common {event.get('id', index)}")
        found, ordinal = _walk_list(
            rel,
            event.get("list") or [],
            [index, "list"],
            scene,
            ordinal,
        )
        records.extend(found)
    return records


def _extract_troops(rel: str, payload: list, ordinal: int) -> list[Record]:
    records: list[Record] = []
    for index, troop in enumerate(payload):
        if not isinstance(troop, dict):
            continue
        scene = f"troop {troop.get('id', index)}"
        for page_index, page in enumerate(troop.get("pages") or []):
            if not isinstance(page, dict):
                continue
            found, ordinal = _walk_list(
                rel,
                page.get("list") or [],
                [index, "pages", page_index, "list"],
                scene,
                ordinal,
            )
            records.extend(found)
    return records


def _extract_map(rel: str, payload: dict, ordinal: int) -> list[Record]:
    records: list[Record] = []
    display = payload.get("displayName")
    record = _make(
        rel,
        ["displayName"],
        display,
        kind="name",
        scene="map",
        line=0,
        ordinal=ordinal,
    )
    if record:
        records.append(record)
        ordinal += 1
    for event_index, event in enumerate(payload.get("events") or []):
        if not isinstance(event, dict):
            continue
        scene = str(event.get("name") or f"event {event.get('id', event_index)}")
        for page_index, page in enumerate(event.get("pages") or []):
            if not isinstance(page, dict):
                continue
            found, ordinal = _walk_list(
                rel,
                page.get("list") or [],
                ["events", event_index, "pages", page_index, "list"],
                scene,
                ordinal,
            )
            records.extend(found)
    return records


def _walk_list(
    rel: str,
    commands: list,
    prefix: list,
    scene: str,
    ordinal: int,
) -> tuple[list[Record], int]:
    records: list[Record] = []
    speaker: str | None = None
    face: str | None = None
    for index, command in enumerate(commands):
        if not isinstance(command, dict):
            continue
        code = command.get("code")
        params = command.get("parameters") or []
        if code == 101:
            face_name = params[0] if params and isinstance(params[0], str) else ""
            face_index = params[1] if len(params) > 1 and isinstance(params[1], int) else 0
            face = f"{face_name}:{face_index}" if face_name else None
            spoken = params[4] if len(params) > 4 and isinstance(params[4], str) else ""
            speaker = spoken or None
            record = _make(
                rel,
                [*prefix, index, "parameters", 4],
                spoken,
                kind="name",
                scene=scene,
                face=face,
                line=index,
                ordinal=ordinal,
                command=101,
            )
            if record:
                records.append(record)
                ordinal += 1
            continue
        if code in (401, 405):
            text = params[0] if params else None
            record = _make(
                rel,
                [*prefix, index, "parameters", 0],
                text,
                kind="dialogue",
                scene=scene,
                speaker=speaker if code == 401 else None,
                face=face if code == 401 else None,
                line=index,
                ordinal=ordinal,
                command=code,
            )
            if record:
                records.append(record)
                ordinal += 1
            continue
        if code == 102 and params and isinstance(params[0], list):
            mirrors = _choice_mirrors(commands, index, prefix)
            for choice_index, choice in enumerate(params[0]):
                record = _make(
                    rel,
                    [*prefix, index, "parameters", 0, choice_index],
                    choice,
                    kind="choice",
                    scene=scene,
                    speaker=speaker,
                    face=face,
                    line=index,
                    ordinal=ordinal,
                    command=102,
                    mirrors=mirrors.get(choice_index, []),
                )
                if record:
                    records.append(record)
                    ordinal += 1
            speaker = None
            face = None
            continue
        if code not in (401, 405, 108, 408):
            speaker = None
            face = None
    return records, ordinal


def _choice_mirrors(commands: list, choice_at: int, prefix: list) -> dict[int, list[list]]:
    """Point at the When [choice] commands that repeat the choice text.

    Indent matches the Show Choices command, so a nested choice list does not
    end the outer one early.
    """

    opener = commands[choice_at] if isinstance(commands[choice_at], dict) else {}
    base_indent = opener.get("indent", 0)
    mirrors: dict[int, list[list]] = {}
    for index in range(choice_at + 1, len(commands)):
        command = commands[index]
        if not isinstance(command, dict):
            continue
        code = command.get("code")
        indent = command.get("indent", 0)
        if code == 404 and indent == base_indent:
            break
        if code != 402 or indent != base_indent:
            continue
        params = command.get("parameters") or []
        if len(params) < 2 or not isinstance(params[0], int):
            continue
        mirrors.setdefault(params[0], []).append([*prefix, index, "parameters", 1])
    return mirrors


def _make(
    rel: str,
    pointer: list,
    text: object,
    *,
    kind: str,
    scene: str | None,
    line: int,
    ordinal: int,
    speaker: str | None = None,
    face: str | None = None,
    command: int | None = None,
    mirrors: list | None = None,
) -> Record | None:
    if not isinstance(text, str) or not _worth_translating(text):
        return None
    pointer_key = "/".join(str(part) for part in pointer)
    rebuild: dict = {"pointer": pointer}
    if face:
        rebuild["face"] = face
    if command is not None:
        rebuild["command"] = command
    if mirrors:
        rebuild["mirrors"] = mirrors
    return Record(
        id=f"rpgmaker:{rel}:{pointer_key}",
        engine="rpgmaker",
        kind=kind,
        source=text,
        translation=None,
        speaker=speaker or None,
        file=rel,
        scene=scene,
        line=line,
        source_hash=source_hash(text),
        status="untranslated",
        ordinal=ordinal,
        rebuild=rebuild,
    )


def _worth_translating(text: str) -> bool:
    visible = _VISIBLE.sub("", text).strip()
    if not visible:
        return False
    return any(char.isalnum() for char in visible)


def _line_from_pointer(pointer: list) -> int:
    for part in reversed(pointer):
        if isinstance(part, int):
            return part
    return 0


def _assign(payload: object, pointer: list, value: str | None) -> bool:
    if not pointer or value is None:
        return False
    node: object = payload
    for part in pointer[:-1]:
        try:
            node = node[part]  # type: ignore[index]
        except (KeyError, IndexError, TypeError):
            return False
    last = pointer[-1]
    try:
        current = node[last]  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        return False
    if current == value:
        return False
    node[last] = value  # type: ignore[index]
    return True
