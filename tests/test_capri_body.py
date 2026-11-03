import math
import unittest

from babyai.ui import capri_body as cb


class Rolls:
    """A random source that returns the queued numbers, then 0.99 (no blink, no glitch jitter)."""

    def __init__(self, *values):
        self.values = list(values)

    def random(self):
        return self.values.pop(0) if self.values else 0.99


def settle(animator, now):
    """Run the intro so the animator is in its idle loop at time `now`."""
    clip = cb.CLIPS["arrive"]
    now += clip.cells / clip.fps + 0.01
    animator.frame(now)
    assert animator.clip == "idle", animator.clip
    return now


class MouthTests(unittest.TestCase):
    def test_letters_map_like_capris_talk_cs(self):
        self.assertEqual([cb.mouth_for(c, 0) for c in "aeo"], [3, 3, 3])
        self.assertEqual([cb.mouth_for(c, 0) for c in "AEO"], [4, 4, 4])
        self.assertEqual([cb.mouth_for(c, 0) for c in "iuy"], [2, 2, 2])
        self.assertEqual([cb.mouth_for(c, 0) for c in "mbp ,.!7"], [0] * 8)
        self.assertEqual(cb.mouth_for("t", 1), 1)  # other consonants flap between 1 and 2
        self.assertEqual(cb.mouth_for("t", 2), 2)

    def test_reply_is_typed_at_28_letters_a_second(self):
        self.assertEqual(cb.letters_revealed(0, 100), 0)
        self.assertEqual(cb.letters_revealed(1, 100), 28)
        self.assertEqual(cb.letters_revealed(10, 100), 100)
        self.assertEqual(cb.letters_revealed(-1, 100), 0)

    def test_mouth_follows_the_letter_being_typed_then_closes(self):
        self.assertEqual(cb.mouth_cell("ab", 0.0), 3)  # 'a'
        self.assertEqual(cb.mouth_cell("ab", 1 / 28 + 0.001), 0)  # 'b'
        self.assertEqual(cb.mouth_cell("ab", 2 / 28 + 0.001), -1)  # said
        self.assertEqual(cb.mouth_cell("ab", -1), -1)


class AnimatorTests(unittest.TestCase):
    def test_starts_with_the_arrive_clip_then_idles(self):
        a = cb.Animator(0, Rolls())
        self.assertEqual(a.frame(0).clip, "arrive")
        self.assertEqual(a.frame(13 / 15 - 0.01).cell, 12)
        self.assertEqual(a.frame(14 / 15 + 0.01).clip, "idle")

    def test_no_intro_starts_idle(self):
        self.assertEqual(cb.Animator(0, Rolls(), intro=False).frame(0).clip, "idle")

    def test_idle_cells_advance_at_8_fps(self):
        a = cb.Animator(0, Rolls(), intro=False)
        self.assertEqual([a.frame(i / 8 + 0.01).cell for i in range(4)], [0, 1, 2, 3])

    def test_blink_replaces_idle_cells_9_to_12_after_a_good_roll(self):
        a = cb.Animator(0, Rolls(0.99, 0.3), intro=False)  # first roll is for the glitch gap
        a.frame(0)
        a.frame(2.0 + 0.01)  # first loop is 16 cells / 8 fps = 2 s; the roll happens as loop two starts
        self.assertEqual(a.frame(2.0 + 8 / 8 + 0.01).clip, "idle")  # cell 8: eyes open
        f = a.frame(2.0 + 9 / 8 + 0.01)
        self.assertEqual((f.clip, f.cell), ("blink", 0))
        f = a.frame(2.0 + 12 / 8 + 0.01)
        self.assertEqual((f.clip, f.cell), ("blink", 3))
        self.assertEqual(a.frame(2.0 + 13 / 8 + 0.01).clip, "idle")

    def test_glitchy_blink_on_a_rare_roll(self):
        a = cb.Animator(0, Rolls(0.99, 0.05), intro=False)
        a.frame(2.01)
        self.assertEqual(a.frame(2.0 + 10 / 8 + 0.01).clip, "blink_glitch")

    def test_first_loop_never_blinks(self):
        a = cb.Animator(0, Rolls(0.99, 0.0), intro=False)
        self.assertTrue(all(a.frame(i / 8 + 0.01).clip == "idle" for i in range(16)))

    def test_glitch_waits_for_its_timer_and_for_quiet(self):
        a = cb.Animator(0, Rolls(0.0, 0.99), intro=False)  # next glitch at 30 s
        a.frame(2.01, quiet=True)
        self.assertEqual(a.clip, "idle")  # too early
        a.frame(32.01, quiet=False)
        self.assertEqual(a.clip, "idle")  # due, but she is talking
        a.frame(34.01, quiet=True)
        self.assertEqual(a.clip, "glitch")
        a.frame(34.01 + 10 / 15 + 0.01)
        self.assertEqual(a.clip, "idle")  # glitch plays once

    def test_petting_grins_for_two_loops_then_idles(self):
        a = cb.Animator(0, Rolls(), intro=False)
        a.pet(5)
        self.assertEqual(a.frame(5.01).clip, "grin")
        self.assertEqual(a.frame(5 + 31 / 8).clip, "grin")  # still on the 32nd cell
        self.assertEqual(a.frame(5 + 32 / 8 + 0.01).clip, "idle")

    def test_sleep_from_idle_then_wake_with_the_boot_glitch(self):
        a = cb.Animator(0, Rolls(), intro=False)
        a.set_asleep(True, 3)
        self.assertEqual(a.frame(3.01).clip, "sleep")
        self.assertEqual(a.frame(3 + 40).clip, "sleep")  # sleep loops
        a.set_asleep(False, 50)
        self.assertEqual(a.frame(50.01).clip, "wake")
        self.assertEqual(a.frame(50 + 13 / 15 + 0.01).clip, "idle")

    def test_she_finishes_a_glitch_before_dropping_off(self):
        a = cb.Animator(0, Rolls(), intro=False)
        a.play("glitch", 0)
        a.set_asleep(True, 0.1)
        self.assertEqual(a.clip, "glitch")
        a.frame(10 / 15 + 0.01)
        a.set_asleep(True, 1)
        self.assertEqual(a.clip, "sleep")

    def test_speaking_wakes_her_and_cuts_off_the_grin(self):
        a = cb.Animator(0, Rolls(), intro=False)
        a.pet(1)
        a.speak(1.2)
        self.assertEqual(a.clip, "idle")
        a.play("sleep", 2)
        a.speak(2.1)
        self.assertEqual(a.clip, "idle")

    def test_she_cannot_be_petted_awake_into_a_grin(self):
        a = cb.Animator(0, Rolls(), intro=False)
        a.play("sleep", 0)
        a.pet(1)
        self.assertEqual(a.clip, "sleep")

    def test_mouth_only_shows_in_the_plain_idle_clip(self):
        a = cb.Animator(0, Rolls(0.99, 0.05), intro=False)
        self.assertEqual(a.frame(0.1, mouth=3).mouth, 3)
        a.frame(2.01)
        self.assertEqual(a.frame(2.0 + 10 / 8 + 0.01, mouth=3).mouth, -1)  # glitchy blink: no jaw
        a.play("grin", 5)
        self.assertEqual(a.frame(5.1, mouth=3).mouth, -1)

    def test_breathing_is_a_gentle_sine(self):
        a = cb.Animator(0, Rolls(), intro=False)
        for t in (0, 0.5, 1.3, 7.7):
            self.assertAlmostEqual(a.frame(t).breathe, math.sin(t * 1.75) * 2.2)
        self.assertLessEqual(max(abs(a.frame(t / 10).breathe) for t in range(200)), 2.2)


class PetDetectorTests(unittest.TestCase):
    def stroke(self, detector, start, xs, step=0.1, body=True):
        t, hit = start, False
        for x in xs:
            hit = detector.feed(x, t, body) or hit
            t += step
        return hit

    ZIGZAG = [0, 20, 0, 20, 0, 20, 0, 20]

    def test_back_and_forth_over_her_body_is_a_pet(self):
        self.assertTrue(self.stroke(cb.PetDetector(), 100, self.ZIGZAG))

    def test_a_straight_pass_is_not(self):
        self.assertFalse(self.stroke(cb.PetDetector(), 100, range(0, 400, 20)))

    def test_it_has_to_be_over_her_body(self):
        self.assertFalse(self.stroke(cb.PetDetector(), 100, self.ZIGZAG, body=False))

    def test_slow_wiggling_does_not_count(self):
        self.assertFalse(self.stroke(cb.PetDetector(), 100, self.ZIGZAG, step=1.0))

    def test_the_minimum_step_scales_with_the_sprite(self):
        wiggle = [0, 4, 0, 4, 0, 4, 0, 4]
        self.assertFalse(self.stroke(cb.PetDetector(), 100, wiggle))
        self.assertTrue(self.stroke(cb.PetDetector(min_step_px=3), 100, wiggle))

    def test_tiny_jitter_does_not_count(self):
        self.assertFalse(self.stroke(cb.PetDetector(), 100, [0, 3, 0, 3, 0, 3, 0, 3, 0, 3]))

    def test_cooldown_between_pets_is_twenty_seconds(self):
        d = cb.PetDetector()
        self.assertTrue(self.stroke(d, 100, self.ZIGZAG))
        self.assertFalse(self.stroke(d, 105, self.ZIGZAG))
        self.assertTrue(self.stroke(d, 125, self.ZIGZAG))

    def test_blocked_while_dragging_or_typing(self):
        d = cb.PetDetector()
        hit = False
        for i, x in enumerate(self.ZIGZAG):
            hit = d.feed(x, 100 + i * 0.1, True, blocked=True) or hit
        self.assertFalse(hit)

    def test_pet_lines_cycle(self):
        self.assertEqual(cb.pet_line(0), "Heh. Okay, I'll allow it.")
        self.assertEqual(cb.pet_line(3), cb.pet_line(0))


if __name__ == "__main__":
    unittest.main()
