import os
import tempfile
import unittest
from unittest import mock

from babyai import screen
from babyai.llm.client import LLMClient
from babyai.safety.gate import SafetyGate
from babyai.tools.screen import LookAtScreenTool
from babyai.tools.voice import CaptureScreenTool
from tests.fake_ollama import FakeOllama


def finished(returncode=0):
    return type("Finished", (), {"returncode": returncode, "stdout": "", "stderr": ""})()


def which_only(*available):
    return lambda name: f"/usr/bin/{name}" if name in available else None


def writes_picture(calls=None, data=b"png"):
    """A fake screenshot tool: it writes to the last word of its command line, like the real ones."""
    def run(command, **kwargs):
        if calls is not None:
            calls.append(command)
        with open(command[-1], "wb") as handle:
            handle.write(data)
        return finished()
    return run


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "shot.png")

    def leftovers(self):
        return sorted(name for name in os.listdir(self.dir.name) if name != "shot.png")

    def test_spectacle_takes_the_whole_screen_into_the_file(self):
        calls = []
        self.assertEqual(
            screen.capture_to(self.path, run=writes_picture(calls), which=which_only("spectacle")), self.path)
        self.assertIn("-f", calls[0])
        with open(self.path, "rb") as handle:
            self.assertEqual(handle.read(), b"png")

    def test_a_window_capture_asks_for_the_active_window(self):
        calls = []
        screen.capture_to(self.path, target="window", run=writes_picture(calls), which=which_only("spectacle"))
        self.assertIn("-a", calls[0])
        self.assertNotIn("-f", calls[0])

    def test_grim_is_tried_first_when_it_is_installed(self):
        calls = []
        screen.capture_to(self.path, run=writes_picture(calls), which=which_only("grim", "spectacle"))
        self.assertEqual([call[0] for call in calls], ["grim"])

    def test_the_tool_writes_beside_the_target_and_not_onto_it(self):
        calls = []
        screen.capture_to(self.path, run=writes_picture(calls), which=which_only("grim"))
        written = calls[0][-1]
        self.assertNotEqual(written, self.path)
        self.assertEqual(os.path.dirname(os.path.dirname(written)), self.dir.name)
        self.assertTrue(written.endswith(".png"))

    def test_the_picture_replaces_an_older_file_at_the_target(self):
        with open(self.path, "wb") as handle:
            handle.write(b"old")
        self.assertEqual(screen.capture_to(self.path, run=writes_picture(data=b"new"), which=which_only("grim")), self.path)
        with open(self.path, "rb") as handle:
            self.assertEqual(handle.read(), b"new")

    def test_a_failed_capture_leaves_the_users_file_alone(self):
        with open(self.path, "wb") as handle:
            handle.write(b"old")
        self.assertIsNone(screen.capture_to(self.path, run=lambda *a, **k: finished(), which=which_only("grim")))
        with open(self.path, "rb") as handle:
            self.assertEqual(handle.read(), b"old")
        self.assertEqual(self.leftovers(), [])

    def test_a_tool_that_fails_after_writing_does_not_count(self):
        def run(command, **kwargs):
            with open(command[-1], "wb") as handle:
                handle.write(b"half a picture")
            return finished(returncode=1)

        with open(self.path, "wb") as handle:
            handle.write(b"old")
        self.assertIsNone(screen.capture_to(self.path, run=run, which=which_only("grim")))
        with open(self.path, "rb") as handle:
            self.assertEqual(handle.read(), b"old")
        self.assertEqual(self.leftovers(), [])

    def test_an_empty_file_does_not_count_as_a_screenshot(self):
        self.assertIsNone(screen.capture_to(self.path, run=writes_picture(data=b""), which=which_only("grim")))
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(self.leftovers(), [])

    def test_a_leftover_from_one_tool_does_not_pass_for_the_next(self):
        def run(command, **kwargs):
            if command[0] == "grim":  # wrote part of a picture, then failed
                with open(command[-1], "wb") as handle:
                    handle.write(b"half a picture")
                return finished(returncode=1)
            return finished()  # spectacle claims success but wrote nothing

        self.assertIsNone(screen.capture_to(self.path, run=run, which=which_only("grim", "spectacle")))
        self.assertFalse(os.path.exists(self.path))

    def test_a_crashing_tool_is_skipped_for_the_next_one(self):
        def run(command, **kwargs):
            if command[0] == "grim":
                raise OSError("no display")
            return writes_picture()(command, **kwargs)

        self.assertEqual(screen.capture_to(self.path, run=run, which=which_only("grim", "spectacle")), self.path)
        self.assertEqual(self.leftovers(), [])

    def test_a_directory_in_the_way_is_not_deleted(self):
        os.mkdir(self.path)
        self.assertIsNone(screen.capture_to(self.path, run=writes_picture(), which=which_only("grim")))
        self.assertTrue(os.path.isdir(self.path))
        self.assertEqual(self.leftovers(), [])

    def test_the_folder_is_made_when_it_is_missing(self):
        deep = os.path.join(self.dir.name, "a", "b", "shot.png")
        self.assertEqual(screen.capture_to(deep, run=writes_picture(), which=which_only("grim")), deep)

    def test_nothing_installed_means_none(self):
        self.assertIsNone(screen.capture_to(self.path, run=lambda *a, **k: finished(), which=which_only()))
        self.assertEqual(self.leftovers(), [])

    def test_the_capture_tool_saves_where_it_says(self):
        with mock.patch("babyai.screen.capture_to", return_value=self.path) as capture:
            result = CaptureScreenTool().invoke(self.path)
        self.assertTrue(result.ok)
        capture.assert_called_once_with(self.path)
        self.assertIn(self.path, result.content)


class VisionClientTests(unittest.TestCase):
    def test_the_image_goes_as_a_data_uri_and_the_answer_comes_back(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            handle.write(b"fake-png")
            path = handle.name
        self.addCleanup(os.remove, path)
        with FakeOllama(models=["llava"]) as server:
            reply = LLMClient(server.url, "llava").complete_vision("what is this?", path)
            request = server.requests[-1]
        self.assertEqual(reply["choices"][0]["message"]["content"], "pong")
        content = request["messages"][0]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "what is this?"})
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))


    def test_a_model_can_be_named_per_call(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            handle.write(b"fake-png")
            path = handle.name
        self.addCleanup(os.remove, path)
        with FakeOllama(models=["llava", "qwen2.5vl"]) as server:
            LLMClient(server.url, "llava").complete_vision("hi", path)
            LLMClient(server.url, "llava").complete_vision("hi", path, model="qwen2.5vl")
            first, second = server.requests[-2:]
        self.assertEqual((first["model"], second["model"]), ("llava", "qwen2.5vl"))


class LookAtScreenToolTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def test_without_a_vision_model_it_says_what_to_set(self):
        result = LookAtScreenTool(config={"llm": {"model": "llama3.2:3b"}}).invoke()
        self.assertFalse(result.ok)
        self.assertIn("vision_model", result.content)

    def test_a_capture_failure_is_explained(self):
        with mock.patch("babyai.screen.capture_to", return_value=None):
            result = LookAtScreenTool(config={"llm": {"vision_model": "llava"}}).invoke()
        self.assertFalse(result.ok)
        self.assertIn("couldn't take a screenshot", result.content)

    def test_the_question_reaches_the_model_and_the_picture_is_deleted(self):
        class Client:
            def __init__(self):
                self.seen = None

            def complete_vision(self, prompt, path, model=None):
                self.seen = (prompt, path, model, os.path.exists(path))
                return {"choices": [{"message": {"content": "A code editor with a test failure."}}]}

        client = Client()

        def capture(path):
            with open(path, "wb") as handle:
                handle.write(b"png")
            return path

        with mock.patch("babyai.screen.capture_to", side_effect=capture):
            result = LookAtScreenTool(config={"llm": {"vision_model": "llava"}}, client=client).invoke("what broke?")
        self.assertTrue(result.ok)
        self.assertIn("test failure", result.content)
        prompt, path, model, existed = client.seen
        self.assertEqual((prompt, model, existed), ("what broke?", "llava", True))
        self.assertFalse(os.path.exists(path))

    def test_a_model_error_does_not_leave_him_stuck(self):
        class Client:
            def complete_vision(self, *args, **kwargs):
                raise RuntimeError("server exploded")

        def capture(path):
            with open(path, "wb") as handle:
                handle.write(b"png")
            return path

        with mock.patch("babyai.screen.capture_to", side_effect=capture):
            result = LookAtScreenTool(config={"llm": {"vision_model": "llava"}}, client=Client()).invoke()
        self.assertFalse(result.ok)
        self.assertIn("server exploded", result.content)

    def where(self, **llm):
        return LookAtScreenTool(config={"llm": llm}).preview("what is this?")["the picture goes to"]

    def test_the_confirmation_says_the_picture_stays_on_this_computer(self):
        shown = LookAtScreenTool(config={"llm": {"vision_model": "llava"}}).preview("what is this?")
        self.assertIn("what is this?", shown["screen"])
        self.assertIn("llava on this computer", shown["the picture goes to"])
        for base_url in ("http://127.0.0.1:11434/v1", "http://localhost:8080/v1", "http://[::1]:11434/v1"):
            self.assertIn("on this computer", self.where(vision_model="llava", base_url=base_url), base_url)

    def test_the_confirmation_warns_when_the_model_is_somewhere_else(self):
        for base_url, host in (("https://models.example.com/v1", "models.example.com"),
                               ("http://192.168.1.20:11434/v1", "192.168.1.20")):
            said = self.where(vision_model="llava", base_url=base_url)
            self.assertIn(host, said)
            self.assertIn("NOT this computer", said)

    def test_the_confirmation_says_when_nothing_will_be_taken(self):
        self.assertIn("nothing will be taken", self.where())

    def test_both_screen_tools_are_the_most_guarded_kind(self):
        from babyai.safety.rules import TOOL_RISK_LEVELS, RiskLevel, get_risk_level

        for name in ("look_at_screen", "capture_screen"):
            self.assertEqual(TOOL_RISK_LEVELS[name], RiskLevel.CRITICAL, name)
            self.assertEqual(get_risk_level(name, {}), RiskLevel.CRITICAL, name)

    def test_looking_at_the_screen_always_asks_first(self):
        gate = SafetyGate(level="strict")
        verdict = gate.check("look_at_screen", {"question": "what is this?"})
        self.assertTrue(verdict["needs_confirmation"])


if __name__ == "__main__":
    unittest.main()
