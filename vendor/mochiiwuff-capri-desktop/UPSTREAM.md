# Vendored: mochiiwuff-capri-desktop

- Source: https://github.com/mochiiwuff/capri-desktop
- Upstream commit: 3e6f529e6875856869789265786ab3e3df14a293 (2026-10-28)
- License: PolyForm Noncommercial 1.0.0 for the code (MIT for versions up to 0.4.3); see LICENSE. The character artwork is explicitly NOT covered.
- Imported by copying the tracked files at that commit; nothing was edited.

## Included
Electron/JS source (`src/`, `hooks/`, `scripts/`, `tools/`, `tests/`), both READMEs, `LICENSE`, `package.json`.

## Excluded, and why
- `assets/capri/`, `assets/icons/`, `docs/`: frames from the show and fan art that belong to CD PROJEKT RED, Studio Trigger and Netflix (see its README). The README says they are not covered by the code license. The app will not look right without art, and READMEs link to missing images.

## Ideas worth porting into BabyAI (as new code)
- A ticker over the figure showing the current action (e.g. `RUNS list_directory`).
- A `THINKING… 45 s` counter after ~15 s of silence.
- Falling asleep after ~10 idle minutes; waking when the mouse comes near.
- Looking toward the mouse when it is close.

## Merge guidance for BabyAI

BabyAI is Python 3.11+ with a Tkinter overlay (`babyai/ui/app.py`) and is MIT licensed. This folder is a
vendored, read-only copy of another project.

- Do not switch GUI framework or add unrelated dependencies to BabyAI; port behaviors into `babyai/ui/app.py`
  (or a small new module) as new code instead of importing this code.
- Keep this folder's license text and any `Required Notice:` lines intact. Do not relabel it as MIT.
- Do not add the excluded artwork listed above. Use your own sprites.
- Keep the change reviewable: one project per merge, and the existing tests must still pass.
