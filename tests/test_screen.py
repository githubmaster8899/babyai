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


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "shot.png")

    def test_spectacle_takes_the_whole_screen_into_the_file(self):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            with open(self.path, "wb") as handle:
                handle.write(b"png")
            return finished()

        self.assertEqual(screen.capture_to(self.path, run=run, which=which_only("spectacle")), self.path)
        self.assertIn("-f", calls[0])
        self.assertIn(self.path, calls[0])

    def test_a_window_capture_asks_for_the_active_window(self):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            with open(self.path, "wb") as handle:
                handle.write(b"png")
            return finished()

        screen.capture_to(self.path, target="window", run=run, which=which_only("spectacle"))
        self.assertIn("-a", calls[0])
        self.assertNotIn("-f", calls[0])

    def test_grim_is_tried_first_when_it_is_installed(self):
        calls = []

        def run(command, **kwargs):
            calls.append(command[0])
            with open(self.path, "wb") as handle:
                handle.write(b"png")
            return finished()

        screen.capture_to(self.path, run=run, which=which_only("grim", "spectacle"))
        self.assertEqual(calls, ["grim"])

    def test_a_stale_file_does_not_count_as_a_screenshot(self):
        with open(self.path, "wb") as handle:
            handle.write(b"old")
        self.assertIsNone(screen.capture_to(self.path, run=lambda *a, **k: finished(), which=which_only("grim")))
        self.assertFalse(os.path.exists(self.path))

    def test_nothing_installed_means_none(self):
        self.assertIsNone(screen.capture_to(self.path, run=lambda *a, **k: finished(), which=which_only()))

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

    def test_looking_at_the_screen_always_asks_first(self):
        gate = SafetyGate(level="strict")
        verdict = gate.check("look_at_screen", {"question": "what is this?"})
        self.assertTrue(verdict["needs_confirmation"])


if __name__ == "__main__":
    unittest.main()
