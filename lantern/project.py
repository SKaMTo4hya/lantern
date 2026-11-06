"""Copy a game and keep Lantern's database beside the copy, never inside the original."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from lantern.engines.base import GamePaths, resolve_game

DEFAULT_MODEL = "qwen3:8b"
DEFAULT_HOST = "http://127.0.0.1:11434"

CONFIG_NAME = "config.json"


class ProjectError(Exception):
    """A problem with the game folder or the output copy."""


def lantern_dir(project: Path) -> Path:
    path = project / ".lantern"
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path(project: Path) -> Path:
    return project / ".lantern" / "project.sqlite"


def config_path(project: Path) -> Path:
    return project / ".lantern" / CONFIG_NAME


def default_config() -> dict[str, object]:
    return {
        "engine": "renpy",
        "language": "english",
        "source_language": "japanese",
        "provider": "ollama",
        "model": DEFAULT_MODEL,
        "host": DEFAULT_HOST,
        "batch_size": 32,
        "workers": 4,
        "terms": True,
        "context_lines": 4,
        "timeout": 180,
    }


def load_config(project: Path) -> dict[str, object]:
    path = config_path(project)
    config = default_config()
    if path.is_file():
        stored = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(stored, dict):
            config.update(stored)
    return config


def save_config(project: Path, config: dict[str, object]) -> None:
    lantern_dir(project)
    config_path(project).write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def require_project(path: Path) -> GamePaths:
    """Return paths for a copy Lantern already extracted. Refuse a bare game."""

    paths = resolve_game(path)
    if not db_path(paths.project).is_file():
        raise ProjectError(
            f"{paths.project} has no Lantern project database.\n"
            "Run `lantern extract GAME --out COPY` first. "
            "Lantern will not create its database inside the original game."
        )
    return paths


def assert_safe_output(source: Path, output: Path) -> None:
    source_resolved = source.resolve()
    output_resolved = output.expanduser().resolve()
    if source_resolved == output_resolved:
        raise ProjectError("Refusing to write into the original game folder. Choose a separate --out copy.")
    if _is_inside(output_resolved, source_resolved) or _is_inside(source_resolved, output_resolved):
        raise ProjectError("The output folder must not sit inside the original game, or the other way around.")


def copy_project(source: Path, output: Path) -> Path:
    """Copy the whole game tree. The original is only read."""

    assert_safe_output(source, output)
    if output.exists():
        raise ProjectError(f"{output} already exists. Pick a new --out folder so an existing copy is not overwritten.")
    shutil.copytree(source, output)
    return output.resolve()


def reuse_project(output: Path) -> GamePaths | None:
    """Return an existing Lantern copy so a rerun does not copy asset folders again."""

    if not output.exists():
        return None
    paths = resolve_game(output)
    if db_path(paths.project).is_file():
        return paths
    return None


def install_sidecar(project: Path, filename: str, source: Path | None) -> Path | None:
    """Copy an optional glossary or mock map into the project so later commands find it."""

    if source is None:
        return None
    lantern_dir(project)
    target = project / ".lantern" / filename
    shutil.copyfile(source, target)
    return target


def clear_previous_build(paths: GamePaths) -> None:
    """Remove tl files this project wrote last time, and nothing else."""

    manifest = paths.project / ".lantern" / "built-files.txt"
    if not manifest.is_file():
        return
    project_root = paths.project.resolve()
    tl_root = (paths.game / "tl").resolve()
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        target = (project_root / line).resolve()
        if target.is_file() and _is_inside(target, tl_root):
            target.unlink()


def record_build(paths: GamePaths, written: list[Path]) -> None:
    project_root = paths.project.resolve()
    rels = [path.resolve().relative_to(project_root).as_posix() for path in written]
    lantern_dir(paths.project)
    text = "".join(rel + "\n" for rel in rels)
    (paths.project / ".lantern" / "built-files.txt").write_text(text, encoding="utf-8")


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return child != parent
