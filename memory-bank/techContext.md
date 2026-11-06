# Tech context

- Python 3.10+, stdlib only at runtime (`sqlite3`, `urllib`, `argparse`).
- Package `lantern/` at the repo root. `python -m lantern` works from the checkout. `pip install -e .` adds the `lantern` script.
- Tests: `pytest` (`pip install -e ".[dev]"`). Ren'Py sample: `examples/sample-renpy`. RPG Maker fixture: `tests/fixtures/rpg_mz`. The real MZ sample stays outside the repo.
- Default model `qwen3:8b`, host `http://127.0.0.1:11434`, language `english`, batch size 32, workers 4, context 4, terms on. Ollama chat requests set `think: false` and `format` to a translations JSON schema. `--limit N` translates names from those lines, then only the first N dialogue lines. `lantern bench GAME --limit N` does not write the game. Reruns reuse an existing `--out`.
- Ren'Py scripts are UTF-8, with a cp932 fallback. `.rpyc` and `.rpa` are detected and not parsed.
