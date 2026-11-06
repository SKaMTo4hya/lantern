# Progress

Works:

- Ren'Py extract of say lines, voice+say blocks, menus, `_()` strings, labels, speakers, and explicit ids
- Official Ren'Py id vectors (`start_636ae3f5`, `start_bd1ad9e1`, `start_9e949aac`)
- SQLite cache, stale-on-source-change, skip of translated and reviewed lines
- Mask, restore, one retry, flag, batch isolation, glossary selection, scene context
- Copy-only output, `.rpyc` / `.rpa` refusal, CSV and JSON review round-trip
- Ollama client (untested against a live daemon) and mock provider

Limits still in force: no SDK-less decompile or RPA unpack, no screen text without `_()`, no RPG Maker / KiriKiri / Unity, no OpenAI provider, no font or image-text handling. See README.
