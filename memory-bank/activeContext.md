# Active context

Ren'Py and RPG Maker MV/MZ adapters are implemented. RPG Maker build patches `data/*.json` in the copy and keeps an untranslated snapshot in `.lantern/source/data/`. Hakubox dictionaries are not the output.

A local extract of the private MZ sample (not in git) produced 6959 records. Tests use `tests/fixtures/rpg_mz` only.

Default Ollama model is `qwen3:8b` with thinking disabled. `--batch-size` defaults to 32 and `--workers` to 4. Ollama requests use a JSON schema. The parser also accepts string arrays, `{id,text}`, number-keyed objects, and fenced JSON. A failed batch larger than 8 lines is split in half before singles, and the log includes a raw snippet. Punctuation, numbers, and short names may match the source. `--terms` (default on) translates repeated honorifics and short phrases once. Exact duplicate lines are translated once. `lantern bench` times N lines without writing the game.

Next: KiriKiri, then msg-tool formats, then Unity. An OpenAI-compatible provider is still not built.
