"""Turn a sprite's transparency into rectangles, so a window can be clipped to her outline.

Pure Python, no Tk. It reads the scaled strips that babyai.ui.sprites caches: Tk writes those as
8-bit RGBA, not interlaced, with every row stored unfiltered, so a pixel's alpha can be found by
slicing the decompressed bytes. A PNG that is not in that layout is simply not handled (None),
and the caller carries on without a silhouette.

This is generic image code (MIT, like the rest of BabyAI). The alpha cut-off below matches the one
Capri uses for its own hit-tests; the sprites it is run on are Capri's (see NOTICE).
"""

from __future__ import annotations

import re
import struct
import zlib

ALPHA_SOLID = 24  # a pixel counts as part of her above this alpha (the same cut-off Capri uses for hit-tests)
_SOLID = bytes(0 if value <= ALPHA_SOLID else 1 for value in range(256))
_RUN = re.compile(rb"\x01+")

Rect = tuple[int, int, int, int]  # x, y, width, height


def alpha_rows(png: bytes) -> list[bytes] | None:
    """One bytes object of alpha values per pixel row, or None if this isn't a PNG we can read quickly."""
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    header = None
    data = bytearray()
    pos = 8
    try:
        while pos + 8 <= len(png):
            length, kind = struct.unpack(">I4s", png[pos:pos + 8])
            body = png[pos + 8:pos + 8 + length]
            if kind == b"IHDR":
                header = struct.unpack(">IIBBBBB", body)
            elif kind == b"IDAT":
                data += body
            elif kind == b"IEND":
                break
            pos += 12 + length
        if header is None:
            return None
        width, height, depth, color, _compression, _filter, interlace = header
        if (depth, color, interlace) != (8, 6, 0):  # 8-bit RGBA, not interlaced
            return None
        raw = zlib.decompress(bytes(data))
    except (struct.error, zlib.error):
        return None
    stride = 1 + 4 * width
    if len(raw) != stride * height:
        return None
    rows = []
    for y in range(height):
        start = y * stride
        if raw[start] != 0:  # a filtered row: not worth unfiltering here
            return None
        rows.append(raw[start + 4:start + stride:4])  # every 4th byte from the first pixel's alpha
    return rows


def solid_rects(rows: list[bytes], x0: int, width: int) -> list[Rect]:
    """Rectangles covering the solid pixels of columns [x0, x0 + width); equal runs on neighbouring rows are merged."""
    done: list[Rect] = []
    open_runs: dict[tuple[int, int], int] = {}  # (x, run width) -> row it started on
    for y, row in enumerate(rows):
        cut = row[x0:x0 + width].translate(_SOLID)
        runs = {(m.start(), m.end() - m.start()) for m in _RUN.finditer(cut)}
        for run in [r for r in open_runs if r not in runs]:
            start = open_runs.pop(run)
            done.append((run[0], start, run[1], y - start))
        for run in runs:
            open_runs.setdefault(run, y)
    for (x, w), start in open_runs.items():
        done.append((x, start, w, len(rows) - start))
    return done
