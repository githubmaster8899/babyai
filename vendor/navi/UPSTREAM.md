# Vendored: navi

- Source: https://github.com/RedSky09/Navi
- Upstream commit: e52a14590d2e521b8bee494bfe4fd584be7fa020 (2026-04-18)
- License: README states "MIT License - feel free to use, modify, and share", but the repository has NO LICENSE file and no copyright line. Treat the terms as unclear until the author adds one.
- Imported by copying the tracked files at that commit; nothing was edited.

## Included
Python source (`main.py`, `core/`, `build.py`), `README.md` (kept because it carries the license statement and the asset credits), `.gitignore`.

## Excluded, and why
- `assets/` and `pets/`: sprites, UI art and fonts. The README credits RiLi_XL's Otter Sprite Pack, dani maccari's Pixel Checkers and
  Cup Nooble's Sprout Lands UI Pack (all on itch.io) and states no terms for them; the fonts are not credited at all. The app will not run
  without them; restore from upstream only if you accept those terms.

## Ideas worth porting into BabyAI (as new code)
- Chat bubble flow: Talk -> type -> short (1-2 sentence) reply through a local Ollama model.
- Settings for name, personality and language; a capped memory with a summary of past sessions.
- Right-click menu (Talk, Play, Sleep, Settings, Quit).

## Merge guidance for BabyAI

BabyAI is Python 3.11+ with a Tkinter overlay (`babyai/ui/app.py`) and is MIT licensed. This folder is a
vendored, read-only copy of another project.

- Do not switch GUI framework or add unrelated dependencies to BabyAI; port behaviors into `babyai/ui/app.py`
  (or a small new module) as new code instead of importing this code.
- Keep this folder's license text and any `Required Notice:` lines intact. Do not relabel it as MIT.
- Do not add the excluded artwork listed above. Use your own sprites.
- Keep the change reviewable: one project per merge, and the existing tests must still pass.
