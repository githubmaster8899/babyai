"""A reminder that comes due is said in Capri's bubble (needs tkinter and a display, like tests/test_overlay.py; skipped otherwise)."""

from __future__ import annotations

import gc
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from babyai import reminders
from babyai.reminders import Reminders
from tests.test_overlay import HAVE_TK, FakeAssistant, make_sprites

if HAVE_TK:
    import tkinter as tk


class RemindingAssistant(FakeAssistant):
    """What the overlay needs of the real assistant for reminders: start_reminders and stop_reminders."""

    def __init__(self, store: Reminders | None = None, ui=None):
        super().__init__(ui)
        self.store = store
        self.started: list[tuple] = []
        self.stopped = 0

    def start_reminders(self, deliver, ready=None):
        self.started.append((deliver, ready))
        if self.store is not None:
            self.store.start(deliver, ready)

    def stop_reminders(self):
        self.stopped += 1
        if self.store is not None:
            self.store.stop()


@unittest.skipUnless(HAVE_TK, "needs tkinter and a display")
class OverlayReminderTests(unittest.TestCase):
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
        patch = mock.patch("babyai.ui.app.REMINDER_GAP_S", 0.0)  # (the one test of the gap sets its own)
        patch.start()
        self.addCleanup(patch.stop)

    def tearDown(self):
        for win in self.windows:
            try:
                win.close()
            except tk.TclError:
                pass
        self.windows.clear()
        gc.collect()  # free her Tk interpreter here, on the main thread, not later inside some worker thread
        self.sprites_module.SPRITE_DIR = self._old_dir
        if self._old_cache is None:
            os.environ.pop("XDG_CACHE_HOME", None)
        else:
            os.environ["XDG_CACHE_HOME"] = self._old_cache

    def window(self, assistant=None):
        from babyai.ui.app import BabyAIDesktopWindow

        assistant = assistant or RemindingAssistant(ui={"sprite_scale": "1"})
        assistant.config["ui"]["sprite_scale"] = "1"
        win = BabyAIDesktopWindow(assistant)
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

    def test_she_asks_the_assistant_to_start_and_stop_the_reminders(self):
        assistant = RemindingAssistant()
        win = self.window(assistant)
        self.assertEqual(len(assistant.started), 1)
        deliver, ready = assistant.started[0]
        self.assertEqual(deliver, win._reminder_lines.put)
        self.assertEqual(ready, win._free_for_reminder)
        win.close()
        self.assertEqual(assistant.stopped, 1)

    def test_an_assistant_without_reminders_is_fine(self):
        win = self.window(FakeAssistant({"sprite_scale": "1"}))  # (no start_reminders, no stop_reminders)
        self.pump(win, 0.1)
        win.close()

    def test_a_reminder_that_arrives_is_said_in_the_bubble(self):
        win = self.window()
        win._reminder_lines.put("Reminder: call mum")
        self.assertTrue(self.pump(win, 3, until=lambda: win._reply == "Reminder: call mum"))
        self.assertEqual(win._echo, "set a reminder")
        self.assertFalse(win._thinking)
        self.assertTrue(win._bubble_box is not None and not win._bubble_hidden)

    def test_it_wakes_her_if_she_was_asleep(self):
        win = self.window()
        win._toggle_sleep()  # put to sleep on purpose, so not even a passing pointer wakes her
        self.assertTrue(win._clock.asleep and win._clock.manual)
        win._reminder_lines.put("Reminder: stretch")
        self.assertTrue(self.pump(win, 3, until=lambda: win._reply == "Reminder: stretch"))
        self.assertFalse(win._clock.asleep)

    def test_a_reminder_brings_her_back_when_she_was_sent_away(self):
        win = self.window()
        win._hide()  # `python3 -m babyai --toggle`: she is off the screen until summoned or until she has something to say
        self.assertEqual(win.root.state(), "withdrawn")
        win._reminder_lines.put("Reminder: tea")
        self.assertTrue(self.pump(win, 3, until=lambda: win.root.state() == "normal"))
        self.assertTrue(self.pump(win, 3, until=lambda: win._reply == "Reminder: tea"))
        self.assertFalse(win._hidden)

    def test_she_does_not_cut_in_on_a_reply_being_worked_out_and_says_it_when_done(self):
        win = self.window()
        win._busy = True
        win._set_reply("", thinking=True)
        win._reminder_lines.put("Reminder: bins")
        self.pump(win, 0.5)
        self.assertTrue(win._thinking)
        self.assertEqual(win._reply, "")
        self.assertFalse(win._free_for_reminder())
        win._results.put("here is your answer")
        self.assertTrue(self.pump(win, 3, until=lambda: win._reply == "here is your answer"))
        self.assertTrue(self.pump(win, 5, until=lambda: win._reply == "Reminder: bins"), "the reminder follows once she is free")

    def test_either_being_busy_or_thinking_alone_is_enough_to_hold_a_reminder_back(self):
        win = self.window()
        self.pump(win, 0.3)
        win._set_reply("done")
        self.finish_typing(win)
        self.assertTrue(win._free_for_reminder())
        for busy, thinking in ((True, False), (False, True)):
            win._busy, win._thinking = busy, thinking
            self.assertFalse(win._free_for_reminder(), (busy, thinking))
        win._busy = win._thinking = False
        self.assertTrue(win._free_for_reminder())

    def finish_typing(self, win):
        win._shown = win._target
        win._reveal_start = time.monotonic() - 100

    def test_an_answer_stays_up_for_a_while_before_a_reminder_takes_its_place(self):
        with mock.patch("babyai.ui.app.REMINDER_GAP_S", 1.5):
            win = self.window()
            win._busy = True
            win._results.put("here is your answer")
            self.assertTrue(self.pump(win, 3, until=lambda: win._reply == "here is your answer"))
            shown_at = time.monotonic()
            win._reminder_lines.put("Reminder: bins")
            self.pump(win, 1.0)
            self.assertEqual(win._reply, "here is your answer", "the reminder must not take the answer away at once")
            self.assertTrue(self.pump(win, 5, until=lambda: win._reply == "Reminder: bins"))
            self.assertGreaterEqual(time.monotonic() - shown_at, 1.4)

    def test_nor_while_she_is_still_typing_an_answer_out(self):
        win = self.window()
        win._set_reply("a fairly long answer that is still being typed out letter by letter")
        win._shown = 3
        self.assertFalse(win._free_for_reminder())
        win._shown = win._target
        self.assertTrue(win._free_for_reminder())

    def test_she_does_not_cut_in_on_a_question_waiting_for_a_yes_or_no(self):
        win = self.window()
        win._begin_confirm({"text": "Can I run x?", "event": threading.Event(), "answer": False, "deadline": 0.0})
        win._reminder_lines.put("Reminder: tea")
        self.pump(win, 0.5)
        self.assertEqual(win._reply, "Can I run x?")
        self.assertFalse(win._free_for_reminder())
        win._busy = True  # (the worker is waiting on the answer: the job goes on)
        win._answer_confirm(True)
        self.pump(win, 0.5)
        self.assertNotEqual(win._reply, "Reminder: tea", "she carries on with the job first")
        win._results.put("all done")
        self.assertTrue(self.pump(win, 3, until=lambda: win._reply == "all done"))
        self.assertTrue(self.pump(win, 5, until=lambda: win._reply == "Reminder: tea"))

    def test_she_does_say_it_while_the_user_is_typing_without_losing_what_they_typed(self):
        win = self.window()
        win._input_open = True
        win._draft = "half a sentence"
        win._reminder_lines.put("Reminder: lunch")
        self.assertTrue(self.pump(win, 3, until=lambda: win._reply == "Reminder: lunch"))
        self.assertEqual(win._draft, "half a sentence")
        self.assertTrue(win._input_open)

    def test_two_that_are_waiting_come_one_after_the_other(self):
        win = self.window()
        win._reminder_lines.put("Reminder: one")
        win._reminder_lines.put("Reminder: two")
        seen = []

        def watch():
            if not seen or seen[-1] != win._reply:
                seen.append(win._reply)
            return win._reply == "Reminder: two"

        self.assertTrue(self.pump(win, 3, until=watch))
        self.assertEqual([s for s in seen if s.startswith("Reminder")], ["Reminder: one", "Reminder: two"])

    def test_end_to_end_with_the_real_store(self):
        store = Reminders(Path(self.dir.name) / "r")
        self.addCleanup(store.stop)
        with mock.patch.object(reminders, "POLL_S", 0.02):
            win = self.window(RemindingAssistant(store, {"sprite_scale": "1"}))
            store.add("tea is ready", minutes=0.002)
            self.assertTrue(self.pump(win, 10, until=lambda: win._reply == "Reminder: tea is ready"))
        self.assertEqual(store.pending(), [])

    def test_a_reminder_waits_on_the_file_while_she_is_busy_so_closing_does_not_lose_it(self):
        store = Reminders(Path(self.dir.name) / "r2")
        self.addCleanup(store.stop)
        with mock.patch.object(reminders, "POLL_S", 0.02):
            win = self.window(RemindingAssistant(store, {"sprite_scale": "1"}))
            win._busy = True
            store.add("not yet", minutes=0.002)
            self.pump(win, 0.5)
            self.assertEqual(len(store.pending()), 1, "she was busy, so it is still kept")
            win.close()
        self.assertEqual(len(Reminders(Path(self.dir.name) / "r2").pending()), 1)


if __name__ == "__main__":
    unittest.main()
