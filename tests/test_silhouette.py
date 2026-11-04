import struct
import unittest
import zlib

from babyai.ui.silhouette import alpha_rows, solid_rects


def png(width, height, alpha_at, color=6, depth=8, interlace=0, filters=None):
    """A hand-made PNG whose pixel (x, y) has alpha alpha_at(x, y). Every row is stored unfiltered
    unless `filters` says otherwise (a dict of row -> filter byte)."""
    raw = bytearray()
    for y in range(height):
        raw.append((filters or {}).get(y, 0))
        for x in range(width):
            raw += bytes((200, 10, 80, alpha_at(x, y)))

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    header = struct.pack(">IIBBBBB", width, height, depth, color, 0, 0, interlace)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b"")


def covered(rects, width, height):
    cells = set()
    for x, y, w, h in rects:
        cells |= {(i, j) for i in range(x, x + w) for j in range(y, y + h)}
    return cells


class AlphaRowsTests(unittest.TestCase):
    def test_reads_the_alpha_of_every_pixel(self):
        rows = alpha_rows(png(5, 3, lambda x, y: x * 10 + y))
        self.assertEqual(len(rows), 3)
        self.assertEqual(list(rows[0]), [0, 10, 20, 30, 40])
        self.assertEqual(list(rows[2]), [2, 12, 22, 32, 42])

    def test_other_layouts_are_declined_not_misread(self):
        self.assertIsNone(alpha_rows(png(4, 4, lambda x, y: 255, filters={2: 1})))  # a filtered row
        self.assertIsNone(alpha_rows(png(4, 4, lambda x, y: 255, interlace=1)))
        self.assertIsNone(alpha_rows(png(4, 4, lambda x, y: 255, color=2)))  # RGB, no alpha
        self.assertIsNone(alpha_rows(b"not a png"))
        self.assertIsNone(alpha_rows(png(4, 4, lambda x, y: 255)[:-30]))  # cut short


class SolidRectsTests(unittest.TestCase):
    def rects(self, width, height, solid, x0=0, cell_w=None):
        rows = alpha_rows(png(width, height, lambda x, y: 255 if solid(x, y) else 0))
        return solid_rects(rows, x0, cell_w or width)

    def test_a_block_becomes_one_rectangle(self):
        self.assertEqual(self.rects(8, 6, lambda x, y: 2 <= x < 5 and 1 <= y < 4), [(2, 1, 3, 3)])

    def test_exactly_the_solid_pixels_are_covered(self):
        solid = lambda x, y: (x - 6) ** 2 + (y - 6) ** 2 <= 20 or (x == 1 and y < 3)  # a blob and a stalk
        rects = self.rects(14, 14, solid)
        self.assertEqual(covered(rects, 14, 14), {(x, y) for x in range(14) for y in range(14) if solid(x, y)})

    def test_nothing_solid_gives_nothing(self):
        self.assertEqual(self.rects(6, 6, lambda x, y: False), [])

    def test_faint_pixels_are_not_part_of_her(self):
        faint = alpha_rows(png(4, 1, lambda x, y: (0, 24, 25, 255)[x]))
        self.assertEqual(solid_rects(faint, 0, 4), [(2, 0, 2, 1)])  # the cut-off is alpha > 24

    def test_a_cell_of_a_strip_only_sees_its_own_columns(self):
        solid = lambda x, y: x in (1, 2) or x in (9, 10, 11)  # cell 0: x 0..5, cell 1: x 6..11
        rows = alpha_rows(png(12, 2, lambda x, y: 255 if solid(x, y) else 0))
        self.assertEqual(solid_rects(rows, 0, 6), [(1, 0, 2, 2)])
        self.assertEqual(solid_rects(rows, 6, 6), [(3, 0, 3, 2)])

    def test_rows_with_different_runs_are_not_merged(self):
        got = self.rects(6, 2, lambda x, y: (x < 2) if y == 0 else (x < 4))
        self.assertEqual(sorted(got), [(0, 0, 2, 1), (0, 1, 4, 1)])


if __name__ == "__main__":
    unittest.main()
