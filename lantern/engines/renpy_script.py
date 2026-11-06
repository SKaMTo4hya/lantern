"""A Ren'Py script reader for dialogue, menus, and _() strings.

This is not a full Ren'Py parser. It understands the statement shapes visual
novels actually translate: say lines, voice lines grouped with the following
say, menu choices, and strings wrapped in _() or __(). Translation ids match
Ren'Py for those shapes. See the module docstring on RenPyAdapter for the rest.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from lantern.engines.renpy_text import IdentifierState, decode_renpy_string, render_say_code
from lantern.models import Record, source_hash

_IDENT = re.compile(r"[^\W\d]\w*", re.UNICODE)

# Statement heads that are never say statements. ``extend`` is intentionally
# absent: Ren'Py treats it as a say whose who is the word extend.
_NON_SAY = {
    "jump",
    "call",
    "scene",
    "show",
    "hide",
    "with",
    "play",
    "stop",
    "queue",
    "pause",
    "window",
    "define",
    "default",
    "image",
    "transform",
    "style",
    "python",
    "init",
    "screen",
    "return",
    "pass",
    "if",
    "elif",
    "else",
    "while",
    "menu",
    "label",
    "voice",
    "nvl",
    "camera",
    "layeredimage",
    "rpy",
    "translate",
    "testcase",
}

_BLOCK = {"if", "elif", "else", "while"}
_OPAQUE = {"screen", "image", "style", "transform", "layeredimage"}


@dataclass
class LogicalLine:
    indent: int
    line: int
    text: str


@dataclass
class SayStmt:
    kind: str
    who: str | None
    attributes: list[str]
    temporary_attributes: list[str]
    what: str
    interact: bool
    explicit_id: str | None
    arguments: str | None
    with_: str | None

    @property
    def code(self) -> str:
        return render_say_code(
            who=self.who,
            attributes=self.attributes,
            temporary_attributes=self.temporary_attributes,
            what=self.what,
            interact=self.interact,
            explicit_id=self.explicit_id,
            arguments=self.arguments,
            with_=self.with_,
        )


@dataclass
class VoiceStmt:
    kind: str
    code: str


@dataclass
class SimpleStmt:
    kind: str
    name: str | None = None
    hide: bool = False
    opens: bool = False


@dataclass
class MenuItem:
    text: str
    opens: bool


def read_bytes(data: bytes) -> tuple[str, str | None]:
    """Decode a script. Ren'Py 8 files are UTF-8; older scripts may be cp932."""

    for encoding in ("utf-8-sig", "cp932"):
        try:
            return data.decode(encoding), None if encoding == "utf-8-sig" else "cp932"
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("utf-8", data[:40], 0, 1, "script is neither utf-8 nor cp932")


def logical_lines(source: str) -> list[LogicalLine]:
    """Split a script into statements, joining quotes, parentheses, and backslashes."""

    physical = source.replace("\ufeff", "").splitlines()
    lines: list[LogicalLine] = []
    index = 0
    while index < len(physical):
        indent, body = _split_indent(physical[index])
        if body == "" or body.startswith("#"):
            index += 1
            continue
        start = index + 1
        buffer = body
        index += 1
        while True:
            closed, in_string = _scan_open(buffer)
            if in_string:
                if index >= len(physical):
                    break
                buffer += "\n" + physical[index]
                index += 1
                continue
            if not closed:
                if index >= len(physical):
                    break
                buffer += " " + physical[index].strip()
                index += 1
                continue
            if _ends_with_continuation(buffer):
                buffer = buffer[:-1]
                if index >= len(physical):
                    break
                buffer += physical[index].strip()
                index += 1
                continue
            break
        text = _strip_comment(buffer).rstrip()
        if text:
            lines.append(LogicalLine(indent=indent, line=start, text=text))
    return lines


def _split_indent(line: str) -> tuple[int, str]:
    width = 0
    index = 0
    while index < len(line) and line[index] in " \t":
        width += 4 if line[index] == "\t" else 1
        index += 1
    return width, line[index:]


def _scan_open(text: str) -> tuple[bool, bool]:
    """Return (structures_closed, inside_string). Stops at a real comment."""

    index = 0
    stack: list[str] = []
    string_quote: str | None = None
    pairs = {")": "(", "]": "[", "}": "{"}
    while index < len(text):
        if string_quote is not None:
            if text.startswith(string_quote, index) and not _escaped(text, index):
                index += len(string_quote)
                string_quote = None
                continue
            index += 1
            continue
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == "#":
            break
        if text.startswith('"""', index) or text.startswith("'''", index):
            string_quote = text[index : index + 3]
            index += 3
            continue
        if text[index] in "\"'":
            string_quote = text[index]
            index += 1
            continue
        char = text[index]
        if char in "([{":
            stack.append(char)
        elif char in ")]}":
            if stack and stack[-1] == pairs[char]:
                stack.pop()
        index += 1
    return (string_quote is None and not stack), string_quote is not None


def _escaped(text: str, index: int) -> bool:
    slashes = 0
    cursor = index - 1
    while cursor >= 0 and text[cursor] == "\\":
        slashes += 1
        cursor -= 1
    return slashes % 2 == 1


def _ends_with_continuation(text: str) -> bool:
    code = _strip_comment(text).rstrip()
    if not code.endswith("\\"):
        return False
    return not _escaped(code, len(code) - 1) and code.endswith("\\")


def _strip_comment(text: str) -> str:
    index = 0
    string_quote: str | None = None
    while index < len(text):
        if string_quote is not None:
            if text.startswith(string_quote, index) and not _escaped(text, index):
                index += len(string_quote)
                string_quote = None
                continue
            index += 1
            continue
        if text[index] == "\\":
            index += 2
            continue
        if text.startswith('"""', index) or text.startswith("'''", index):
            string_quote = text[index : index + 3]
            index += 3
            continue
        if text[index] in "\"'":
            string_quote = text[index]
            index += 1
            continue
        if text[index] == "#":
            return text[:index]
        index += 1
    return text


def read_string(text: str, index: int) -> tuple[str, str, int]:
    """Read a string at ``index``. Return (raw body, quote, index after the string)."""

    if text.startswith('"""', index) or text.startswith("'''", index):
        quote = text[index : index + 3]
        index += 3
        start = index
        while index < len(text) and not (text.startswith(quote, index) and not _escaped(text, index)):
            index += 2 if text[index] == "\\" else 1
        if not text.startswith(quote, index):
            raise ValueError("unclosed string")
        return text[start:index], quote, index + 3
    quote = text[index]
    if quote not in "\"'":
        raise ValueError("not a string")
    index += 1
    start = index
    while index < len(text):
        if text[index] == "\\" and index + 1 < len(text):
            index += 2
            continue
        if text[index] == quote:
            return text[start:index], quote, index + 1
        index += 1
    raise ValueError("unclosed string")


def _skip_ws(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t":
        index += 1
    return index


def _read_balanced(text: str, index: int) -> tuple[str, int]:
    """Read a parenthesized group starting at ``index``, which must be ``(``."""

    if index >= len(text) or text[index] != "(":
        raise ValueError("expected '('")
    start = index
    depth = 0
    string_quote: str | None = None
    while index < len(text):
        if string_quote is not None:
            if text.startswith(string_quote, index) and not _escaped(text, index):
                index += len(string_quote)
                string_quote = None
                continue
            index += 2 if text[index] == "\\" else 1
            continue
        if text.startswith('"""', index) or text.startswith("'''", index):
            string_quote = text[index : index + 3]
            index += 3
            continue
        if text[index] in "\"'":
            string_quote = text[index]
            index += 1
            continue
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return text[start : index + 1], index + 1
        index += 1
    raise ValueError("unclosed '('")


def try_parse_say(text: str) -> SayStmt | None:
    index = _skip_ws(text, 0)
    who: str | None = None
    attributes: list[str] = []
    temporary: list[str] = []
    if index < len(text) and text[index] == "(":
        try:
            who, index = _read_balanced(text, index)
        except ValueError:
            return None
        who = " ".join(who.split())
    elif index < len(text) and text[index] not in "\"'":
        matched = _IDENT.match(text, index)
        if not matched:
            return None
        who = matched.group(0)
        index = matched.end()
        mode = "attr"
        while True:
            index = _skip_ws(text, index)
            if index >= len(text) or text[index] in "\"'":
                break
            if text[index] == "@":
                mode = "temp"
                index += 1
                continue
            matched = _IDENT.match(text, index)
            if not matched:
                return None
            (attributes if mode == "attr" else temporary).append(matched.group(0))
            index = matched.end()
    index = _skip_ws(text, index)
    if index >= len(text) or text[index] not in "\"'":
        return None
    try:
        body, _quote, index = read_string(text, index)
    except ValueError:
        return None
    what = decode_renpy_string(body)
    rest = text[index:].strip()
    interact = True
    explicit_id: str | None = None
    arguments: str | None = None
    with_: str | None = None
    while rest:
        if rest == "nointeract" or rest.startswith("nointeract ") or rest.startswith("nointeract\t"):
            interact = False
            rest = rest[len("nointeract") :].strip()
            continue
        id_match = re.match(r"id\s+([^\W\d]\w*)\b", rest, re.UNICODE)
        if id_match and explicit_id is None:
            explicit_id = id_match.group(1)
            rest = rest[id_match.end() :].strip()
            continue
        if rest.startswith("("):
            try:
                arguments, end = _read_balanced(rest, 0)
            except ValueError:
                return None
            arguments = " ".join(arguments.split())
            rest = rest[end:].strip()
            continue
        if rest == "with" or rest.startswith("with "):
            with_ = rest[4:].strip()
            if not with_:
                return None
            rest = ""
            continue
        return None
    return SayStmt(
        kind="say",
        who=who,
        attributes=attributes,
        temporary_attributes=temporary,
        what=what,
        interact=interact,
        explicit_id=explicit_id,
        arguments=arguments,
        with_=with_,
    )


def try_parse_menu_item(text: str) -> MenuItem | None:
    try:
        body, _quote, index = read_string(text, 0)
    except ValueError:
        return None
    source = decode_renpy_string(body)
    rest = text[index:].strip()
    opens = False
    if rest.endswith(":"):
        opens = True
        rest = rest[:-1].strip()
    if rest == "":
        return MenuItem(text=source, opens=opens)
    if rest == "if" or rest.startswith("if "):
        return MenuItem(text=source, opens=opens)
    return None


def find_underscore_strings(text: str) -> list[str]:
    """Return decoded strings passed to _() or __(), in order."""

    found: list[str] = []
    index = 0
    while index < len(text):
        if text[index] in "\"'":
            try:
                _body, _quote, index = read_string(text, index)
            except ValueError:
                break
            continue
        if text[index] == "_" and (index == 0 or not (text[index - 1].isalnum() or text[index - 1] == "_")):
            cursor = index + 1
            if cursor < len(text) and text[cursor] == "_":
                cursor += 1
            probe = cursor
            while probe < len(text) and text[probe].isspace():
                probe += 1
            if probe < len(text) and text[probe] == "(":
                probe += 1
                while probe < len(text) and text[probe].isspace():
                    probe += 1
                if probe < len(text) and text[probe] in "\"'":
                    try:
                        body, _quote, index = read_string(text, probe)
                    except ValueError:
                        break
                    found.append(decode_renpy_string(body))
                    continue
        index += 1
    return found


def find_character_literals(text: str) -> list[str]:
    """Display names passed as plain strings to Character(), not through _()."""

    found: list[str] = []
    cursor = 0
    needle = "Character("
    while True:
        start = text.find(needle, cursor)
        if start < 0:
            return found
        index = start + len(needle)
        while index < len(text) and text[index].isspace():
            index += 1
        if index < len(text) and text[index] in "\"'":
            try:
                body, _quote, index = read_string(text, index)
            except ValueError:
                return found
            found.append(decode_renpy_string(body))
            cursor = index
            continue
        cursor = start + len(needle)


def _first_word(text: str) -> str:
    parts = text.split(None, 1)
    return parts[0] if parts else ""


def _head(text: str) -> str:
    """Statement keyword, without a trailing colon (``menu:`` and ``else:``)."""

    word = _first_word(text)
    return word[:-1] if word.endswith(":") else word


def _is_python(text: str) -> bool:
    if text.startswith("python"):
        return True
    return re.match(r"init(?:\s+-?\d+)?\s+python\b", text) is not None


def classify(text: str) -> SayStmt | VoiceStmt | SimpleStmt:
    if text.startswith("$"):
        return SimpleStmt("code")
    word = _head(text)
    if word == "label":
        return _parse_label(text)
    if word == "menu" and text.rstrip().endswith(":"):
        return SimpleStmt("menu", opens=True)
    if _is_python(text):
        return SimpleStmt("python", opens=text.rstrip().endswith(":"))
    if word in _OPAQUE:
        return SimpleStmt("opaque", opens=text.rstrip().endswith(":"))
    if word == "voice":
        return VoiceStmt("voice", code=" ".join(text.split()))
    if word in _BLOCK or (word == "init" and text.rstrip().endswith(":")):
        return SimpleStmt("block", opens=True)
    if word in _NON_SAY:
        return SimpleStmt("other", opens=text.rstrip().endswith(":"))
    say = try_parse_say(text)
    if say is not None:
        return say
    return SimpleStmt("other", opens=text.rstrip().endswith(":"))


def _parse_label(text: str) -> SimpleStmt:
    matched = re.match(r"label\s+(\.?[^\W\d][\w.]*)(.*)$", text, re.UNICODE)
    if not matched:
        return SimpleStmt("other", opens=text.rstrip().endswith(":"))
    name = matched.group(1)
    rest = matched.group(2)
    hide = re.search(r"\bhide\b", rest) is not None
    return SimpleStmt("label", name=name, hide=hide, opens=True)


def collect_explicit_ids(lines: list[LogicalLine]) -> set[str]:
    found: set[str] = set()
    for line in lines:
        if _first_word(line.text) in _NON_SAY or line.text.startswith("$"):
            continue
        say = try_parse_say(line.text)
        if say and say.explicit_id:
            found.add(say.explicit_id)
    return found


class ScriptParser:
    def __init__(self, rel: str, lines: list[LogicalLine], ids: IdentifierState, ordinal_start: int):
        self.rel = rel
        self.lines = lines
        self.ids = ids
        self.ordinal = ordinal_start
        self.records: list[Record] = []
        self.warnings: list[str] = []
        self.label: str | None = None
        self.global_label: str | None = None
        self.alternate: str | None = None
        self.group: list[SayStmt | VoiceStmt] = []

    def parse(self) -> None:
        self._parse_block(0, parent_indent=-1)
        self._flush_voice_only()

    def _parse_block(self, index: int, parent_indent: int) -> int:
        if index >= len(self.lines) or self.lines[index].indent <= parent_indent:
            return index
        block_indent = self.lines[index].indent
        while index < len(self.lines) and self.lines[index].indent >= block_indent:
            line = self.lines[index]
            if line.indent > block_indent:
                self.warnings.append(
                    f"{self.rel}:{line.line}: indented line is not inside a block, skipped"
                )
                index += 1
                continue
            index += 1
            self._scan_strings(line)
            stmt = classify(line.text)
            if isinstance(stmt, SayStmt):
                self.group.append(stmt)
                self._emit_group(line)
            elif isinstance(stmt, VoiceStmt):
                self.group.append(stmt)
            elif stmt.kind == "label":
                self._flush_voice_only()
                index = self._enter_label(index, line, stmt)
            elif stmt.kind == "menu":
                self._flush_voice_only()
                index = self._parse_menu(index, line.indent)
            elif stmt.kind == "python":
                self._flush_voice_only()
                if stmt.opens:
                    index = self._consume_opaque(index, line.indent)
            elif stmt.kind == "opaque":
                self._flush_voice_only()
                if stmt.opens:
                    index = self._consume_opaque(index, line.indent)
            elif stmt.kind == "block":
                self._flush_voice_only()
                index = self._parse_block(index, line.indent)
            else:
                self._flush_voice_only()
                if stmt.opens:
                    index = self._consume_opaque(index, line.indent)
                elif '"' in line.text or "'" in line.text:
                    if _first_word(line.text) not in _NON_SAY and not line.text.startswith("$"):
                        self.warnings.append(
                            f"{self.rel}:{line.line}: quoted line was not parsed as dialogue"
                        )
        self._flush_voice_only()
        return index

    def _enter_label(self, index: int, line: LogicalLine, stmt: SimpleStmt) -> int:
        """Match Ren'Py: a label applies to its body and to later siblings.

        Underscore labels set the alternate id and leave the current label.
        ``hide`` labels do not change it. The label is not restored afterwards;
        the next real label is what clears it.
        """

        raw = stmt.name or ""
        if raw.startswith("."):
            self.label = f"{self.global_label}{raw}" if self.global_label else raw[1:]
            self.alternate = None
        elif raw.startswith("_"):
            self.alternate = raw
        elif not stmt.hide:
            self.label = raw
            self.global_label = raw
            self.alternate = None
        return self._parse_block(index, line.indent)

    def _parse_menu(self, index: int, parent_indent: int) -> int:
        if index >= len(self.lines) or self.lines[index].indent <= parent_indent:
            return index
        item_indent = self.lines[index].indent
        while index < len(self.lines) and self.lines[index].indent >= item_indent:
            line = self.lines[index]
            if line.indent > item_indent:
                self.warnings.append(f"{self.rel}:{line.line}: unexpected indent inside menu, skipped")
                index += 1
                continue
            index += 1
            item = try_parse_menu_item(line.text)
            if item is None:
                self.warnings.append(f"{self.rel}:{line.line}: expected a menu string, skipped")
                if line.text.rstrip().endswith(":"):
                    index = self._consume_opaque(index, line.indent)
                continue
            self._add_string("menu", item.text, line.line, 0)
            self._scan_strings(line)
            if item.opens:
                index = self._parse_block(index, line.indent)
        return index

    def _consume_opaque(self, index: int, parent_indent: int) -> int:
        while index < len(self.lines) and self.lines[index].indent > parent_indent:
            self._scan_strings(self.lines[index])
            index += 1
        return index

    def _scan_strings(self, line: LogicalLine) -> None:
        for nth, text in enumerate(find_underscore_strings(line.text)):
            if text.strip():
                self._add_string("string", text, line.line, nth)
        for name in find_character_literals(line.text):
            self.warnings.append(
                f"{self.rel}:{line.line}: Character({name!r}) is not wrapped in _(), "
                "so a translate-strings entry would not change the name shown in game"
            )

    def _add_string(self, kind: str, text: str, line: int, nth: int) -> None:
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
        record_id = f"{kind}:{self.rel}:{line}:{nth}:{digest}"
        self.records.append(
            Record(
                id=record_id,
                engine="renpy",
                kind=kind,
                source=text,
                translation=None,
                speaker=None,
                file=self.rel,
                scene=self.label,
                line=line,
                source_hash=source_hash(text),
                status="untranslated",
                ordinal=self.ordinal,
                rebuild={"comment_file": f"game/{self.rel}"},
            )
        )
        self.ordinal += 1

    def _emit_group(self, line: LogicalLine) -> None:
        if not self.group or not isinstance(self.group[-1], SayStmt):
            return
        say = self.group[-1]
        codes = [item.code for item in self.group]
        renpy_id = self.ids.assign(self.label, codes, say.explicit_id, self.alternate)
        statements = []
        for item in self.group:
            if isinstance(item, VoiceStmt):
                statements.append({"type": "voice", "code": item.code})
            else:
                statements.append(
                    {
                        "type": "say",
                        "who": item.who,
                        "attributes": list(item.attributes),
                        "temporary_attributes": list(item.temporary_attributes),
                        "interact": item.interact,
                        "explicit_id": item.explicit_id,
                        "arguments": item.arguments,
                        "with": item.with_,
                    }
                )
        self.records.append(
            Record(
                id=renpy_id,
                engine="renpy",
                kind="dialogue",
                source=say.what,
                translation=None,
                speaker=say.who,
                file=self.rel,
                scene=self.label,
                line=line.line,
                source_hash=source_hash(say.what),
                status="untranslated",
                ordinal=self.ordinal,
                rebuild={
                    "renpy_id": renpy_id,
                    "comment_file": f"game/{self.rel}",
                    "statements": statements,
                    "original_codes": codes,
                },
            )
        )
        self.ordinal += 1
        self.group = []

    def _flush_voice_only(self) -> None:
        if not self.group:
            return
        if any(isinstance(item, SayStmt) for item in self.group):
            self.group = []
            return
        codes = [item.code for item in self.group if isinstance(item, VoiceStmt)]
        if codes:
            # Register the id Ren'Py would consume so later lines do not collide.
            self.ids.assign(self.label, codes, None, self.alternate)
        self.group = []


def parse_script(rel: str, source: str, ids: IdentifierState, ordinal_start: int) -> tuple[list[Record], list[str], int]:
    lines = logical_lines(source)
    parser = ScriptParser(rel, lines, ids, ordinal_start)
    parser.parse()
    next_ordinal = parser.ordinal
    return parser.records, parser.warnings, next_ordinal
