import json
from pathlib import Path

from lantern.cli import main

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "sample-renpy"
RPYC = ROOT / "tests" / "fixtures" / "rpyc_only"
RPA = ROOT / "tests" / "fixtures" / "rpa_only"


def test_run_sample_with_mock_writes_english_tl_and_leaves_the_original(tmp_path: Path):
    out = tmp_path / "sample-en"
    code = main(
        [
            "run",
            str(SAMPLE),
            "--out",
            str(out),
            "--provider",
            "mock",
            "--mock-map",
            str(SAMPLE / "mock_map.json"),
            "--glossary",
            str(SAMPLE / "glossary.json"),
            "--language",
            "english",
        ]
    )
    assert code == 0
    original_script = (SAMPLE / "game" / "script.rpy").read_bytes()
    assert (out / "game" / "script.rpy").read_bytes() == original_script
    assert not (SAMPLE / ".lantern").exists()
    assert not (SAMPLE / "game" / "tl").exists()

    dialogue = (out / "game" / "tl" / "english" / "script.rpy").read_text(encoding="utf-8")
    strings = (out / "game" / "tl" / "english" / "lantern_strings.rpy").read_text(encoding="utf-8")
    assert "translate english roof_careful:" in dialogue
    assert "Hello, [player_name].{w}The weather is nice today." in dialogue
    assert 'm happy "Sorry, I got a little delayed."' in dialogue
    assert 'voice "audio/misaki_01.ogg"' in dialogue
    assert "It's quiet here. It feels like %(place)s." in dialogue
    assert "Be careful{b}, okay{/b}?" in dialogue
    assert 'old "美咲"' in strings and 'new "Misaki"' in strings
    assert 'old "どこへ行く？"' in strings and 'new "Where should we go?"' in strings
    assert 'old "小さなサンプル"' in strings and 'new "A small sample"' in strings
    assert "Hello, [player_name]" not in (out / "game" / "script.rpy").read_text(encoding="utf-8")

    second = main(["translate", str(out)])
    assert second == 0
    # Captured by re-reading status instead of stdout: the cache skipped every line.
    status = main(["status", str(out)])
    assert status == 0
    from lantern.project import db_path
    from lantern.store import Store

    with Store(db_path(out)) as store:
        counts = store.counts()
    assert counts["translated"] == counts["total"]
    assert counts["flagged"] == 0
    assert counts["total"] == 14


def test_second_translate_reports_everything_skipped(tmp_path: Path, capsys):
    out = tmp_path / "sample-en"
    assert main(
        [
            "run",
            str(SAMPLE),
            "--out",
            str(out),
            "--provider",
            "mock",
            "--mock-map",
            str(SAMPLE / "mock_map.json"),
            "--glossary",
            str(SAMPLE / "glossary.json"),
        ]
    ) == 0
    capsys.readouterr()
    assert main(["translate", str(out)]) == 0
    output = capsys.readouterr().out
    assert "result: translated=0 skipped=14 flagged=0" in output


def test_export_edit_import_and_rebuild(tmp_path: Path):
    out = tmp_path / "sample-en"
    assert main(
        [
            "run",
            str(SAMPLE),
            "--out",
            str(out),
            "--provider",
            "mock",
            "--mock-map",
            str(SAMPLE / "mock_map.json"),
        ]
    ) == 0
    exported = tmp_path / "review.csv"
    assert main(["export", str(out), "--all", "-o", str(exported)]) == 0
    text = exported.read_text(encoding="utf-8-sig")
    assert "風が強い。" in text
    rows = []
    import csv

    with exported.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        if row["source"] == "風が強い。":
            row["translation"] = 'The wind is "strong".'
            row["status"] = "reviewed"
    with exported.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    assert main(["import", str(out), str(exported)]) == 0
    assert main(["build", str(out)]) == 0
    dialogue = (out / "game" / "tl" / "english" / "script.rpy").read_text(encoding="utf-8")
    assert 'The wind is \\"strong\\".' in dialogue
    assert main(["translate", str(out)]) == 0
    dialogue_after = (out / "game" / "tl" / "english" / "script.rpy").read_text(encoding="utf-8")
    assert 'The wind is \\"strong\\".' in dialogue_after

    flagged = tmp_path / "flagged.json"
    assert main(["export", str(out), "-o", str(flagged)]) == 0
    assert json.loads(flagged.read_text(encoding="utf-8")) == []


def test_compiled_only_games_are_reported_and_not_copied(tmp_path: Path, capsys):
    out = tmp_path / "should-not-exist"
    code = main(["run", str(RPYC), "--out", str(out), "--provider", "mock"])
    assert code == 2
    assert "does not decompile" in capsys.readouterr().out
    assert not out.exists()

    code = main(["run", str(RPA), "--out", str(out), "--provider", "mock"])
    assert code == 2
    assert "does not unpack" in capsys.readouterr().out
    assert not out.exists()


def test_bench_times_lines_without_writing_the_game(capsys):
    before = {path: path.read_bytes() for path in SAMPLE.rglob("*") if path.is_file()}
    code = main(
        [
            "bench",
            str(SAMPLE),
            "--provider",
            "mock",
            "--mock-map",
            str(SAMPLE / "mock_map.json"),
            "--limit",
            "2",
            "--no-terms",
            "--workers",
            "1",
        ]
    )
    assert code == 0
    after = {path: path.read_bytes() for path in SAMPLE.rglob("*") if path.is_file()}
    assert after == before
    assert not (SAMPLE / ".lantern").exists()
    logged = capsys.readouterr().out
    assert "lines/s" in logged
    assert "game files were not written" in logged


def test_refuses_to_modify_the_original_folder(tmp_path: Path):
    code = main(["run", str(SAMPLE), "--out", str(SAMPLE), "--provider", "mock"])
    assert code == 1
    assert not (SAMPLE / ".lantern").exists()
    nested = SAMPLE / "game" / "nested-out"
    code = main(["extract", str(SAMPLE), "--out", str(nested), "--provider", "mock"])
    assert code == 1
    assert not nested.exists()
