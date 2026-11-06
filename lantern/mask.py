"""Mask control codes before translation.

Placeholders are restored mechanically. The model is only asked to copy them.
A line is accepted when every placeholder comes back once, in the original order.

``renpy`` covers text tags, ``[variables]``, and printf codes.
``rpgmaker`` covers ``\\V[n]``, ``\\N[n]``, ``\\P[n]``, ``\\C[n]``, ``\\I[n]``,
``\\{``, ``\\}``, and the other message codes, plus ``%1`` style tokens.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Left-to-right. Longer escapes ({{, [[, %%) win over the forms they prefix.
_SPECIAL = re.compile(
    r"\[\[|"
    r"\[[^\]]+\]|"
    r"\{\{|"
    r"\{[^{}]*\}|"
    r"%%|"
    r"%\([^)]+\)[-+#0-9.]*[a-zA-Z]|"
    r"%[-+#0-9.]*[a-zA-Z]"
)

# Bracket codes first so \V[1] is one token, not a stray \V plus [1].
_RPG = re.compile(
    r"\\[A-Za-z]+\[[^\]]*\]|"
    r"\\[{}]|"
    r"\\[.$|^!><\\]|"
    r"\\[GgNn]|"
    r"%\d+|"
    r"<e\d+>"
)

_TOKEN = re.compile(r"⟪(TAG|VAR|FMT)(\d{3})⟫")

_CJK = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")


@dataclass(frozen=True)
class Masked:
    text: str
    tokens: tuple[str, ...]
    originals: tuple[str, ...]


@dataclass(frozen=True)
class RestoreResult:
    ok: bool
    text: str
    error: str | None = None


def _kind(lexeme: str) -> str:
    if lexeme.startswith("["):
        return "VAR"
    if lexeme.startswith("%"):
        return "FMT"
    return "TAG"


def _rpg_kind(lexeme: str) -> str:
    if lexeme.startswith("%"):
        return "FMT"
    if re.match(r"\\[VNP](?:\[|$)", lexeme):
        return "VAR"
    return "TAG"


def mask_text(source: str, profile: str = "renpy") -> Masked:
    pattern = _RPG if profile == "rpgmaker" else _SPECIAL
    classify = _rpg_kind if profile == "rpgmaker" else _kind
    counts = {"TAG": 0, "VAR": 0, "FMT": 0}
    tokens: list[str] = []
    originals: list[str] = []

    def repl(match: re.Match[str]) -> str:
        lexeme = match.group(0)
        kind = classify(lexeme)
        token = f"⟪{kind}{counts[kind]:03d}⟫"
        counts[kind] += 1
        tokens.append(token)
        originals.append(lexeme)
        return token

    return Masked(text=pattern.sub(repl, source), tokens=tuple(tokens), originals=tuple(originals))


def _is_translatable_prose(masked_text: str) -> bool:
    """True when an unchanged line is a failed translation, not a name or '……'."""

    visible = _TOKEN.sub("", masked_text).strip()
    if not visible or not _CJK.search(visible):
        return False
    letters = [char for char in visible if "\u3040" <= char <= "\u9fff"]
    if len(letters) <= 8 and not any(char in visible for char in "。！？?!，,、"):
        return False
    return True


def validate_and_restore(
    source: str,
    translated: str,
    *,
    reject_unchanged_cjk: bool,
    profile: str = "renpy",
) -> RestoreResult:
    """Check a masked model reply and splice the original codes back in."""

    masked = mask_text(source, profile)
    if translated is None or not str(translated).strip():
        return RestoreResult(False, "", "empty translation")

    text = str(translated).replace("\r\n", "\n").replace("\r", "\n")
    found = [f"⟪{kind}{num}⟫" for kind, num in _TOKEN.findall(text)]
    expected = list(masked.tokens)
    if found != expected:
        got = " ".join(found) if found else "(none)"
        want = " ".join(expected) if expected else "(none)"
        return RestoreResult(False, text, f"placeholders not intact: expected {want}, got {got}")

    if masked.text.count("\n") != text.count("\n"):
        return RestoreResult(False, text, "newline count changed")

    restored = text
    for token, original in zip(masked.tokens, masked.originals):
        restored = restored.replace(token, original, 1)

    if reject_unchanged_cjk and restored == source and _is_translatable_prose(masked.text):
        return RestoreResult(False, restored, "translation echoed the source")

    return RestoreResult(True, restored, None)
