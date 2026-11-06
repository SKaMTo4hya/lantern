import json
from pathlib import Path

from lantern.glossary import GlossaryEntry, load_glossary, select_glossary
from lantern.mask import mask_text
from lantern.models import BatchRequest, Record, source_hash
from lantern.providers.base import ProviderResponseError, ProviderUnavailable, TranslationProvider
from lantern.providers.ollama import parse_translations
from lantern.store import Store
from lantern.translate import TranslationStopped, translate_project


def _record(record_id: str, source: str, ordinal: int, scene: str = "start", kind: str = "dialogue") -> Record:
    return Record(
        id=record_id,
        engine="renpy",
        kind=kind,
        source=source,
        translation=None,
        speaker="m",
        file="script.rpy",
        scene=scene,
        line=ordinal + 1,
        source_hash=source_hash(source),
        status="untranslated",
        ordinal=ordinal,
        rebuild={},
    )


class MapProvider(TranslationProvider):
    name = "map"
    model = "map"

    def __init__(self, mapping: dict[str, str], fail_ids: set[str] | None = None, fail_times: int = 1):
        self.mapping = mapping
        self.fail_ids = fail_ids or set()
        self.fail_times = fail_times
        self.seen: dict[str, int] = {}
        self.calls: list[list[str]] = []
        self.requests: list[BatchRequest] = []

    def translate_batch(self, request: BatchRequest) -> dict[str, str]:
        self.calls.append([line.id for line in request.lines])
        self.requests.append(request)
        output: dict[str, str] = {}
        for line in request.lines:
            self.seen[line.id] = self.seen.get(line.id, 0) + 1
            if line.id in self.fail_ids and self.seen[line.id] <= self.fail_times:
                output[line.id] = "dropped the codes"
            else:
                output[line.id] = mask_text(self.mapping[line.source]).text
        return output


def _run(tmp_path: Path, records: list[Record], provider: TranslationProvider, **kwargs):
    store_path = tmp_path / "project.sqlite"
    with Store(store_path) as store:
        store.replace_extracted(records)
        stats = translate_project(
            tmp_path,
            store,
            provider,
            glossary=kwargs.get("glossary", []),
            source_language="japanese",
            target_language="english",
            batch_size=kwargs.get("batch_size", 8),
            context_lines=kwargs.get("context_lines", 4),
            force=kwargs.get("force", False),
            limit=kwargs.get("limit"),
        )
        saved = store.list_records()
    return stats, saved


def test_retry_only_the_line_that_failed_then_cache_skips_it(tmp_path: Path):
    good = _record("good", "こんにちは、[player_name]。", 0)
    bad = _record("bad", "今日は{w}いい天気。", 1)
    mapping = {
        good.source: "Hello, [player_name].",
        bad.source: "The weather is {w}nice today.",
    }
    provider = MapProvider(mapping, fail_ids={"bad"}, fail_times=1)
    stats, saved = _run(tmp_path, [good, bad], provider)
    assert stats.translated == 2
    assert stats.flagged == 0
    assert provider.calls == [["good", "bad"], ["bad"]]
    by_id = {record.id: record for record in saved}
    assert by_id["good"].translation == "Hello, [player_name]."
    assert by_id["bad"].translation == "The weather is {w}nice today."
    assert by_id["good"].status == "translated"

    provider.calls.clear()
    with Store(tmp_path / "project.sqlite") as store:
        stats2 = translate_project(
            tmp_path,
            store,
            provider,
            glossary=[],
            source_language="japanese",
            target_language="english",
            batch_size=8,
            context_lines=4,
        )
    assert provider.calls == []
    assert stats2.translated == 0
    assert stats2.skipped == 2


def test_failed_line_is_flagged_after_one_retry_without_redoing_the_sibling(tmp_path: Path):
    good = _record("good", "こんにちは。", 0)
    bad = _record("bad", "名前は[name]です。", 1)
    provider = MapProvider({good.source: "Hello.", bad.source: "The name is [name]."}, fail_ids={"bad"}, fail_times=2)
    stats, saved = _run(tmp_path, [good, bad], provider)
    assert provider.calls == [["good", "bad"], ["bad"]]
    assert stats.translated == 1
    assert stats.flagged == 1
    by_id = {record.id: record for record in saved}
    assert by_id["good"].status == "translated"
    assert by_id["bad"].status == "flagged"
    assert "placeholders" in (by_id["bad"].error or "")


def test_only_the_failing_batch_is_retried(tmp_path: Path):
    first = _record("a", "おはよう。", 0, scene="one")
    second = _record("b", "おやすみ。", 1, scene="two")

    class SecondBatchBroken(TranslationProvider):
        name = "broken"
        model = "broken"

        def __init__(self):
            self.calls: list[list[str]] = []

        def translate_batch(self, request: BatchRequest) -> dict[str, str]:
            self.calls.append([line.id for line in request.lines])
            if request.lines[0].id == "b":
                raise ProviderResponseError("not json")
            return {line.id: "Good morning." for line in request.lines}

    provider = SecondBatchBroken()
    stats, saved = _run(tmp_path, [first, second], provider)
    assert provider.calls == [["a"], ["b"], ["b"]]
    assert stats.translated == 1
    assert stats.flagged == 1
    by_id = {record.id: record for record in saved}
    assert by_id["a"].status == "translated"
    assert by_id["b"].status == "flagged"


def test_unreachable_host_stops_without_flagging(tmp_path: Path):
    record = _record("a", "こんにちは。", 0)

    class Down(TranslationProvider):
        name = "down"
        model = "down"

        def translate_batch(self, request: BatchRequest) -> dict[str, str]:
            raise ProviderUnavailable("connection refused")

    try:
        _run(tmp_path, [record], Down())
    except TranslationStopped as exc:
        assert "connection refused" in str(exc)
    else:
        raise AssertionError("expected the job to stop")
    with Store(tmp_path / "project.sqlite") as store:
        saved = store.list_records()
    assert saved[0].status == "untranslated"


def test_context_and_glossary_follow_the_scene(tmp_path: Path):
    earlier = _record("earlier", "美咲だよ。", 0)
    current = _record("current", "学園に行こう。", 1)
    other = _record("other", "別の場面。", 2, scene="roof")
    mapping = {earlier.source: "It's Misaki.", current.source: "Let's go to school.", other.source: "Another scene."}
    glossary = [
        GlossaryEntry("美咲", "Misaki", "heroine"),
        GlossaryEntry("学園", "school"),
        GlossaryEntry("別", "other"),
    ]
    provider = MapProvider(mapping)
    _run(tmp_path, [earlier, current, other], provider, glossary=glossary, batch_size=1, context_lines=2)
    by_first = {request.lines[0].id: request for request in provider.requests}
    assert by_first["earlier"].context == []
    assert by_first["earlier"].glossary[0].source == "美咲"
    assert [entry.source for entry in by_first["current"].glossary] == ["学園"]
    assert by_first["current"].context[0]["translation"] == "It's Misaki."
    assert by_first["other"].context == []
    assert [entry.source for entry in by_first["other"].glossary] == ["別"]


def test_glossary_selection_ignores_absent_terms():
    entries = [GlossaryEntry("美咲", "Misaki"), GlossaryEntry("学園", "school"), GlossaryEntry("健一", "Kenichi")]
    chosen = select_glossary(entries, ["美咲、学園の門で待ってたよ。"])
    assert [entry.source for entry in chosen] == ["美咲", "学園"]


def test_parse_ollama_json_payload():
    parsed = parse_translations('{"translations":[{"id":"a","translation":"Hello ⟪TAG000⟫"}]}')
    assert parsed == {"a": "Hello ⟪TAG000⟫"}
    fenced = parse_translations('```json\n{"translations":[{"id":"b","text":"Hi"}]}\n```')
    assert parsed and fenced["b"] == "Hi"
    wrapped = parse_translations(
        '<think>The model reasoned here.</think>\n{"translations":[{"id":"c","translation":"Hello"}]}'
    )
    assert wrapped["c"] == "Hello"


def test_ollama_chat_payload_turns_thinking_off():
    from lantern.models import BatchRequest, PromptLine
    from lantern.project import default_config
    from lantern.providers.ollama import build_chat_payload

    assert default_config()["model"] == "qwen3:8b"
    request = BatchRequest(
        lines=[PromptLine(id="a", kind="dialogue", speaker=None, source="こんにちは", masked="こんにちは")],
        context=[],
        glossary=[],
        source_language="Japanese",
        target_language="English",
    )
    payload = build_chat_payload("qwen3:8b", request)
    assert payload["think"] is False
    assert payload["model"] == "qwen3:8b"
    assert payload["stream"] is False


def test_batch_mismatch_falls_back_to_single_lines(tmp_path: Path):
    from lantern.providers.base import ProviderResponseError

    records = [_record(f"d{index}", f"行{index}", index) for index in range(4)]

    class SplitOnBatch(TranslationProvider):
        name = "split"
        model = "split"

        def __init__(self):
            self.calls: list[list[str]] = []

        def translate_batch(self, request: BatchRequest) -> dict[str, str]:
            ids = [line.id for line in request.lines]
            self.calls.append(ids)
            if len(ids) > 1:
                raise ProviderResponseError("count mismatch")
            return {ids[0]: f"Line {ids[0]}"}

    provider = SplitOnBatch()
    stats, saved = _run(tmp_path, records, provider, batch_size=4)
    assert stats.translated == 4
    assert stats.flagged == 0
    assert provider.calls[0] == ["d0", "d1", "d2", "d3"]
    assert provider.calls.count(provider.calls[0]) == 1
    assert ["d0"] in provider.calls and ["d3"] in provider.calls
    assert {record.translation for record in saved} == {f"Line {record.id}" for record in saved}


def test_placeholder_failure_retries_only_that_line_then_a_smaller_batch(tmp_path: Path):
    first = _record("good", "こんにちは。", 0)
    bad = _record("bad", "今日は{w}いい。", 1)
    mapping = {first.source: "Hello.", bad.source: "Today is {w}fine."}
    provider = MapProvider(mapping, fail_ids={"bad"}, fail_times=1)
    stats, saved = _run(tmp_path, [first, bad], provider, batch_size=32)
    assert provider.calls == [["good", "bad"], ["bad"]]
    assert stats.translated == 2
    assert {record.status for record in saved} == {"translated"}


def test_pinned_names_are_not_translated_again(tmp_path: Path):
    name = _record("name", "芙莉莲", 0, kind="name")
    name.rebuild = {"command": 101}
    line = _record("line", "芙莉莲，走吧。", 1)
    line.speaker = "芙莉莲"
    provider = MapProvider({line.source: "Let's go.", "芙莉莲": "Fairy Lady"})
    stats, saved = _run(
        tmp_path,
        [name, line],
        provider,
        glossary=[GlossaryEntry("芙莉莲", "Frieren")],
    )
    sent = [line_id for call in provider.calls for line_id in call]
    assert "glossary-name:0" not in sent
    assert sent == ["line"]
    by_id = {record.id: record for record in saved}
    assert by_id["name"].translation == "Frieren"
    assert by_id["line"].translation == "Let's go."
    assert stats.names_translated == 1
    request = provider.requests[-1]
    assert request.glossary[0].source == "芙莉莲"
    assert request.glossary[0].target == "Frieren"


def test_unpinned_name_is_translated_once_and_reused(tmp_path: Path):
    name = _record("name", "菲伦<e1>", 0, kind="name")
    name.rebuild = {"command": 101}
    line = _record("line", "菲伦看了过来。", 1)
    line.speaker = "菲伦<e1>"
    provider = MapProvider({"菲伦": "Fern", line.source: "Fern looked over."})
    _stats, saved = _run(tmp_path, [name, line], provider)
    assert ["glossary-name:0"] in provider.calls
    assert provider.calls.count(["glossary-name:0"]) == 1
    by_id = {record.id: record for record in saved}
    assert by_id["name"].translation == "Fern<e1>"
    assert by_id["line"].translation == "Fern looked over."
    cached = json.loads((tmp_path / ".lantern" / "auto-glossary.json").read_text(encoding="utf-8"))
    assert cached["菲伦"] == "Fern"


def test_glossary_text_file(tmp_path: Path):
    path = tmp_path / "names.txt"
    path.write_text("# pins\n芙莉莲=Frieren\n菲伦=Fern\n", encoding="utf-8")
    entries = load_glossary(path)
    assert [(entry.source, entry.target) for entry in entries] == [("芙莉莲", "Frieren"), ("菲伦", "Fern")]


def test_duplicate_speakers_are_one_name_batch(tmp_path: Path, capsys):
    records = []
    mapping = {"芙莉莲": "Frieren", "菲伦": "Fern"}
    for index in range(6):
        speaker = "芙莉莲" if index % 2 == 0 else "菲伦<e1>"
        record = _record(f"d{index}", f"台词{index}。", index)
        record.speaker = speaker
        mapping[record.source] = f"Line {index}."
        records.append(record)
    provider = MapProvider(mapping)
    _run(tmp_path, records, provider, batch_size=32)
    name_calls = [call for call in provider.calls if call and call[0].startswith("glossary-name:")]
    assert name_calls == [["glossary-name:0", "glossary-name:1"]]
    output = capsys.readouterr().out
    assert "progress: names 0/2 unique" in output
    assert "progress: translate" in output


def test_limit_only_names_speakers_in_those_lines(tmp_path: Path):
    first = _record("d1", "芙莉莲点头。", 0)
    first.speaker = "芙莉莲"
    second = _record("d2", "菲伦摇头。", 1)
    second.speaker = "菲伦"
    provider = MapProvider({"芙莉莲": "Frieren", "菲伦": "Fern", first.source: "Frieren nods.", second.source: "Fern shakes her head."})
    _run(tmp_path, [first, second], provider, limit=1)
    sent = [line_id for call in provider.calls for line_id in call]
    assert "glossary-name:0" in sent
    assert sent.count("glossary-name:0") == 1
    assert all(call == ["glossary-name:0"] or "d1" in call for call in provider.calls)
    assert "d2" not in sent


def test_limit_translates_only_the_first_dialogue_lines(tmp_path: Path):
    first = _record("d1", "おはよう。", 0)
    menu = _record("menu", "はい", 1, kind="choice")
    second = _record("d2", "こんにちは。", 2)
    third = _record("d3", "さようなら。", 3)
    mapping = {record.source: "EN " + record.source for record in (first, menu, second, third)}
    provider = MapProvider(mapping)
    stats, saved = _run(tmp_path, [first, menu, second, third], provider, limit=2)
    assert stats.dialogue_total == 3
    assert stats.dialogue_selected == 2
    assert stats.translated == 2
    by_id = {record.id: record for record in saved}
    assert by_id["d1"].translation == "EN おはよう。"
    assert by_id["d2"].translation == "EN こんにちは。"
    assert by_id["d3"].status == "untranslated"
    assert by_id["menu"].status == "untranslated"
    assert [line_id for call in provider.calls for line_id in call] == ["d1", "d2"]


def test_source_change_marks_a_record_stale(tmp_path: Path):
    record = _record("a", "こんにちは。", 0)
    with Store(tmp_path / "project.sqlite") as store:
        store.replace_extracted([record])
        store.save_result("a", translation="Hello.", status="translated", error=None, provider="mock", model="mock")
        changed = _record("a", "さようなら。", 0)
        store.replace_extracted([changed])
        saved = store.list_records()
        pending = store.pending()
    assert saved[0].status == "stale"
    assert saved[0].translation == "Hello."
    assert [item.id for item in pending] == ["a"]
