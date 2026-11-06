# Lantern

Lantern is a local command-line translator for visual novel scripts. You point it at a game, it copies the game, and it fills in a translation with a model running on your machine.

It is an open alternative to the part of [MTool](https://mtool.app/) that translates text. MTool only supports some engines, and several of its translation models sit behind a subscription. Lantern keeps the script on disk, talks to a local [Ollama](https://ollama.com/) model by default, and does not modify the original game.

This version translates **Ren'Py** projects that still have `.rpy` source, and **RPG Maker MV/MZ** projects (`data/*.json`). Ren'Py output is a `game/tl/<language>/` folder. RPG Maker output is a patched copy of the data JSON.

## What it does

1. Copies the game to a folder you choose.
2. Reads dialogue, menu choices, and `_()` / `__()` strings from `.rpy` files. Each line keeps its speaker, file, and label.
3. Stores those lines in a SQLite database (`<copy>/.lantern/project.sqlite`) with the source, translation, status, and a hash of the source. A later run skips lines that are already translated.
4. Masks Ren'Py text tags (`{w}`, `{b}`), variables (`[player_name]`), and format codes (`%s`, `%(name)s`) before the model sees the line, then checks that every mask came back intact.
5. Translates in small batches with a glossary and a few preceding lines from the same scene. A failed batch is retried once. A line that still fails is flagged, and the lines that succeeded are not sent again.
6. Writes the translation into the copy. Ren'Py dialogue ids follow the engine's own rules (label plus an MD5 of the statement). RPG Maker lines are written back into the event or database field they came from.

`.rpyc`-only and `.rpa`-only games are reported and left alone. Lantern does not decompile bytecode or unpack archives.

## RPG Maker MV/MZ

Point Lantern at the project folder (the one that contains `data/System.json`):

```bat
lantern run C:\Games\MyRPG --out C:\Games\MyRPG-en --language english --limit 50
```

It reads Show Text lines (command 401), scrolling text (405), and choices (102), and it keeps the speaker and face from the Show Text command (101) with each line. It also reads actor, class, item, skill, weapon, armor, state, and enemy names and descriptions, map names, and the System terms (including the title, currency, elements, and battle messages). Switch names and variable names are left alone. They are editor identifiers.

The copy's `data/*.json` files are patched. An untouched snapshot stays in `<copy>/.lantern/source/data/` so a later extract still sees the original language. Build does not rewrite `Hakubox-Translate-*.json` when that plugin is present. Hakubox is a flat dictionary: one translation per exact source string, looked up on the whole message. That cannot keep a speaker, a face, or a second wording of the same line in another scene. After the JSON patch, Hakubox does not find the new text and leaves it as written, so the copy is one language. The in-game language menu will not turn it back into Japanese.

Control codes such as `\V[n]`, `\N[n]`, `\P[n]`, `\C[n]`, `\I[n]`, `\{`, `\}`, `\G`, and `%1` are masked and must come back unchanged. Speaker tags like `<e1>` (used by portrait plugins) are masked the same way.

The real game used to check this adapter is not in the repository. Tests use `tests/fixtures/rpg_mz`, a few original lines about a lighthouse.

## Windows setup

Lantern needs Python 3.10 or newer. Python 3.12 is a good choice on a laptop. Install it from <https://www.python.org/downloads/windows/> and leave the **py launcher** enabled. Install [Git for Windows](https://git-scm.com/download/win) as well. `pip` uses Git to install Lantern from the private repository.

Open PowerShell or Command Prompt:

```bat
py -3.12 -m pip install --upgrade pip
py -3.12 -m pip install "git+https://github.com/SKaMTo4hya/lantern.git"
lantern --help
```

`py -3` works too if 3.12 is not installed and another 3.10+ copy is. The GitHub repository is public.

Install Ollama from <https://ollama.com/download> and start it. On Windows it sits in the tray and serves `http://127.0.0.1:11434`.

The default model is `qwen3:8b` (Apache-2.0, about 5 GB at Q4). It fits an 8 GB GPU such as an RTX 5060. Qwen3 thinks unless the request turns that off, which makes every batch very slow, so Lantern sends `think: false` and drops any stray `<think>` block before it reads the JSON.

```bat
ollama pull qwen3:8b
```

Game text is sent only to that local host. A first quality check should translate a few dozen dialogue lines, not the whole game:

```bat
lantern run C:\Games\MyGame --out C:\Games\MyGame-en --language english --limit 50
```

`--limit 50` translates the first 50 dialogue lines and leaves every other line in the source language. Names, menus, and later dialogue stay as they were. Drop `--limit` when you want the rest. `--model` changes the Ollama tag if you pull a different one.

Then, in the copy, choose the language in the game's preferences. Ren'Py does not switch languages just because `tl/english` exists. The font in the game also has to contain the characters you translate into. Lantern does not edit fonts or redraw text that is baked into images.

From a local checkout, `py -3.12 -m pip install -e .` installs the same way, and `py -3.12 -m lantern` runs it without putting the script on `PATH`.

## Try the sample without a model

`examples/sample-renpy` is a tiny original scene, not a commercial game. The mock provider replays `mock_map.json`, so you can see a finished `tl/english` tree with no Ollama running.

```bat
py -m lantern run examples\sample-renpy --out sample-en --provider mock --mock-map examples\sample-renpy\mock_map.json --glossary examples\sample-renpy\glossary.json
```

That writes:

- `sample-en/game/tl/english/script.rpy` — dialogue, including the `voice` line and the explicit id `roof_careful`
- `sample-en/game/tl/english/lantern_strings.rpy` — character names, the window title, and menu choices
- `sample-en/.lantern/project.sqlite` — the cache

The original `examples/sample-renpy` is not modified. Running `lantern translate sample-en` again prints `skipped=14`.

## Commands

| Command | What it does |
| --- | --- |
| `lantern detect GAME` | Report Ren'Py, or explain that the scripts are only `.rpyc` / `.rpa` |
| `lantern extract GAME --out COPY` | Copy the game and fill the database |
| `lantern translate COPY` | Translate lines that are not done |
| `lantern build COPY` | Write `tl/<language>` from accepted lines |
| `lantern run GAME --out COPY` | Extract, translate, and build |
| `lantern status COPY` | Count lines by status |
| `lantern export COPY -o review.csv` | Flagged lines as CSV. Use `--all`, or `-o review.json` |
| `lantern import COPY review.csv` | Load edits. A row becomes `reviewed` unless its status column says otherwise |

Useful options: `--language english`, `--source-language japanese`, `--model qwen3:8b`, `--host`, `--glossary glossary.json`, `--batch-size 8`, `--context 4`, `--timeout 180`, `--limit 50`, `--force`.

`--limit N` translates only the first N dialogue lines, in project order, and writes only those into the copy. Use it to judge tone before a full run.

`--force` retranslates lines already marked translated. Lines you imported as `reviewed` are kept. Changing the glossary does not invalidate the cache; pass `--force` when you want old lines redone.

### Glossary

```json
{
  "entries": [
    {"source": "美咲", "target": "Misaki", "note": "heroine, she/her"},
    {"source": "学園", "target": "school"}
  ]
}
```

A plain `{"美咲": "Misaki"}` object works too. Only entries that appear in the current batch are sent.

### Review

Flagged lines failed the placeholder check, came back empty, echoed the Japanese source, or broke the line-break count. The model is asked once more for that failed batch only. Export the flagged rows, edit the `translation` column, import, and build:

```bat
lantern export sample-en -o review.csv
lantern import sample-en review.csv
lantern build sample-en
```

Build writes `translated` and `reviewed` lines. Flagged lines are left out, so the game shows the original text for those.

## Tests

```bat
py -m pip install -e ".[dev]"
py -m pytest
```

The tests use the sample project and a fake provider. They do not call Ollama.

## How a record is stored

Dialogue is not a single global "Japanese string to English string" map. The same line can be translated differently in two scenes. A dialogue id is the Ren'Py translation id (`start_636ae3f5`, or an explicit `id` written in the script). Menu choices and `_()` strings are separate rows; when they are written out, Ren'Py itself only allows one replacement per source string, so conflicting edits are reported in `.lantern/string-conflicts.json` and one of them is written.

Statuses are `untranslated`, `translated`, `reviewed`, `flagged`, and `stale` (the source text changed).

## Design notes

The research around MTool points at msg-tool, GalTransl, and a desktop UI as a larger product. This MVP is narrower on purpose:

- A Python CLI, because that is what this version needed to be, and it runs on Windows without a launcher SDK.
- A Ren'Py reader in this repo, instead of shelling out to the Ren'Py SDK, so `tl/<language>` files can be generated (and their ids checked) with no SDK installed.
- RPG Maker lines are patched in the data JSON of the copy. Hakubox_Translate's dictionary is not the output, because it is one string-to-string map with no scene.
- Ollama as the only live provider. `TranslationProvider` is the seam for an OpenAI-compatible adapter later. That adapter is not implemented.
- SQLite as the project store. CSV and JSON are the review format, not the cache.

## Roadmap

1. **KiriKiri**, with a maintained parser rather than a shared regex. Keep event references intact.
2. **msg-tool adapters** for static visual-novel formats, starting with one known `.ks` and one `.asb`. Publish what was extracted, and say so when encryption or bytecode is unsupported.
3. **Unity**, by writing XUnity.AutoTranslator text files rather than a new hook.
4. Later, not in the core path: an OpenAI-compatible provider, a side-by-side review screen, and a LunaTranslator setup for games that can only be hooked at runtime.

Out of scope until those exist: IL2CPP patching, OCR, text baked into images, generic KiriKiri Z decryption, every visual-novel archive, voice generation, and save editing.

## Known limits

- The Ren'Py reader understands say lines (including `extend`, attributes, `id`, `nointeract`, `with`, and a parenthetical), a `voice` line grouped with the next say, menus, labels, and `_()` / `__()` strings. It does not translate `text "Hello"` inside screens unless it is wrapped in `_()`, Python calls like `$ m("hello")`, or say lines whose dialogue is an expression.
- The RPG Maker reader does not translate plugin-command arguments, comments, switch names, or variable names. Troop and common-event *names* are not rewritten; the text inside their command lists is. A patched copy no longer switches back to the source language through Hakubox.
- Dialogue ids match current Ren'Py when the block is one or more `voice` lines plus a say. Other translatable statements (some `nvl` lines) are not included in the hash, and `config.tlid_only_considers_say` is not read. Unusual argument formatting can also change an id, because parentheses are kept with their spaces collapsed.
- Scripts are read in sorted path order. That matches Ren'Py for unique ids. Duplicate ids across files can get a different `_1` suffix if the game loads files in another order.
- `.rpyc` is not decompiled and `.rpa` is not unpacked. Decompile with the Ren'Py SDK or UnRen, unpack archives, then run Lantern on the `.rpy` files. Do not paste translations into `.rpyc` bytes.
- `Character("美咲")` without `_()` is reported and not written as a string translation. Ren'Py would not apply one. Wrap display names in `_()` in the game, or edit the copy's definitions yourself.
- String translations are one pair per source string for the whole game. Dialogue can differ by scene; menu text cannot.
- A translated copy still needs a language menu (or preferences) and a font that covers the target language. Line wrapping, image text, and encodings such as CP932 in non-Ren'Py engines are separate problems.
- If Ollama is down, the command stops after one retry. Lines already accepted stay saved; the unfinished batch is not flagged. Run `translate` again when the model is up.
- The mock provider only knows the map you give it. It is for tests and for the sample, not for a real game.

## License

MIT, for this program. It does not license any game you translate. Keep game files local, and do not ship a commercial game or its archives with your translation.
