"""Ren'Py say-string encoding and translation-id digests.

Identifiers follow Ren'Py's Restructurer: MD5 of each statement's canonical
``get_code()`` plus ``\\r\\n``, first 8 hex chars, prefixed by the label.
The check vector is the tutorial line documented by Ren'Py itself:

``e "Thank you for taking a look at the Ren'Py translation framework."``
-> ``start_636ae3f5``
"""

from __future__ import annotations

import hashlib
import re


def encode_say_string(text: str) -> str:
    """Encode a decoded say string the way ``renpy.translation.encode_say_string`` does."""

    text = text.replace("\\", "\\\\")
    text = text.replace("\n", "\\n")
    text = text.replace('"', '\\"')
    text = re.sub(r"(?<= ) ", r"\\ ", text)
    return '"' + text + '"'


def quote_unicode(text: str) -> str:
    """Escape a string for a ``translate strings`` old/new line."""

    text = text.replace("\\", "\\\\")
    text = text.replace('"', '\\"')
    text = text.replace("\a", "\\a")
    text = text.replace("\b", "\\b")
    text = text.replace("\f", "\\f")
    text = text.replace("\n", "\\n")
    text = text.replace("\r", "\\r")
    text = text.replace("\t", "\\t")
    text = text.replace("\v", "\\v")
    return text


def decode_renpy_string(body: str) -> str:
    """Decode escapes inside a Ren'Py string literal body (quotes already removed)."""

    out: list[str] = []
    index = 0
    mapping = {
        "n": "\n",
        "t": "\t",
        "r": "\r",
        '"': '"',
        "'": "'",
        "\\": "\\",
        " ": " ",
        "a": "\a",
        "b": "\b",
        "f": "\f",
        "v": "\v",
    }
    while index < len(body):
        if body[index] != "\\":
            out.append(body[index])
            index += 1
            continue
        if index + 1 >= len(body):
            out.append("\\")
            break
        nxt = body[index + 1]
        out.append(mapping.get(nxt, nxt))
        index += 2
    return "".join(out)


def render_say_code(
    *,
    who: str | None,
    attributes: list[str],
    temporary_attributes: list[str],
    what: str,
    interact: bool = True,
    explicit_id: str | None = None,
    arguments: str | None = None,
    with_: str | None = None,
) -> str:
    """Canonical say line. This is what Ren'Py hashes and what tl files quote."""

    parts: list[str] = []
    if who:
        parts.append(who)
    parts.extend(attributes)
    if temporary_attributes:
        parts.append("@")
        parts.extend(temporary_attributes)
    parts.append(encode_say_string(what))
    if not interact:
        parts.append("nointeract")
    if explicit_id:
        parts.append("id")
        parts.append(explicit_id)
    if arguments:
        parts.append(arguments)
    if with_:
        parts.append("with")
        parts.append(with_)
    return " ".join(parts)


class IdentifierState:
    """Tracks ids the way Ren'Py's Restructurer does, across files in load order."""

    def __init__(self) -> None:
        self.used: set[str] = set()
        self.preexisting: set[str] = set()

    def unique(self, label: str | None, digest: str) -> str:
        base = digest if label is None else label.replace(".", "_") + "_" + digest
        index = 0
        suffix = ""
        while True:
            identifier = base + suffix
            if identifier not in self.used and identifier not in self.preexisting:
                return identifier
            index += 1
            suffix = f"_{index}"

    def assign(self, label: str | None, codes: list[str], explicit_id: str | None, alternate_label: str | None) -> str:
        digest_md5 = hashlib.md5()
        for code in codes:
            digest_md5.update((code + "\r\n").encode("utf-8"))
        digest = digest_md5.hexdigest()[:8]

        auto = self.unique(label, digest)
        identifier = auto
        alternate: str | None = None
        if alternate_label is not None:
            alternate = self.unique(alternate_label, digest)
            identifier = explicit_id or identifier
        elif explicit_id is not None:
            alternate = identifier
            identifier = explicit_id

        self.used.add(identifier)
        if alternate is not None:
            self.used.add(alternate)
        return identifier
