# Active context

Ren'Py and RPG Maker MV/MZ adapters are implemented. RPG Maker build patches `data/*.json` in the copy and keeps an untranslated snapshot in `.lantern/source/data/`. Hakubox dictionaries are not the output.

A local extract of the private MZ sample (not in git) produced 6959 records. Tests use `tests/fixtures/rpg_mz` only.

Default Ollama model is `qwen3:8b` with thinking disabled. `--batch-size` defaults to 32 and `--workers` to 4. A repeated `run` to the same `--out` reuses asset folders. Character names are translated once, with user pins such as `芙莉莲=Frieren` winning. `--limit N` still caps dialogue lines.

Next: KiriKiri, then msg-tool formats, then Unity. An OpenAI-compatible provider is still not built.
