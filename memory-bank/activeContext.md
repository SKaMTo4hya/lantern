# Active context

Ren'Py and RPG Maker MV/MZ adapters are implemented. RPG Maker build patches `data/*.json` in the copy and keeps an untranslated snapshot in `.lantern/source/data/`. Hakubox dictionaries are not the output.

A local extract of the private MZ sample (not in git) produced 6959 records. Tests use `tests/fixtures/rpg_mz` only.

Default Ollama model is `qwen3:8b` with thinking disabled. `--batch-size` defaults to 32 and `--workers` to 4. The name pass sends distinct speakers in batches, and `--limit` only includes speakers from those dialogue lines. Progress prints during names and dialogue. A repeated `run` reuses asset folders.

Next: KiriKiri, then msg-tool formats, then Unity. An OpenAI-compatible provider is still not built.
