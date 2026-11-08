"""Clip an X11 window to a set of rectangles (the X Shape extension), so only those show.

Tk cannot do per-pixel transparency on X11, but the server can cut a window to a shape: what is
outside it is not drawn and does not take clicks. Used with babyai.ui.silhouette so that Capri
floats on the desktop instead of sitting on a dark panel. Everything here is best-effort:
``XShape.create`` returns None whenever it cannot work (not X11, no libX11/libXext, no Shape
extension, ...) and the overlay keeps its translucent panel.

Applies to X11 and to XWayland windows; how a Wayland compositor treats a shaped XWayland window
is up to the compositor.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
from typing import Iterable

SHAPE_BOUNDING, SHAPE_INPUT = 0, 2
SHAPE_SET, UNSORTED = 0, 0


class _XRectangle(ctypes.Structure):
    _fields_ = [("x", ctypes.c_short), ("y", ctypes.c_short), ("width", ctypes.c_ushort), ("height", ctypes.c_ushort)]


def _library(name: str, fallback: str) -> ctypes.CDLL:
    return ctypes.CDLL(ctypes.util.find_library(name) or fallback)


class XShape:
    def __init__(self, x11: ctypes.CDLL, xext: ctypes.CDLL, display: int, windows: tuple[int, ...]):
        self._x11, self._xext, self._display, self.windows = x11, xext, display, windows

    @classmethod
    def create(cls, windows: Iterable[int]) -> "XShape | None":
        ids = tuple(sorted({int(w) for w in windows if w}))
        if not ids or not os.environ.get("DISPLAY"):
            return None
        try:
            x11 = _library("X11", "libX11.so.6")
            xext = _library("Xext", "libXext.so.6")
            x11.XOpenDisplay.restype = ctypes.c_void_p
            x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
            x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
            x11.XFlush.argtypes = [ctypes.c_void_p]
            xext.XShapeQueryExtension.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
            xext.XShapeCombineRectangles.argtypes = [
                ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                ctypes.POINTER(_XRectangle), ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ]
            xext.XShapeGetRectangles.restype = ctypes.POINTER(_XRectangle)
            xext.XShapeGetRectangles.argtypes = [
                ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
            ]
            x11.XQueryTree.argtypes = [
                ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
                ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)), ctypes.POINTER(ctypes.c_uint),
            ]
            x11.XFree.argtypes = [ctypes.c_void_p]
            display = x11.XOpenDisplay(None)
            if not display:
                return None
            events, errors = ctypes.c_int(), ctypes.c_int()
            if not xext.XShapeQueryExtension(display, ctypes.byref(events), ctypes.byref(errors)):
                x11.XCloseDisplay(display)
                return None
            shape = cls(x11, xext, display, ids)
            shape.windows = shape._with_toplevel(ids)
            return shape
        except (OSError, AttributeError):
            return None

    def _with_toplevel(self, windows: tuple[int, ...]) -> tuple[int, ...]:
        """Tk puts each toplevel inside a wrapper window, and the wrapper is what the screen shows,
        so the shape has to go on every window from the given one up to (not including) the root."""
        found: set[int] = set(windows)
        for window in windows:
            for _ in range(8):  # a toplevel is never nested this deep
                root, parent, children, count = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.POINTER(ctypes.c_ulong)(), ctypes.c_uint()
                if not self._x11.XQueryTree(self._display, window, ctypes.byref(root), ctypes.byref(parent), ctypes.byref(children), ctypes.byref(count)):
                    break
                if children:
                    self._x11.XFree(ctypes.cast(children, ctypes.c_void_p))
                if not parent.value or parent.value == root.value:
                    break
                found.add(parent.value)
                window = parent.value
        return tuple(sorted(found))

    def set_rects(self, rects: list[tuple[int, int, int, int]]) -> None:
        """Show only these (x, y, width, height) rectangles of the window. An empty list is ignored
        (an empty shape would make the window vanish)."""
        rects = [(x, y, w, h) for x, y, w, h in rects if w > 0 and h > 0]
        if not rects or self._display is None:
            return
        array = (_XRectangle * len(rects))(*[_XRectangle(x, y, w, h) for x, y, w, h in rects])
        for window in self.windows:
            for kind in (SHAPE_BOUNDING, SHAPE_INPUT):
                self._xext.XShapeCombineRectangles(self._display, window, kind, 0, 0, array, len(rects), SHAPE_SET, UNSORTED)
        self._x11.XFlush(self._display)

    def get_rects(self, window: int | None = None) -> list[tuple[int, int, int, int]]:
        """The bounding rectangles the server currently has for a window (used by tests)."""
        count, ordering = ctypes.c_int(), ctypes.c_int()
        found = self._xext.XShapeGetRectangles(self._display, window or self.windows[0], SHAPE_BOUNDING, ctypes.byref(count), ctypes.byref(ordering))
        result = [(found[i].x, found[i].y, found[i].width, found[i].height) for i in range(count.value)] if found else []
        if found:
            self._x11.XFree(ctypes.cast(found, ctypes.c_void_p))
        return result

    def close(self) -> None:
        if self._display is not None:
            self._x11.XCloseDisplay(self._display)  # the shape stays on the windows until they are destroyed
            self._display = None
