# Project brief

Lantern is a local CLI for translating visual novel scripts. It is an open alternative to MTool's translation path: no subscription models, and a pluggable engine adapter instead of one closed engine list.

MVP scope that is in the tree:

- Python CLI, Ren'Py adapter first
- SQLite record store used as a translation cache
- Ollama provider by default, plus a mock provider for tests
- Mask and restore Ren'Py tags, variables, and format codes
- Write `game/tl/<language>/` into a copy of the game
- CSV/JSON export of flagged lines and re-import of edits

Not in this version: RPG Maker, KiriKiri, msg-tool, Unity, an OpenAI-compatible provider, a review UI.
