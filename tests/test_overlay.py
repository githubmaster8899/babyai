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


class Recorder:
    """Stands in for the sound player: remembers what she tried to play."""

    def __init__(self):
        self.played = []

    def play(self, name):
        self.played.append(name)
        return True


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
            shift = cell % 3  # the block wobbles from frame to frame, like her hair does
            strip.put("#ff2d7d", to=(cell * CELL_W + 4 + shift, 4, cell * CELL_W + 20 + shift, CELL_H - 2))  # solid, rest see-through
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
        self._old_runtime = os.environ.get("XDG_RUNTIME_DIR")
        sprites.SPRITE_DIR = type(sprites.SPRITE_DIR)(self.sprites)
        os.environ["XDG_CACHE_HOME"] = os.path.join(self.dir.name, "cache")
        self._runtime = tempfile.TemporaryDirectory(dir="/tmp")  # the summon socket goes here, never in the real runtime folder
        os.environ["XDG_RUNTIME_DIR"] = self._runtime.name
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
        if self._old_runtime is None:
            os.environ.pop("XDG_RUNTIME_DIR", None)
        else:
            os.environ["XDG_RUNTIME_DIR"] = self._old_runtime
        self._runtime.cleanup()

    def window(self, **ui):
        from babyai.ui.app import NEON, BabyAIDesktopWindow

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

    def test_sounds_are_off_unless_asked_for(self):
        self.assertFalse(self.window()._sounds.enabled)
        win = self.window(sounds=True, sound_volume=0.2)
        self.assertTrue(win._sounds.enabled)
        self.assertEqual(win._sounds.volume, 0.2)

    def test_she_chimes_when_she_wakes_and_not_when_she_was_already_up(self):
        win = self.window()
        win._sounds = Recorder()
        win._wake()
        self.assertEqual(win._sounds.played, [])
        win._clock.sleep()
        win._wake()
        win._wake()
        self.assertEqual(win._sounds.played, ["wake"])

    def test_a_reply_has_its_own_sound_and_does_not_add_the_wake_chime(self):
        win = self.window()
        win._sounds = Recorder()
        win._clock.sleep()
        win._results.put("hello")
        self.assertTrue(self.pump(win, 3.0, until=lambda: win._sounds.played))
        self.assertEqual(win._sounds.played, ["reply"])
        self.assertFalse(win._clock.asleep)

    def test_the_pointer_coming_close_wakes_her_with_a_chime_but_nothing_else_does(self):
        win = self.window()
        win._sounds = Recorder()
        win._clock.sleep()

        def pointer_woke_her(*_args):
            win._clock.asleep = False
            return False  # (tick says whether she is asleep afterwards)

        real_tick = win._clock.tick
        win._clock.tick = lambda *a: True  # still asleep afterwards
        win._presence_tick()
        win._clock.tick = real_tick
        self.assertEqual(win._sounds.played, [], "a pointer that did not wake her is not a chime")
        win._clock.tick = pointer_woke_her
        win._presence_tick()
        self.assertEqual(win._sounds.played, ["wake"])
        win._presence_tick()  # awake before and after
        self.assertEqual(win._sounds.played, ["wake"])

    # ---- summon / hide, Esc, history

    def test_hide_show_and_toggle_from_outside(self):
        from babyai.ui.summon import send, socket_path

        win = self.window()
        self.pump(win, 0.2)
        self.assertEqual(win.root.state(), "normal")
        self.assertEqual(send("hide", socket_path()), "ok")
        self.assertTrue(self.pump(win, 3.0, until=lambda: win.root.state() == "withdrawn"))
        self.assertEqual(send("toggle", socket_path()), "ok")
        self.assertTrue(self.pump(win, 3.0, until=lambda: win.root.state() == "normal"))
        self.assertEqual(send("toggle", socket_path()), "ok")
        self.assertTrue(self.pump(win, 3.0, until=lambda: win.root.state() == "withdrawn"))
        self.assertEqual(send("show", socket_path()), "ok")
        self.assertTrue(self.pump(win, 3.0, until=lambda: win.root.state() == "normal"))
        self.assertEqual(send("show", socket_path()), "ok")  # already up: no harm
        self.pump(win, 0.3)
        self.assertEqual(win.root.state(), "normal")

    def test_a_reply_while_she_is_hidden_brings_her_back(self):
        win = self.window()
        win._hide()
        self.assertEqual(win.root.state(), "withdrawn")
        win._results.put("you have mail")
        self.assertTrue(self.pump(win, 3.0, until=lambda: win.root.state() == "normal"))
        self.assertIn("you have mail", win._reply)

    def test_a_question_while_she_is_hidden_brings_her_back_and_she_cannot_be_hidden_while_asking(self):
        win = self.window()
        win._sounds = Recorder()
        win._clock.sleep()
        win._hide()
        answers = []
        worker = threading.Thread(target=lambda: answers.append(win._confirm_from_worker("write_file", {}, {"risk_level": "MEDIUM"})))
        worker.start()
        self.assertTrue(self.pump(win, 3.0, until=lambda: win._confirm is not None))
        self.assertEqual(win._sounds.played, ["ask"])
        self.assertEqual(win.root.state(), "normal")
        win._hide()  # the shortcut pressed while she waits for a yes or no
        self.assertEqual(win.root.state(), "normal")
        win._answer_confirm(True)
        worker.join(3)
        self.assertEqual(answers, [True])

    def test_being_petted_chirps(self):
        win = self.window()
        win._sounds = Recorder()
        self.pump(win, 2.0, until=lambda: win._body_drawn[0] == "idle")
        h = win._sprites.cell_size[1]
        from babyai.ui.app import SPRITE_BOTTOM, WIDTH

        x, y = WIDTH // 2, SPRITE_BOTTOM - h // 2
        for i in range(10):
            win._on_motion(SimpleNamespace(x=x + (5 if i % 2 else -5), y=y))
        self.assertEqual(win._sounds.played, ["pet"])

    def test_hiding_her_closes_the_box_she_was_being_typed_to_in(self):
        win = self.window(typing="box")
        win._open_input()
        self.assertIsNotNone(win._typing_box)
        win._hide()
        self.assertIsNone(win._typing_box)
        self.assertFalse(win._input_open)

    def test_the_socket_goes_away_when_she_is_closed_and_summon_can_be_turned_off(self):
        from babyai.ui.summon import send, socket_path

        win = self.window()
        self.assertEqual(send("show", socket_path()), "ok")
        win.close()
        self.assertIsNone(send("show", socket_path()))
        off = self.window(summon=False)
        self.assertIsNone(off._summon_server)
        self.assertIsNone(send("show", socket_path()))

    def test_a_second_overlay_leaves_the_summon_to_the_first(self):
        from babyai.ui.summon import send, socket_path

        first = self.window()
        second = self.window()
        self.assertIsNotNone(first._summon_server)
        self.assertIsNone(second._summon_server)
        self.assertEqual(send("hide", socket_path()), "ok")
        self.assertTrue(self.pump(first, 3.0, until=lambda: first.root.state() == "withdrawn"))
        self.assertEqual(second.root.state(), "normal")

    def test_escape_puts_the_reply_away_and_ctrl_period_too(self):
        for event in (SimpleNamespace(keysym="Escape", char="", state=0), SimpleNamespace(keysym="period", char=".", state=0x4)):
            win = self.window()
            win._set_reply("a fairly long reply that is being said")
            self.pump(win, 0.3)
            self.assertIsNotNone(win._bubble_box)
            self.assertEqual(win._on_key(event), "break")
            self.assertIsNone(win._bubble_box)
            self.assertTrue(win._bubble_hidden)

    def test_a_plain_period_or_escape_with_nothing_to_dismiss_is_left_alone(self):
        win = self.window()
        win._hide_bubble()
        self.assertIsNone(win._on_key(SimpleNamespace(keysym="Escape", char="", state=0)))
        win._set_reply("hello")
        self.assertIsNone(win._on_key(SimpleNamespace(keysym="period", char=".", state=0)))
        self.assertIsNotNone(win._bubble_box)

    def test_escape_does_not_pretend_to_stop_her_thinking(self):
        win = self.window()
        win._busy = True
        win._set_reply("", thinking=True)
        self.assertIsNone(win._on_key(SimpleNamespace(keysym="Escape", char="", state=0)))
        self.assertTrue(win._thinking)

    def test_up_and_down_in_her_bubble_go_through_what_was_said(self):
        win = self.window()
        for text in ("first thing", "second thing"):
            win._history.add(text)
        win._input_open = True
        win._draft = "half"
        press = lambda key: win._on_key(SimpleNamespace(keysym=key, char="", state=0))
        self.assertEqual(press("Up"), "break")
        self.assertEqual(win._draft, "second thing")
        press("Up")
        self.assertEqual(win._draft, "first thing")
        press("Down")
        press("Down")
        self.assertEqual(win._draft, "half", "back to what was being typed")
        press("Up")
        win._on_key(SimpleNamespace(keysym="x", char="x", state=0))  # editing it makes it a new draft
        self.assertEqual(win._draft, "second thingx")
        press("Up")
        self.assertEqual(win._draft, "second thing")

    def test_what_was_sent_is_remembered(self):
        win = self.window()
        win._submit("remember me")
        self.assertTrue(self.pump(win, 3.0, until=lambda: not win._busy))
        win._open_input()
        win._on_key(SimpleNamespace(keysym="Up", char="", state=0))
        self.assertEqual(win._draft, "remember me")

    def test_up_and_down_in_the_typing_box(self):
        win = self.window(typing="box")
        win._history.add("old one")
        win._open_input()
        box = win._typing_box
        self.assertIsNotNone(box)
        win._recall(-1)
        self.assertEqual(box._var.get(), "old one")
        self.assertEqual(win._draft, "old one")
        box._var.set("old one, changed")
        self.assertEqual(win._draft, "old one, changed")
        win._recall(1)  # nothing newer: the box keeps what is in it
        self.assertEqual(box._var.get(), "old one, changed")

    def test_the_box_keys_reach_her_and_editing_a_recalled_line_starts_again_from_the_newest(self):
        win = self.window(typing="box")
        win._history.add("a")
        win._history.add("b")
        win._open_input()
        box = win._typing_box
        box.entry.focus_force()
        box.window.update()
        box.entry.event_generate("<Up>", when="now")
        box.window.update()
        self.assertEqual(box._var.get(), "b")
        box._var.set("b!")  # typed over the recalled line: a new draft
        box.entry.event_generate("<Up>", when="now")
        box.window.update()
        self.assertEqual(box._var.get(), "b")
        box.entry.event_generate("<Up>", when="now")
        box.window.update()
        self.assertEqual(box._var.get(), "a")
        box.entry.event_generate("<Down>", when="now")
        box.entry.event_generate("<Down>", when="now")
        box.window.update()
        self.assertEqual(box._var.get(), "b!")

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

    def test_she_hides_after_a_quiet_spell_and_comes_back_when_found(self):
        # The wait is long next to one slide so a slow machine can't see her hide again before she is back out.
        win = self.window(hide_and_seek=True, hide_after_s=3.0, peek_every_s=100, sleep_after_s=0)
        self.finish_typing(win)
        self.assertTrue(self.pump(win, 10.0, until=lambda: win._game.mode == "hidden"))
        self.assertTrue(self.pump(win, 10.0, until=lambda: win._hide_offset > 100))
        self.assertEqual(win._applied_position[0], win._home_x + round(win._hide_offset))
        win._wake()  # a click, key or reply: she comes back out
        self.assertEqual(win._game.mode, "out")
        self.assertTrue(self.pump(win, 10.0, until=lambda: win._hide_offset <= 1))
        self.assertEqual(win._applied_position[0], win._home_x)

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

    # ---- long replies follow the typing and can be scrolled; the bubble hides itself after she finishes talking

    LONG = " ".join(f"w{n:03d}" for n in range(120))  # a reply much longer than the bubble

    def finish_typing(self, win):
        win._shown = win._target
        win._render_bubble()

    def inside_bubble(self, win, dy=20):
        left, top, right, _bottom = win._bubble_box
        return SimpleNamespace(x=(left + right) // 2, y=top + dy, delta=120)

    def test_a_long_reply_is_shown_in_full_not_cut_off_with_an_ellipsis(self):
        from babyai.ui.app import MAX_LINES

        win = self.window()
        win._set_reply(self.LONG)
        self.finish_typing(win)
        text = self.bubble_text(win)
        self.assertIn("w119", text)
        self.assertNotIn("…", text)
        self.assertNotIn("w000", text)  # the start has scrolled out of view ...
        top, total, visible = win._view
        self.assertEqual(visible, MAX_LINES)
        self.assertEqual(top, total - visible)  # ... and she is showing the end of it

    def test_the_view_follows_the_line_being_typed(self):
        from babyai.ui.app import NEON

        win = self.window()
        win._set_reply(self.LONG)
        for fraction in (0.1, 0.3, 0.55, 0.8, 0.97):
            win._shown = int(win._target * fraction)
            win._render_bubble()
            _left, top, _right, bottom = win._bubble_box
            cursor = [i for i in win.canvas.find_withtag("bubble") if win.canvas.type(i) == "rectangle" and win.canvas.itemcget(i, "fill") == NEON]
            self.assertEqual(len(cursor), 1, fraction)
            y1, y2 = win.canvas.bbox(cursor[0])[1], win.canvas.bbox(cursor[0])[3]
            self.assertTrue(top <= y1 and y2 <= bottom, f"the typing cursor left the bubble at {fraction}")

    def test_the_wheel_scrolls_a_long_reply_and_scrolling_down_follows_again(self):
        win = self.window()
        win._set_reply(self.LONG)
        self.finish_typing(win)
        end = win._view[0]
        win._on_wheel(self.inside_bubble(win))  # delta > 0: towards the start
        self.assertEqual(win._view[0], end - 1)
        self.assertEqual(win._scroll, end - 1)
        for _ in range(end + 5):
            win._on_wheel(self.inside_bubble(win))
        self.assertEqual(win._view[0], 0)  # stops at the start
        self.assertIn("w000", self.bubble_text(win))
        self.assertIn("CAPRI:", self.bubble_text(win))
        for _ in range(end + 5):
            win._on_wheel(SimpleNamespace(**{**vars(self.inside_bubble(win)), "delta": -120}))
        self.assertEqual(win._view[0], end)  # stops at the end ...
        self.assertIsNone(win._scroll)  # ... where it follows the typing again

    def test_scrolled_up_there_are_arrows_for_what_is_off_screen(self):
        win = self.window()
        win._set_reply(self.LONG)
        self.finish_typing(win)
        count = lambda: len([i for i in win.canvas.find_withtag("bubble") if win.canvas.type(i) == "polygon"])
        base = count()  # the bubble plate, plus the arrow towards the start
        win._on_wheel(self.inside_bubble(win))
        self.assertEqual(count(), base + 1)  # now text below as well
        for _ in range(50):
            win._on_wheel(self.inside_bubble(win))
        self.assertEqual(count(), base)  # at the start: only the one towards the end

    def test_the_wheel_away_from_the_bubble_does_nothing(self):
        win = self.window()
        win._set_reply(self.LONG)
        self.finish_typing(win)
        before = win._view
        win._on_wheel(SimpleNamespace(x=2, y=2, delta=120))
        self.assertEqual(win._view, before)
        self.assertIsNone(win._scroll)

    def test_a_new_reply_follows_the_typing_again(self):
        win = self.window()
        win._set_reply(self.LONG)
        self.finish_typing(win)
        win._on_wheel(self.inside_bubble(win))
        self.assertIsNotNone(win._scroll)
        win._set_reply(self.LONG)
        self.assertIsNone(win._scroll)

    def test_a_short_reply_does_not_scroll(self):
        win = self.window()
        win._set_reply("Short and sweet.")
        self.finish_typing(win)
        top, total, visible = win._view
        self.assertEqual((top, total), (0, 1))
        win._on_wheel(self.inside_bubble(win))
        self.assertEqual(win._view[0], 0)

    def talk_and_wait_for_hide(self, win, seconds=3.0):
        win._set_reply("Hello there.")
        self.pump(win, 3.0, until=lambda: win._shown >= win._target)
        return self.pump(win, seconds, until=lambda: win._bubble_hidden)

    def test_the_bubble_hides_itself_after_she_finishes_talking(self):
        win = self.window(bubble_hide_s=0.5)
        self.pump(win, 0.3)
        started = time.monotonic()
        self.assertTrue(self.talk_and_wait_for_hide(win))
        self.assertGreaterEqual(time.monotonic() - started, 0.5)
        self.assertEqual(win.canvas.find_withtag("bubble"), ())
        self.assertIsNone(win._bubble_box)

    def test_the_window_shape_gives_up_the_bubble_when_it_hides(self):
        win = self.window(bubble_hide_s=0.5)
        if win._shape is None:
            self.skipTest("no X11 shape here")
        self.pump(win, 0.3)
        win._set_reply("Hello there.")
        self.pump(win, 0.3)
        left, top, right, bottom = win._bubble_box
        middle = ((left + right) // 2, (top + bottom) // 2)
        covers = lambda: any(x <= middle[0] < x + w and y <= middle[1] < y + h for x, y, w, h in win._shape.get_rects())
        self.assertTrue(covers())
        self.assertTrue(self.pump(win, 4.0, until=lambda: win._bubble_hidden))
        self.pump(win, 0.2)
        self.assertFalse(covers(), "the hidden bubble is still cut into the window")

    def test_a_new_reply_brings_the_bubble_back(self):
        win = self.window(bubble_hide_s=0.5)
        self.assertTrue(self.talk_and_wait_for_hide(win))
        win._set_reply("Back again.")
        self.assertFalse(win._bubble_hidden)
        self.assertTrue(win.canvas.find_withtag("bubble"))
        self.assertIsNotNone(win._bubble_box)

    def test_it_stays_while_she_is_thinking_or_being_typed_to(self):
        win = self.window(bubble_hide_s=0.4)
        hides = []
        real = win._hide_bubble
        win._hide_bubble = lambda: (hides.append(1), real())[1]
        self.pump(win, 0.3)
        win._busy = True
        win._set_reply("", thinking=True)
        self.pump(win, 1.5)
        self.assertEqual(hides, [], "hid while she was still thinking")
        win._busy = False
        win._set_reply("Done.")
        self.pump(win, 3.0, until=lambda: win._shown >= win._target)
        hides.clear()  # (the bubble may rightly have hidden after "Done.")
        win._toggle_input()  # you are typing
        self.pump(win, 1.5)
        self.assertEqual(hides, [], "hid while you were typing")

    def test_resting_the_pointer_on_the_bubble_keeps_it_up(self):
        win = self.window(bubble_hide_s=0.6)
        self.pump(win, 0.3)
        win._set_reply("Hello there.")
        self.pump(win, 3.0, until=lambda: win._shown >= win._target)
        left, top, right, bottom = win._bubble_box
        spot = (win.root.winfo_rootx() + (left + right) // 2, win.root.winfo_rooty() + (top + bottom) // 2)
        win.root.winfo_pointerxy = lambda: spot
        self.pump(win, 2.0)
        self.assertFalse(win._bubble_hidden, "hid with the pointer on it")
        win.root.winfo_pointerxy = lambda: (1, 1)  # pointer moves away
        self.assertTrue(self.pump(win, 4.0, until=lambda: win._bubble_hidden))

    def test_scrolling_counts_as_looking_at_it(self):
        win = self.window(bubble_hide_s=1.0)
        win._set_reply(self.LONG)
        self.finish_typing(win)
        start = time.monotonic()
        while time.monotonic() - start < 1.6:  # a wheel notch every 0.3 s keeps it up past the 1 s delay
            win._on_wheel(self.inside_bubble(win))
            self.pump(win, 0.3)
        self.assertFalse(win._bubble_hidden)

    def test_zero_never_hides_the_bubble(self):
        win = self.window(bubble_hide_s=0)
        win._set_reply("Hello there.")
        self.pump(win, 3.0, until=lambda: win._shown >= win._target)
        self.pump(win, 1.5)
        self.assertFalse(win._bubble_hidden)

    # ---- typing on a desktop where the borderless window can't take keys (Wayland): the typing box

    def open_box(self, **ui):
        win = self.window(typing="box", **ui)
        self.pump(win, 0.3)
        win._toggle_input()
        self.pump(win, 0.2)
        self.assertIsNotNone(win._typing_box)
        return win, win._typing_box

    def bubble_text(self, win):
        return " ".join(str(win.canvas.itemcget(i, "text")) for i in win.canvas.find_withtag("bubble") if win.canvas.type(i) == "text")

    def test_the_box_takes_the_keys_and_her_bubble_mirrors_them(self):
        win, box = self.open_box()
        self.assertTrue(win._input_open)
        box.entry.insert("end", "hello there")
        self.assertEqual(win._draft, "hello there")
        self.assertIn("hello there", self.bubble_text(win))

    def test_return_in_the_box_sends_what_was_typed_and_closes_it(self):
        win, box = self.open_box()
        sent = []
        win._submit = sent.append
        box.entry.insert("end", "what's the weather")
        box.entry.focus_force()
        box.entry.event_generate("<Return>")
        self.pump(win, 0.2)
        self.assertEqual(sent, ["what's the weather"])
        self.assertIsNone(win._typing_box)
        self.assertFalse(win._input_open)

    def test_return_on_an_empty_box_keeps_it_open(self):
        win, box = self.open_box()
        win._submit = lambda text: self.fail("nothing to send")
        box.entry.focus_force()
        box.entry.event_generate("<Return>")
        self.pump(win, 0.2)
        self.assertIs(win._typing_box, box)

    def test_escape_in_the_box_closes_only_the_box(self):
        win, box = self.open_box()
        box.entry.insert("end", "never mind")
        box.entry.focus_force()
        box.entry.event_generate("<Escape>")
        self.pump(win, 0.2)
        self.assertIsNone(win._typing_box)
        self.assertFalse(win._input_open)
        self.assertEqual(win._draft, "")
        self.assertTrue(win.root.winfo_exists())  # Esc on the overlay quits; in the box it must not

    def test_closing_the_box_window_cancels_the_input(self):
        win, box = self.open_box()
        box.window.tk.call(box.window.protocol("WM_DELETE_WINDOW"))  # what the window manager's close button runs
        self.pump(win, 0.2)
        self.assertIsNone(win._typing_box)
        self.assertFalse(win._input_open)

    def test_the_box_cannot_hold_more_than_a_message_may_be(self):
        from babyai.ui.app import MAX_DRAFT

        win, box = self.open_box()
        box.entry.insert("end", "x" * (MAX_DRAFT + 50))
        self.assertEqual(len(win._draft), MAX_DRAFT)

    def test_a_question_from_the_safety_gate_takes_the_box_away(self):
        win, box = self.open_box()
        box.entry.insert("end", "half a thought")
        answers = []
        worker = threading.Thread(target=lambda: answers.append(win._confirm_from_worker("write_file", {}, {"risk_level": "MEDIUM"})))
        worker.start()
        self.assertTrue(self.pump(win, 3.0, until=lambda: win._confirm is not None))
        self.assertIsNone(win._typing_box)  # her question comes first; the buttons still answer it
        self.assertFalse(win._input_open)
        win._answer_confirm(False)
        worker.join(3)
        self.assertEqual(answers, [False])

    def test_quitting_takes_the_box_with_it(self):
        win, box = self.open_box()
        win.close()
        self.assertIsNone(win._typing_box)
        with self.assertRaises(tk.TclError):
            box.window.winfo_exists()

    def test_the_box_sits_on_the_screen_above_her_bubble(self):
        win, box = self.open_box()
        win.root.update_idletasks()
        window = box.window
        self.assertGreaterEqual(window.winfo_rooty(), 0)
        self.assertLessEqual(window.winfo_rooty() + window.winfo_reqheight(), window.winfo_screenheight())
        _left, top, _right, _bottom = win._bubble_box
        self.assertLessEqual(window.winfo_rooty() + window.winfo_reqheight(), win.root.winfo_rooty() + top + 1)

    def test_bubble_mode_keeps_typing_in_her_bubble(self):
        win = self.window(typing="bubble")
        self.pump(win, 0.3)
        win._toggle_input()
        self.assertTrue(win._input_open)
        self.assertIsNone(win._typing_box)
        win._on_key(SimpleNamespace(keysym="h", char="h", state=0))
        self.assertEqual(win._draft, "h")

    def test_if_the_box_cannot_be_made_she_falls_back_to_her_bubble(self):
        import babyai.ui.app as app_module

        win = self.window(typing="box")
        self.pump(win, 0.3)

        def broken(*args, **kwargs):
            raise tk.TclError("no second window")

        real, app_module.TypingBox = app_module.TypingBox, broken
        try:
            win._toggle_input()
        finally:
            app_module.TypingBox = real
        self.assertTrue(win._input_open)
        self.assertIsNone(win._typing_box)
        self.assertFalse(win._typing_via_box)
        win._on_key(SimpleNamespace(keysym="a", char="a", state=0))
        self.assertEqual(win._draft, "a")

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

    def count_shape_rewrites(self, win):
        calls = []
        real = win._shape.set_rects
        win._shape.set_rects = lambda rects: (calls.append(len(rects)), real(rects))[1]
        return calls

    def test_animating_does_not_re_cut_the_window(self):
        """Re-cutting the shape on every frame (or every breathing step) made her flicker on a real desktop."""
        win = self.window()
        if win._shape is None:
            self.skipTest("no X11 shape here")
        self.pump(win, 2.0, until=lambda: win._body_drawn[0] == "idle")
        self.pump(win, 0.3)
        calls = self.count_shape_rewrites(win)
        frames = set()
        self.pump(win, 3.0, until=lambda: frames.add(win._body_drawn[:2]) and False)  # idle frames pass by, and she breathes
        self.assertGreater(len(frames), 8, "the animation should have been running")
        self.assertEqual(calls, [], "the shape must not change while she idles")

    def test_the_shape_changes_when_she_changes_pose_family(self):
        win = self.window()
        if win._shape is None:
            self.skipTest("no X11 shape here")
        self.pump(win, 2.0, until=lambda: win._body_drawn[0] == "idle")
        self.pump(win, 0.3)
        calls = self.count_shape_rewrites(win)
        win._animator.play("grin", time.monotonic())  # same family as idle: no change
        self.pump(win, 0.5)
        self.assertEqual(calls, [])
        win._animator.play("glitch", time.monotonic())  # a different family: the shape is re-cut once
        self.pump(win, 0.3)
        self.assertEqual(len(calls), 1)

    def test_every_pose_and_breathing_step_stays_inside_the_shape(self):
        win = self.window()
        if win._shape is None:
            self.skipTest("no X11 shape here")
        from babyai.ui.app import SPRITE_BOTTOM, WIDTH

        self.pump(win, 0.3)
        width, height = win._sprites.cell_size
        rects = win._shape.get_rects()
        shown = lambda x, y: any(rx <= x < rx + rw and ry <= y < ry + rh for rx, ry, rw, rh in rects)
        ox, oy = (WIDTH - width) // 2, SPRITE_BOTTOM - height
        for clip in ("idle", "blink", "blink_glitch", "grin"):
            for cell in range(CLIPS[clip].cells):
                for dy in (-1, 0, 1):  # her breathing
                    for x, y, w, h in win._sprites.silhouette(clip, cell):
                        self.assertTrue(shown(ox + x, oy + y + dy) and shown(ox + x + w - 1, oy + y + h - 1 + dy), (clip, cell, dy))

    # ---- smoothness: her frames should land on time and nothing should hitch the first time it is used

    def test_every_frame_is_made_before_she_starts(self):
        """Making a frame the first time it is shown (a few ms each) made her hitch."""
        from babyai.ui.capri_body import CLIPS as clips

        win = self.window()
        wanted = {(name, cell) for name, clip in clips.items() for cell in range(clip.cells)}
        self.assertEqual(wanted - set(win._sprites._cells), set())

    def test_every_outline_is_worked_out_before_she_starts(self):
        win = self.window()
        if win._shape is None:
            self.skipTest("no X11 shape here")
        for clip in CLIPS:
            key = (win._pose_family(clip), 0, win._breathing_room())
            self.assertIn(key, win._sprites._unions, f"{clip}'s outline would be worked out mid-animation")

    def test_the_tick_is_much_shorter_than_her_fastest_frame(self):
        """With a coarse tick a 15 fps frame was held 40 ms, then 80 ms (uneven); at ~16 ms it stays within a few ms."""
        from babyai.ui.app import TICK_MS

        fastest = min(1000 / clip.fps for clip in CLIPS.values())
        self.assertLessEqual(TICK_MS * 3, fastest)

    def test_ticks_keep_their_rhythm_and_never_burst(self):
        from babyai.ui.app import TICK_MS

        win = self.window()
        now = time.monotonic()
        win._tick_due = now  # a tick that was due right now, and has taken 5 ms to run
        time.sleep(0.005)
        delay = win._tick_delay()
        self.assertAlmostEqual(win._tick_due, now + TICK_MS / 1000, places=6)  # the next one is due a tick after the last was due ...
        self.assertLessEqual(delay, TICK_MS - 4)  # ... so the 5 ms already spent is not added on top
        win._tick_due = time.monotonic() - 2.0  # far behind (the machine stalled): carry on from now, don't catch up in a burst
        self.assertEqual(win._tick_delay(), 1)
        self.assertGreaterEqual(win._tick_due, time.monotonic() - 0.05)

    def test_the_bubble_is_redrawn_once_per_letter_not_once_per_tick(self):
        win = self.window()
        self.pump(win, 1.2, until=lambda: win._body_drawn[0] == "idle")
        renders = []
        real = win._render_bubble
        win._render_bubble = lambda *a, **k: (renders.append(1), real(*a, **k))[1]
        text = "Pets logged. Morale up twelve percent."
        win._set_reply(text)
        renders.clear()
        self.pump(win, 4.0, until=lambda: win._shown >= win._target)
        self.assertEqual(win._shown, win._target)
        self.assertLessEqual(len(renders), len(text) + 2)  # one per new letter; per tick would be ~2x that or more

    def test_she_only_moves_when_breathing_takes_her_a_whole_pixel(self):
        win = self.window()
        self.pump(win, 1.2, until=lambda: win._body_drawn[0] == "idle")
        moves = []
        real = win.canvas.coords
        win.canvas.coords = lambda item, *a: (moves.append(item) if a and item == win._body_item else None, real(item, *a))[1]
        self.pump(win, 2.0)
        self.assertLessEqual(len(moves), 12, "she moved on nearly every tick")

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
