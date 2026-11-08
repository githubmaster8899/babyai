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
