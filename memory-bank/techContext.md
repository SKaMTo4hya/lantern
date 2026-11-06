# Tech context

- Python 3.10+, stdlib only at runtime (`sqlite3`, `urllib`, `argparse`).
- Package `lantern/` at the repo root. `python -m lantern` works from the checkout. `pip install -e .` adds the `lantern` script.
- Tests: `pytest` (`pip install -e ".[dev]"`). Ren'Py sample: `examples/sample-renpy`. RPG Maker fixture: `tests/fixtures/rpg_mz`. The real MZ sample stays outside the repo.
- Default model `qwen2.5:7b-instruct`, host `http://127.0.0.1:11434`, language `english`, batch size 8, context 4.
- Ren'Py scripts are UTF-8, with a cp932 fallback. `.rpyc` and `.rpa` are detected and not parsed.
