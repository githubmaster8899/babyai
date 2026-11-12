"""When does typing go through the little box? (No display needed.)"""

import unittest

from babyai.config.loader import DEFAULT_CONFIG
from babyai.ui.behavior import needs_typing_box

WAYLAND = {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0"}
X11 = {"XDG_SESSION_TYPE": "x11"}


class NeedsTypingBoxTests(unittest.TestCase):
    def test_auto_uses_the_box_on_wayland_only(self):
        self.assertTrue(needs_typing_box("auto", "x11", WAYLAND))
        self.assertFalse(needs_typing_box("auto", "x11", X11))
        self.assertFalse(needs_typing_box("auto", "x11", {}))

    def test_either_wayland_variable_is_enough(self):
        self.assertTrue(needs_typing_box("auto", "x11", {"XDG_SESSION_TYPE": "Wayland"}))
        self.assertTrue(needs_typing_box("auto", "x11", {"WAYLAND_DISPLAY": "wayland-1"}))

    def test_windows_and_mac_never_need_it_on_auto(self):
        self.assertFalse(needs_typing_box("auto", "win32", WAYLAND))
        self.assertFalse(needs_typing_box("auto", "aqua", WAYLAND))

    def test_it_can_be_forced_either_way(self):
        self.assertTrue(needs_typing_box("box", "x11", X11))
        self.assertTrue(needs_typing_box(" BOX ", "win32", {}))
        self.assertFalse(needs_typing_box("bubble", "x11", WAYLAND))

    def test_nothing_set_means_auto(self):
        self.assertTrue(needs_typing_box(None, "x11", WAYLAND))
        self.assertFalse(needs_typing_box("", "x11", X11))

    def test_the_default_config_says_auto(self):
        self.assertEqual(DEFAULT_CONFIG["ui"]["typing"], "auto")


if __name__ == "__main__":
    unittest.main()


try:
    import tkinter as tk

    _probe = tk.Tk()
    _probe.destroy()
    HAVE_TK = True
except Exception:  # no tkinter, or no display
    HAVE_TK = False


@unittest.skipUnless(HAVE_TK, "needs tkinter and a display")
class TypingBoxHistoryKeys(unittest.TestCase):
    def make(self, **options):
        from babyai.ui.typing_box import TypingBox

        root = tk.Tk()
        self.addCleanup(root.destroy)
        colors = dict(bg="#000000", field="#111111", text="#ffffff", accent="#ff00ff", dim="#888888")
        box = TypingBox(root, "Talk", 40, lambda _t: None, lambda: None, lambda: None, colors, **options)
        box.window.update()
        box.entry.focus_force()
        box.window.update()
        return box

    def test_up_and_down_are_reported_when_asked_for(self):
        seen = []
        box = self.make(on_history=seen.append)
        box.entry.event_generate("<Up>", when="now")
        box.entry.event_generate("<Down>", when="now")
        box.window.update()
        self.assertEqual(seen, [-1, 1])

    def test_without_a_callback_the_keys_are_not_taken(self):
        box = self.make()
        self.assertEqual(box.entry.bind("<Up>"), "")
        self.assertEqual(box.entry.bind("<Down>"), "")

    def test_set_text_replaces_the_text_and_tells_the_overlay(self):
        changed = []
        from babyai.ui.typing_box import TypingBox

        root = tk.Tk()
        self.addCleanup(root.destroy)
        colors = dict(bg="#000000", field="#111111", text="#ffffff", accent="#ff00ff", dim="#888888")
        box = TypingBox(root, "Talk", 40, changed.append, lambda: None, lambda: None, colors)
        box.set_text("hello")
        self.assertEqual(box._var.get(), "hello")
        self.assertEqual(changed, ["hello"])
        self.assertEqual(box.entry.index("insert"), 5)
        box.set_text("x" * 100)  # the box's own limit still applies
        self.assertEqual(len(box._var.get()), 40)
