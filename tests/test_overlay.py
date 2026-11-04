"""Smoke tests for the Tk overlay. They need tkinter and a display (e.g. xvfb-run) and skip otherwise.

Capri's real sprites are slow to prepare the first time, so these use tiny synthetic strips
with the same layout (same clip names and cell counts)."""

import os
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace

try:
    import tkinter as tk

    _probe = tk.Tk()
    _probe.destroy()
    HAVE_TK = True
except Exception:  # no tkinter, or no display
    HAVE_TK = False

from babyai.agent.context import ConversationContext
from babyai.config.loader import DEFAULT_CONFIG
from babyai.safety.rules import RiskLevel
from babyai.ui.capri_body import CLIPS

CELL_W, CELL_H = 24, 32


class FakeAssistant:
    def __init__(self, ui=None):
        self.config = {"agent": dict(DEFAULT_CONFIG["agent"]), "ui": {**DEFAULT_CONFIG["ui"], "sleep_after_s": 0, **(ui or {})}}
        self.context = ConversationContext(self.config["agent"]["system_prompt"])
        self.confirm = None
        self.heard = []

    def process_user_input(self, text):
        self.heard.append(text)
        return "ok"


def make_sprites(directory):
    for name, clip in CLIPS.items():
        strip = tk.PhotoImage(width=clip.cells * CELL_W, height=CELL_H)
        for cell in range(clip.cells):
            strip.put("#ff2d7d", to=(cell * CELL_W + 4, 4, cell * CELL_W + 20, CELL_H - 2))  # a solid block, rest see-through
        strip.write(os.path.join(directory, f"{name}.png"), format="png")


@unittest.skipUnless(HAVE_TK, "needs tkinter and a display")
class OverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cls.sprites = os.path.join(cls.dir.name, "sprites")
        os.mkdir(cls.sprites)
        helper = tk.Tk()
        make_sprites(cls.sprites)
        helper.destroy()

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def setUp(self):
        import babyai.ui.sprites as sprites

        self.sprites_module = sprites
        self._old_dir = sprites.SPRITE_DIR
        self._old_cache = os.environ.get("XDG_CACHE_HOME")
        sprites.SPRITE_DIR = type(sprites.SPRITE_DIR)(self.sprites)
        os.environ["XDG_CACHE_HOME"] = os.path.join(self.dir.name, "cache")
        self.windows = []

    def tearDown(self):
        for win in self.windows:
            try:
                win.close()
            except tk.TclError:
                pass
        self.sprites_module.SPRITE_DIR = self._old_dir
        if self._old_cache is None:
            os.environ.pop("XDG_CACHE_HOME", None)
        else:
            os.environ["XDG_CACHE_HOME"] = self._old_cache

    def window(self, **ui):
        from babyai.ui.app import BabyAIDesktopWindow

        win = BabyAIDesktopWindow(FakeAssistant(dict(sprite_scale="1", **ui)))
        self.windows.append(win)
        return win

    def pump(self, win, seconds=0.0, until=None):
        end = time.time() + seconds
        while time.time() < end:
            win.root.update()
            if until is not None and until():
                return True
            time.sleep(0.01)
        return until() if until else None

    def test_capri_is_the_default_character(self):
        win = self.window()
        self.assertIsNotNone(win._sprites)
        self.assertEqual(win._name, "Capri")
        self.assertTrue(win.assistant.context.system_prompt.startswith("You are Capri"))
        self.assertIs(win.assistant.confirm.__self__, win)  # the safety gate asks through the bubble

    def test_she_arrives_then_idles(self):
        win = self.window()
        self.pump(win, 0.1)
        self.assertEqual(win._body_drawn[0], "arrive")
        self.assertTrue(self.pump(win, 2.0, until=lambda: win._body_drawn[0] == "idle"))

    def test_stick_figure_when_asked_for(self):
        win = self.window(character="stick")
        self.assertIsNone(win._sprites)
        self.assertTrue(win.canvas.find_withtag("figure"))
        self.assertEqual(win._name, "BabyAI")
        self.assertFalse(win.assistant.context.system_prompt.startswith("You are Capri"))

    def test_missing_sprites_fall_back_to_the_stick_figure(self):
        self.sprites_module.SPRITE_DIR = type(self._old_dir)(os.path.join(self.dir.name, "nowhere"))
        win = self.window()
        self.assertIsNone(win._sprites)
        self.pump(win, 0.2)
        self.assertTrue(win.canvas.find_withtag("figure"))

    def test_the_stick_figures_face_is_not_drawn_over_her(self):
        win = self.window()
        self.pump(win, 0.3)
        self.assertFalse(win.canvas.find_withtag("face"))

    def test_reply_types_out_and_the_mouth_follows(self):
        win = self.window()
        self.pump(win, 1.2, until=lambda: win._body_drawn[0] == "idle")
        win._set_reply("Hello there, this is a fairly long reply to type out.")
        mouths = set()
        self.pump(win, 3.0, until=lambda: mouths.add(win._body_drawn[2]) or win._shown >= win._target)
        self.assertEqual(win._shown, win._target)
        self.assertTrue(any(m >= 0 for m in mouths), mouths)
        self.pump(win, 0.2)
        self.assertEqual(win._body_drawn[2], -1)  # mouth closed once she has said it all

    def test_confirmation_round_trip_by_key(self):
        win = self.window()
        verdict = {"risk_level": RiskLevel.MEDIUM}
        answers = []
        worker = threading.Thread(target=lambda: answers.append(win._confirm_from_worker("write_file", {"path": "/x"}, verdict)))
        worker.start()
        self.assertTrue(self.pump(win, 3.0, until=lambda: win._confirm is not None))
        self.assertIn("write_file", win._confirm["text"])
        self.assertIn("MEDIUM", win._confirm["text"])
        self.assertNotIn("RiskLevel", win._confirm["text"])
        self.assertTrue(win.canvas.find_withtag("confirm_yes") and win.canvas.find_withtag("confirm_no"))
        self.assertEqual(win._on_key(SimpleNamespace(keysym="n", char="n", state=0)), "break")
        worker.join(3)
        self.assertEqual(answers, [False])
        self.assertIsNone(win._confirm)

    def test_confirmation_can_be_approved(self):
        win = self.window()
        answers = []
        worker = threading.Thread(target=lambda: answers.append(win._confirm_from_worker("write_file", {}, {"risk_level": "MEDIUM"})))
        worker.start()
        self.pump(win, 3.0, until=lambda: win._confirm is not None)
        win._on_key(SimpleNamespace(keysym="y", char="y", state=0))
        worker.join(3)
        self.assertEqual(answers, [True])

    def test_an_unanswered_confirmation_times_out_as_no(self):
        win = self.window()
        answers = []
        worker = threading.Thread(target=lambda: answers.append(win._confirm_from_worker("write_file", {}, {"risk_level": "MEDIUM"})))
        worker.start()
        self.pump(win, 3.0, until=lambda: win._confirm is not None)
        win._confirm["deadline"] = time.monotonic() - 1
        worker.join(5) if self.pump(win, 1.0, until=lambda: win._confirm is None) else None
        self.assertEqual(answers, [False])

    def test_closing_with_a_question_open_answers_no(self):
        win = self.window()
        answers = []
        worker = threading.Thread(target=lambda: answers.append(win._confirm_from_worker("write_file", {}, {"risk_level": "MEDIUM"})))
        worker.start()
        self.pump(win, 3.0, until=lambda: win._confirm is not None)
        win.close()
        worker.join(3)
        self.assertEqual(answers, [False])

    def test_stroking_her_gets_a_grin_then_a_pet_line(self):
        win = self.window()
        self.pump(win, 2.0, until=lambda: win._body_drawn[0] == "idle")
        w, h = win._sprites.cell_size
        from babyai.ui.app import SPRITE_BOTTOM, WIDTH

        x, y = WIDTH // 2, SPRITE_BOTTOM - h // 2
        self.assertTrue(win._body_hit(x, y))
        self.assertFalse(win._body_hit(x - w, y))  # the see-through edge of her frame is not "her"
        for i in range(10):
            win._on_motion(SimpleNamespace(x=x + (5 if i % 2 else -5), y=y))
        self.assertEqual(win._animator.clip, "grin")
        self.pump(win, 2.0, until=lambda: win._echo == "pets you")
        self.assertIn(win._reply, ("Heh. Okay, I'll allow it.",))

    def test_pointer_motion_is_ignored_while_she_is_asleep(self):
        win = self.window()
        win._clock.sleep()
        self.pump(win, 3.0, until=lambda: win._animator.clip == "sleep")
        from babyai.ui.app import SPRITE_BOTTOM, WIDTH

        h = win._sprites.cell_size[1]
        for i in range(10):
            win._on_motion(SimpleNamespace(x=WIDTH // 2 + (5 if i % 2 else -5), y=SPRITE_BOTTOM - h // 2))
        self.assertEqual(win._animator.clip, "sleep")

    def test_sleep_and_wake_follow_the_clock(self):
        win = self.window()
        self.pump(win, 2.0, until=lambda: win._body_drawn[0] == "idle")
        win._clock.sleep()
        self.assertTrue(self.pump(win, 2.0, until=lambda: win._body_drawn[0] == "sleep"))
        win._wake()
        self.assertTrue(self.pump(win, 2.0, until=lambda: win._body_drawn[0] == "wake"))
        self.assertTrue(self.pump(win, 2.0, until=lambda: win._body_drawn[0] == "idle"))

    def test_the_slow_first_load_does_not_count_as_idle_time(self):
        real = self.sprites_module.SpriteBank.preload

        def slow_preload(bank, names):
            time.sleep(0.5)  # a cold sprite cache takes ~20 s; this is the same thing in miniature
            return real(bank, names)

        self.sprites_module.SpriteBank.preload = slow_preload
        try:
            win = self.window(sleep_after_s=0.3)  # shorter than the load
        finally:
            self.sprites_module.SpriteBank.preload = real
        win.root.update()  # first tick
        self.assertFalse(win._clock.asleep)
        self.assertEqual(win._animator.clip, "arrive")

    def test_pencil_and_mic_do_nothing_while_a_question_is_open(self):
        win = self.window()
        win._confirm = {"text": "Can I?", "event": threading.Event(), "answer": False, "deadline": time.monotonic() + 30}
        win._toggle_input()
        win._on_mic()
        self.assertFalse(win._input_open)
        win._confirm = None

    def test_on_x11_the_window_is_clipped_to_her_outline(self):
        win = self.window()
        if win._windowing != "x11":
            self.skipTest("the silhouette is X11-only")
        self.assertIsNotNone(win._shape, "the shape should have been enabled")
        self.pump(win, 0.3)
        rects = win._shape.get_rects()
        self.assertTrue(rects)
        from babyai.ui.app import BTN_Y, HEIGHT, SPRITE_BOTTOM, WIDTH

        def shown(x, y):
            return any(rx <= x < rx + rw and ry <= y < ry + rh for rx, ry, rw, rh in rects)

        self.assertFalse(shown(5, 5), "the empty corner must not be drawn")
        self.assertFalse(shown(WIDTH - 5, HEIGHT // 2))
        self.assertTrue(shown(WIDTH // 2, SPRITE_BOTTOM - CELL_H // 2), "her body must be")
        self.assertFalse(shown(WIDTH // 2 - CELL_W, SPRITE_BOTTOM - CELL_H // 2), "but not the see-through side of her frame")
        self.assertTrue(shown(WIDTH // 2, BTN_Y), "the pill must be")
        left, top, right, bottom = win._bubble_box
        self.assertTrue(shown(left + 5, top + 5) and shown(right - 5, bottom - 5), "the bubble must be")
        self.assertFalse(shown(left + 20, bottom + 8), "the strip beside the bubble's tail is not part of it")
        self.assertTrue(shown(WIDTH // 2, bottom + 3), "the tail is")

    def test_bubble_and_pill_shapes_follow_their_corners(self):
        from babyai.ui.app import _bubble_rects, _pill_rects

        def covers(rects, x, y):
            return any(rx <= x < rx + rw and ry <= y < ry + rh for rx, ry, rw, rh in rects)

        bubble = _bubble_rects(60, 100, 500, 200)
        self.assertTrue(covers(bubble, 300, 150) and covers(bubble, 499, 101) and covers(bubble, 61, 150))
        self.assertFalse(covers(bubble, 60, 100), "the cut top-left corner is not part of the bubble")
        self.assertFalse(covers(bubble, 60, 200), "nor the bottom-left one")
        self.assertTrue(covers(bubble, 71, 101), "but the edge past the cut is")
        pill = _pill_rects(200, 580, 300, 620)
        self.assertTrue(covers(pill, 250, 600) and covers(pill, 205, 600) and covers(pill, 250, 580))
        self.assertFalse(covers(pill, 200, 580), "the rounded pill corner is not part of it")
        self.assertFalse(covers(pill, 299, 619))

    def test_the_shape_follows_the_bubble_as_it_grows(self):
        win = self.window()
        if win._shape is None:
            self.skipTest("no X11 shape here")
        self.pump(win, 0.3)
        before = win._bubble_box
        win._set_reply("word " * 60)  # a long reply: a taller bubble
        self.pump(win, 0.3)
        self.assertLess(win._bubble_box[1], before[1])
        top = min(ry for rx, ry, rw, rh in win._shape.get_rects() if rw > 100)
        self.assertLessEqual(top, win._bubble_box[1])

    def test_without_a_silhouette_the_panel_comes_back(self):
        win = self.window()
        if win._shape is None:
            self.skipTest("no X11 shape here")
        win._drop_shape()
        self.assertIsNone(win._shape)
        self.pump(win, 0.2)  # keeps running

    def test_launch_chip_is_gone(self):
        win = self.window()
        self.assertFalse(win.canvas.find_withtag("btn_capri"))
        self.assertTrue(win.canvas.find_withtag("btn_pencil") and win.canvas.find_withtag("btn_voice"))


if __name__ == "__main__":
    unittest.main()
