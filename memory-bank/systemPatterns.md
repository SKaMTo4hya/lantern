# System patterns

- Engine adapters (`detect`, `extract`, `build`) sit behind `lantern.engines`. `RenPyAdapter` and `RpgMakerAdapter` are registered. `detect_project` picks the first adapter that can extract.
- RPG Maker records point at a JSON path (`rebuild.pointer`). Choices also list the matching `402` commands in `rebuild.mirrors`. Build writes the translation into the copy's `data/` file. Extract after that still reads `.lantern/source/data/`.
- Hakubox_Translate is detected and left as a dictionary. It is a flat string map and is not how Lantern applies lines.
- Records are per location, not a global source-to-translation map. Dialogue ids are Ren'Py translation ids (label + MD5 of canonical `get_code()`, or an explicit `id`).
- `voice` lines immediately before a say are part of that translation block and part of the digest.
- Menu choices and `_()` / `__()` strings are separate records. Build emits one `translate <lang> strings` block and warns if the same source has two translations.
- Placeholders `⟪TAG000⟫`, `⟪VAR000⟫`, `⟪FMT000⟫` are masked before the provider and must return once, in order.
- Providers implement `translate_batch`. Ollama is the live provider. Mock replays a JSON map. OpenAI-compatible is a later provider, not a class in the tree.
- Batches do not cross a label. Context is earlier lines in the same file and label. Only a failed batch or the failed lines inside it are retried, once.
- The SQLite file and glossary live in `<copy>/.lantern/`. Build rewrites only files listed in `built-files.txt`.
