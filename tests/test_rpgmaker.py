import json
import shutil
from pathlib import Path

from lantern.cli import main
from lantern.engines.base import resolve_game
from lantern.engines.rpgmaker import RpgMakerAdapter, ensure_source_snapshot
from lantern.mask import mask_text, validate_and_restore

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "rpg_mz"


def test_rpg_maker_control_codes_round_trip():
    source = r"你好，\N[1]。现在是\V[2]点。\C[3]风\{大\}。获得了 %1\G！"
    masked = mask_text(source, "rpgmaker")
    assert masked.originals == (
        r"\N[1]",
        r"\V[2]",
        r"\C[3]",
        r"\{",
        r"\}",
        "%1",
        r"\G",
    )
    assert [token[:4] for token in masked.tokens] == ["⟪VAR", "⟪VAR", "⟪TAG", "⟪TAG", "⟪TAG", "⟪FMT", "⟪TAG"]
    english = masked.text.replace("你好，", "Hello, ").replace("现在是", "It is ").replace("点。", ". ").replace("风", "The wind is ").replace("大", "strong").replace("获得了 ", "Received ")
    restored = validate_and_restore(source, english, reject_unchanged_cjk=True, profile="rpgmaker")
    assert restored.ok, restored.error
    assert r"\N[1]" in restored.text and r"\V[2]" in restored.text
    assert r"\C[3]" in restored.text and r"\{" in restored.text and r"\}" in restored.text
    assert "%1" in restored.text and r"\G" in restored.text
    portrait = mask_text(r"店主<e1>说\C[6]晚安", "rpgmaker")
    assert "<e1>" in portrait.originals and r"\C[6]" in portrait.originals


def test_extract_keeps_speaker_face_and_skips_switches():
    result = RpgMakerAdapter().extract(resolve_game(FIXTURE))
    sources = [record.source for record in result.records]
    assert "内部开关不要译" not in sources
    assert "内部变量" not in sources
    assert "<ignore>" not in sources
    hello = next(record for record in result.records if record.source.startswith("你好，"))
    assert hello.kind == "dialogue"
    assert hello.speaker == "灯塔守"
    assert hello.rebuild["face"] == "People1:2"
    assert hello.rebuild["command"] == 401
    wind = next(record for record in result.records if record.source == "风很大。")
    assert wind.speaker == "灯塔守"
    scroll = next(record for record in result.records if record.source.startswith("公告："))
    assert scroll.rebuild["command"] == 405
    assert scroll.speaker is None
    stay = next(record for record in result.records if record.source == "留下")
    leave_mirrors = stay.rebuild["mirrors"]
    assert len(leave_mirrors) == 1
    assert leave_mirrors[0][-1] == 1
    tea = next(record for record in result.records if record.source == "喝茶")
    assert tea.rebuild["mirrors"]
    saved = next(record for record in result.records if "已保存" in record.source)
    assert saved.file == "data/CommonEvents.json"
    gold = next(record for record in result.records if record.source.startswith("获得了"))
    assert gold.kind == "term"
    assert any("Hakubox" in warning for warning in result.warnings)


def test_build_patches_json_and_leaves_hakubox_dictionary(tmp_path: Path):
    copied = tmp_path / "game"
    shutil.copytree(FIXTURE, copied)
    original_system = (FIXTURE / "data" / "System.json").read_bytes()
    original_dict = (FIXTURE / "data" / "Hakubox-Translate-en-US.json").read_bytes()
    paths = resolve_game(copied)
    ensure_source_snapshot(paths)
    adapter = RpgMakerAdapter()
    result = adapter.extract(paths)
    for record in result.records:
        record.status = "translated"
        record.translation = "EN:" + record.source
    written = adapter.build(paths, result.records, "english")
    assert (FIXTURE / "data" / "System.json").read_bytes() == original_system
    assert (copied / "data" / "Hakubox-Translate-en-US.json").read_bytes() == original_dict
    assert all("Hakubox-Translate" not in path.name for path in written)

    system = json.loads((copied / "data" / "System.json").read_text(encoding="utf-8"))
    assert system["currencyUnit"] == "EN:金币"
    assert system["switches"][1] == "内部开关不要译"
    assert system["terms"]["messages"]["obtainGold"] == "EN:获得了 %1\\G！"

    game_map = json.loads((copied / "data" / "Map001.json").read_text(encoding="utf-8"))
    commands = game_map["events"][1]["pages"][0]["list"]
    assert commands[1]["parameters"][0] == "EN:你好，\\N[1]。现在是\\V[2]点。"
    assert commands[4]["parameters"][1] == "EN:留下"
    assert commands[6]["parameters"][1] == "EN:喝茶"
    assert commands[18]["parameters"][0].startswith("EN:公告：")
    assert "\\{" in commands[18]["parameters"][0]

    again = adapter.build(paths, result.records, "english")
    assert again == []

    refreshed = adapter.extract(paths)
    hello = next(record for record in refreshed.records if record.file.endswith("Map001.json") and record.kind == "dialogue")
    assert hello.source.startswith("你好，")
    assert not hello.source.startswith("EN:")


def test_cli_run_on_synthetic_project(tmp_path: Path):
    source = tmp_path / "source"
    shutil.copytree(FIXTURE, source)
    extracted = RpgMakerAdapter().extract(resolve_game(source))
    mapping = {record.source: "EN:" + record.source for record in extracted.records}
    mock_map = tmp_path / "mock_map.json"
    mock_map.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "translated"
    code = main(
        [
            "run",
            str(source),
            "--out",
            str(out),
            "--provider",
            "mock",
            "--mock-map",
            str(mock_map),
            "--language",
            "english",
        ]
    )
    assert code == 0
    assert (source / "data" / "Map001.json").read_bytes() == (FIXTURE / "data" / "Map001.json").read_bytes()
    payload = json.loads((out / "data" / "Items.json").read_text(encoding="utf-8"))
    assert payload[1]["description"] == "EN:指向北。\\C[3]不会迷路。"
    assert (out / ".lantern" / "source" / "data" / "Items.json").read_bytes() == (
        FIXTURE / "data" / "Items.json"
    ).read_bytes()


def test_mv_project_is_recognized(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "System.json").write_text('{"gameTitle":"Old Town"}', encoding="utf-8")
    (tmp_path / "js").mkdir()
    (tmp_path / "js" / "rpg_core.js").write_text("// mv\n", encoding="utf-8")
    detection = RpgMakerAdapter().detect(resolve_game(tmp_path))
    assert detection.engine == "rpgmaker"
    assert detection.can_extract
    assert "MV" in detection.summary
