# Active context

The MVP CLI is implemented and tested. `lantern run` on `examples/sample-renpy` with `--provider mock` writes `game/tl/english/script.rpy` and `lantern_strings.rpy` in a copy. 26 tests pass without Ollama.

Next engines, in order: RPG Maker MV/MZ and KiriKiri, then msg-tool formats, then Unity via XUnity translation files. An OpenAI-compatible provider is the translation-side follow-up.
