import json
import unittest
from unittest import mock

from babyai.desktop import read_active_window
from babyai.safety.rules import RiskLevel, get_risk_level
from babyai.tools.desktop import GetActiveWindowTool

from types import SimpleNamespace


def finished(stdout="", returncode=0):
    return SimpleNamespace(stdout=stdout, returncode=returncode)


def which_only(*available):
    return lambda name: f"/usr/bin/{name}" if name in available else None


class ReadActiveWindowTests(unittest.TestCase):
    def test_hyprland_json(self):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            return finished(json.dumps({"title": "notes.md", "class": "code"}))

        info = read_active_window(run=run, which=which_only("hyprctl"))
        self.assertEqual(info, {"app": "code", "title": "notes.md", "backend": "hyprctl"})
        self.assertEqual(calls, [["hyprctl", "activewindow", "-j"]])

    def test_xdotool_title_and_class(self):
        def run(command, **kwargs):
            if command[-1] == "getwindowname":
                return finished("Inbox - Mail\n")
            return finished("thunderbird\n")

        info = read_active_window(run=run, which=which_only("xdotool"))
        self.assertEqual(info["app"], "thunderbird")
        self.assertEqual(info["title"], "Inbox - Mail")

    def test_sway_focused_node_is_found_deep_in_the_tree(self):
        tree = {"nodes": [{"name": "other"}, {"nodes": [{"focused": True, "name": "clip.py", "app_id": "foot"}]}]}

        def run(command, **kwargs):
            return finished(json.dumps(tree))

        info = read_active_window(run=run, which=which_only("swaymsg"))
        self.assertEqual(info, {"app": "foot", "title": "clip.py", "backend": "swaymsg"})

    def test_a_failing_tool_falls_through_to_the_next(self):
        def run(command, **kwargs):
            if command[0] == "hyprctl":
                return finished("", returncode=1)
            return finished("hello")

        info = read_active_window(run=run, which=which_only("hyprctl", "kdotool"))
        self.assertEqual(info["backend"], "kdotool")
        self.assertEqual(info["title"], "hello")

    def test_an_error_exit_is_not_believed_even_if_it_printed_something(self):
        def run(command, **kwargs):
            return finished(json.dumps({"title": "error text", "class": "usage"}), returncode=2)

        self.assertIsNone(read_active_window(run=run, which=which_only("hyprctl")))

    def test_nothing_installed_means_no_guess(self):
        self.assertIsNone(read_active_window(run=lambda *a, **k: finished(), which=which_only()))

    def test_broken_json_does_not_crash(self):
        self.assertIsNone(read_active_window(run=lambda *a, **k: finished("{oops"),
                                             which=which_only("hyprctl")))


class ActiveWindowToolTests(unittest.TestCase):
    def test_it_reports_the_window(self):
        with mock.patch("babyai.tools.desktop.desktop.read_active_window",
                        return_value={"app": "code", "title": "notes.md", "backend": "hyprctl"}):
            result = GetActiveWindowTool().invoke()
        self.assertTrue(result.ok)
        self.assertIn("code", result.content)
        self.assertIn("notes.md", result.content)

    def test_it_says_so_when_the_desktop_will_not_tell(self):
        with mock.patch("babyai.tools.desktop.desktop.read_active_window", return_value=None):
            result = GetActiveWindowTool().invoke()
        self.assertFalse(result.ok)
        self.assertIn("won't tell me", result.content)

    def test_it_asks_before_looking_in_strict_mode(self):
        self.assertEqual(get_risk_level("get_active_window"), RiskLevel.MEDIUM)


if __name__ == "__main__":
    unittest.main()
