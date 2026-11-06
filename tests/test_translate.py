from pathlib import Path

from lantern.glossary import GlossaryEntry, select_glossary
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
