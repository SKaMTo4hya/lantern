import hashlib
from pathlib import Path

from lantern.engines.base import resolve_game
from lantern.engines.renpy import RenPyAdapter
from lantern.engines.renpy_script import parse_script
from lantern.engines.renpy_text import IdentifierState, encode_say_string, render_say_code

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "sample-renpy"


def _digest(codes: list[str]) -> str:
    md5 = hashlib.md5()
    for code in codes:
        md5.update((code + "\r\n").encode("utf-8"))
    return md5.hexdigest()[:8]


def test_official_renpy_dialogue_ids():
    script = (
        "label start:\n"
        '    e "Thank you for taking a look at the Ren\'Py translation framework."\n'
        "\n"
        "    show eileen happy\n"
        "\n"
        '    e "We aim to provide a comprehensive framework for translating dialogue, strings, images, and styles."\n'
        "\n"
        '    e "Pretty much everything your game needs!"\n'
    )
    records, warnings, _ordinal = parse_script("script.rpy", script, IdentifierState(), 0)
    dialogue = [record for record in records if record.kind == "dialogue"]
    assert warnings == []
    assert [record.id for record in dialogue] == [
        "start_636ae3f5",
        "start_bd1ad9e1",
        "start_9e949aac",
    ]
    assert dialogue[0].speaker == "e"
    assert dialogue[0].scene == "start"
    assert dialogue[0].line == 2


def test_duplicate_lines_and_voice_and_explicit_id():
    script = (
        "label start:\n"
        '    e "Hi"\n'
        '    e "Hi"\n'
        '    voice "audio/line.ogg"\n'
        '    m "じゃあ、行こっか。"\n'
        '    m "気をつけて。" id roof_careful\n'
    )
    records, _warnings, _ordinal = parse_script("script.rpy", script, IdentifierState(), 0)
    dialogue = [record for record in records if record.kind == "dialogue"]
    assert dialogue[1].id == dialogue[0].id + "_1"

    say = render_say_code(who="m", attributes=[], temporary_attributes=[], what="じゃあ、行こっか。")
    voice = 'voice "audio/line.ogg"'
    assert dialogue[2].id == "start_" + _digest([voice, say])
    assert dialogue[2].rebuild["statements"][0]["type"] == "voice"
    assert dialogue[3].id == "roof_careful"
    assert "id roof_careful" in dialogue[3].rebuild["original_codes"][-1]


def test_menu_character_and_narrator_context():
    script = (SAMPLE / "game" / "script.rpy").read_text(encoding="utf-8")
    records, warnings, _ordinal = parse_script("script.rpy", script, IdentifierState(), 0)
    by_kind: dict[str, list[str]] = {}
    for record in records:
        by_kind.setdefault(record.kind, []).append(record.source)

    assert by_kind["string"] == ["美咲", "健一"]
    assert by_kind["menu"] == ["どこへ行く？", "屋上へ行く", "図書室に残る"]
    assert "こんにちは、[player_name]。{w}今日はいい天気ね。" in by_kind["dialogue"]
    assert "風が強い。" in by_kind["dialogue"]
    wind = next(record for record in records if record.source == "風が強い。")
    assert wind.speaker is None
    assert wind.scene == "roof"
    waiting = next(record for record in records if record.source.startswith("美咲、学園"))
    assert waiting.speaker == "k"
    assert waiting.scene == "start"
    careful = next(record for record in records if record.id == "roof_careful")
    assert careful.line > wind.line
    assert not any("Character(" in warning and "美咲" in warning for warning in warnings)


def test_underscore_inside_a_python_block_is_a_string():
    script = 'init python:\n    name = _("図書館")\n'
    records, warnings, _ordinal = parse_script("script.rpy", script, IdentifierState(), 0)
    assert warnings == []
    assert [record.source for record in records] == ["図書館"]
    assert records[0].kind == "string"


def test_character_literal_without_underscore_is_reported():
    script = 'define e = Character("美咲")\n'
    _records, warnings, _ordinal = parse_script("script.rpy", script, IdentifierState(), 0)
    assert any("not wrapped in _()" in warning for warning in warnings)
    assert _records == []


def test_encode_say_string_matches_renpy_rules():
    encoded = encode_say_string('She said "hi"  there')
    assert encoded == '"She said \\"hi\\" \\ there"'


def test_sample_project_warns_about_rpyc_and_rpa_but_extracts():
    paths = resolve_game(SAMPLE)
    adapter = RenPyAdapter()
    detection = adapter.detect(paths)
    assert detection.can_extract
    assert any("does not decompile" in warning for warning in detection.warnings)
    assert any("does not unpack" in warning for warning in detection.warnings)
    result = adapter.extract(paths)
    assert {path.name for path in [Path(name) for name in result.files]} >= {"script.rpy", "options.rpy"}
    assert any(record.source == "小さなサンプル" for record in result.records)
    assert len(result.records) == 14


def test_rpyc_only_and_rpa_only_are_blocked():
    adapter = RenPyAdapter()
    rpyc = adapter.detect(resolve_game(ROOT / "tests" / "fixtures" / "rpyc_only"))
    assert rpyc.engine == "renpy"
    assert not rpyc.can_extract
    assert "does not decompile" in rpyc.summary

    rpa = adapter.detect(resolve_game(ROOT / "tests" / "fixtures" / "rpa_only"))
    assert rpa.engine == "renpy"
    assert not rpa.can_extract
    assert "does not unpack" in rpa.summary


def test_build_writes_translation_files_and_keeps_codes(tmp_path: Path):
    import shutil

    copied = tmp_path / "sample"
    shutil.copytree(SAMPLE, copied)
    paths = resolve_game(copied)
    adapter = RenPyAdapter()
    result = adapter.extract(paths)
    for record in result.records:
        record.status = "translated"
        record.translation = "EN:" + record.source
    written = adapter.build(paths, result.records, "english")
    out_game = paths.game
    dialogue = (out_game / "tl" / "english" / "script.rpy").read_text(encoding="utf-8")
    strings = (out_game / "tl" / "english" / "lantern_strings.rpy").read_text(encoding="utf-8")
    assert any(path.name == "script.rpy" for path in written)
    assert "translate english roof_careful:" in dialogue
    assert 'voice "audio/misaki_01.ogg"' in dialogue
    assert "EN:じゃあ、行こっか。" in dialogue
    assert 'old "美咲"' in strings
    assert 'new "EN:美咲"' in strings
    assert 'old "屋上へ行く"' in strings
    assert "translate english strings:" in strings
