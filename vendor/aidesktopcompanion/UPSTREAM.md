# Vendored: aidesktopcompanion

- Source: https://github.com/theRadicalSoftware/AIDesktopCompanion
- Upstream commit: 76579f49ff0089c41d98bb9e69d5c5bde191ad4a (2026-05-12)
- License: MIT (see LICENSE)
- Imported by copying the tracked files at that commit; nothing was edited.

## Included
Everything: Python/PyQt6 runtime (`run.py`, `hatchpet/`), templates, docs, examples, its own neutral `starter-buddy` pet and thought-bubble/usage-meter art. The README describes these as "permissive demo assets" and the repo license is MIT, but it has no per-asset license, so check the art before reusing it outside this repo.

## Excluded, and why
- Nothing excluded. Note it needs PyQt6 (not Tk) and targets Linux/X11.

## Ideas worth porting into BabyAI (as new code)
- A pet-pack format (manifest + fixed-cell sprite atlas, see `docs/PET_PACK_SPEC.md`) so sprites are swappable from a folder.
- Walking / drag-and-drop reactions.
- An animated thought bubble that shows task status.

## Merge guidance for BabyAI

BabyAI is Python 3.11+ with a Tkinter overlay (`babyai/ui/app.py`) and is MIT licensed. This folder is a
vendored, read-only copy of another project.

- Do not switch GUI framework or add unrelated dependencies to BabyAI; port behaviors into `babyai/ui/app.py`
  (or a small new module) as new code instead of importing this code.
- Keep this folder's license text and any `Required Notice:` lines intact. Do not relabel it as MIT.
- Do not add the excluded artwork listed above. Use your own sprites.
- Keep the change reviewable: one project per merge, and the existing tests must still pass.
