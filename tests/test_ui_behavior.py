import math
import os
import tempfile
import unittest

from babyai.config.loader import DEFAULT_CONFIG, load_config
from babyai.ui import behavior
from babyai.ui.behavior import BubbleTimer, SleepClock, UiTimings, cursor_line, eye_offset, scroll_top, thinking_label, timings_from_config


class ThinkingLabelTests(unittest.TestCase):
    def test_nothing_is_shown_before_the_threshold(self):
        self.assertIsNone(thinking_label(0))
        self.assertIsNone(thinking_label(14.99))

    def test_label_appears_at_the_threshold_and_counts_up(self):
        self.assertEqual(thinking_label(15), "THINKING… 15 s")
        self.assertEqual(thinking_label(15.9), "THINKING… 15 s")
        self.assertEqual(thinking_label(16), "THINKING… 16 s")
        self.assertEqual(thinking_label(125.4), "THINKING… 125 s")

    def test_threshold_is_configurable(self):
        self.assertIsNone(thinking_label(4, after_s=5))
        self.assertEqual(thinking_label(5, after_s=5), "THINKING… 5 s")

    def test_default_threshold_is_about_fifteen_seconds(self):
        self.assertEqual(behavior.THINK_AFTER_S, 15.0)


class SleepClockTests(unittest.TestCase):
    def test_awake_at_start_and_before_the_threshold(self):
        clock = SleepClock(now=100, sleep_after_s=600)
        self.assertFalse(clock.asleep)
        self.assertFalse(clock.tick(699.9))

    def test_falls_asleep_once_idle_long_enough(self):
        clock = SleepClock(now=100, sleep_after_s=600)
        self.assertTrue(clock.tick(700))
        self.assertTrue(clock.asleep)
        self.assertTrue(clock.tick(701))  # and stays asleep

    def test_activity_restarts_the_idle_timer(self):
        clock = SleepClock(now=0, sleep_after_s=600)
        self.assertFalse(clock.touch(500))  # was awake, so nothing was woken
        self.assertFalse(clock.tick(1000))  # only 500 s since the touch
        self.assertTrue(clock.tick(1100))

    def test_touch_wakes_a_sleeping_figure(self):
        clock = SleepClock(now=0, sleep_after_s=600)
        clock.tick(700)
        self.assertTrue(clock.touch(710))  # True = it was asleep
        self.assertFalse(clock.asleep)
        self.assertFalse(clock.tick(711))
        self.assertTrue(clock.tick(1310))  # a new idle period has to pass first

    def test_pointer_moving_close_wakes_it(self):
        clock = SleepClock(now=0, sleep_after_s=600, wake_radius_px=150)
        clock.tick(700)
        self.assertFalse(clock.tick(701, pointer_dist=100, pointer_moved=True))

    def test_pointer_resting_close_does_not_wake_it(self):
        clock = SleepClock(now=0, sleep_after_s=600, wake_radius_px=150)
        clock.tick(700)
        self.assertTrue(clock.tick(701, pointer_dist=100, pointer_moved=False))

    def test_pointer_moving_far_away_or_unknown_does_not_wake_it(self):
        clock = SleepClock(now=0, sleep_after_s=600, wake_radius_px=150)
        clock.tick(700)
        self.assertTrue(clock.tick(701, pointer_dist=151, pointer_moved=True))
        self.assertTrue(clock.tick(702, pointer_dist=None, pointer_moved=True))

    def test_pointer_near_the_edge_of_the_radius_counts(self):
        clock = SleepClock(now=0, sleep_after_s=600, wake_radius_px=150)
        clock.tick(700)
        self.assertFalse(clock.tick(701, pointer_dist=150, pointer_moved=True))

    def test_pointer_moving_nearby_keeps_an_awake_figure_awake(self):
        clock = SleepClock(now=0, sleep_after_s=600, wake_radius_px=150)
        clock.tick(590, pointer_dist=50, pointer_moved=True)  # restarts the idle timer
        self.assertFalse(clock.tick(1000))
        self.assertTrue(clock.tick(1190))

    def test_zero_turns_auto_sleep_off(self):
        clock = SleepClock(now=0, sleep_after_s=0)
        self.assertFalse(clock.tick(10**9))

    def test_defaults_are_ten_minutes_and_150_px(self):
        clock = SleepClock(now=0)
        self.assertEqual(clock.sleep_after_s, 600.0)
        self.assertEqual(clock.wake_radius_px, 150.0)


class EyeOffsetTests(unittest.TestCase):
    def test_eyes_stay_centred_outside_the_radius(self):
        self.assertEqual(eye_offset(300, 0), (0, 0))
        self.assertEqual(eye_offset(0, -251), (0, 0))
        self.assertEqual(eye_offset(200, 200), (0, 0))  # ~283 px away

    def test_the_edge_of_the_radius_counts_as_inside(self):
        self.assertEqual(eye_offset(250, 0), (6, 0))

    def test_eyes_turn_toward_the_pointer_in_each_direction(self):
        self.assertEqual(eye_offset(200, 0), (6, 0))
        self.assertEqual(eye_offset(-200, 0), (-6, 0))
        self.assertEqual(eye_offset(0, 200), (0, 4))
        self.assertEqual(eye_offset(0, -200), (0, -4))

    def test_diagonal_pointer_turns_both_axes(self):
        self.assertEqual(eye_offset(141.42, 141.42), (4, 3))
        self.assertEqual(eye_offset(-141.42, -141.42), (-4, -3))

    def test_pointer_on_the_head_leaves_the_eyes_centred(self):
        self.assertEqual(eye_offset(0, 0), (0, 0))

    def test_eyes_ease_in_as_the_pointer_gets_close(self):
        self.assertEqual(eye_offset(40, 0), (3, 0))  # half of full_at -> half of the reach
        self.assertEqual(eye_offset(80, 0), (6, 0))

    def test_radius_and_reach_are_configurable(self):
        self.assertEqual(eye_offset(150, 0, radius=100), (0, 0))
        self.assertEqual(eye_offset(90, 0, radius=100, reach=(10, 5), full_at=90), (10, 0))


class ManualSleepTests(unittest.TestCase):
    def test_sleep_puts_the_figure_to_sleep_straight_away(self):
        clock = SleepClock(now=0, sleep_after_s=600)
        clock.sleep()
        self.assertTrue(clock.asleep)
        self.assertTrue(clock.tick(1))

    def test_a_passing_pointer_does_not_wake_a_manual_sleep(self):
        clock = SleepClock(now=0, sleep_after_s=600, wake_radius_px=150)
        clock.sleep()
        self.assertTrue(clock.tick(1, pointer_dist=10, pointer_moved=True))

    def test_a_click_or_key_wakes_a_manual_sleep(self):
        clock = SleepClock(now=0, sleep_after_s=600)
        clock.sleep()
        self.assertTrue(clock.touch(5))  # it was asleep
        self.assertFalse(clock.asleep)

    def test_after_waking_the_pointer_works_again(self):
        clock = SleepClock(now=0, sleep_after_s=600, wake_radius_px=150)
        clock.sleep()
        clock.touch(5)
        clock.tick(700)  # idle again -> ordinary sleep
        self.assertTrue(clock.asleep)
        self.assertFalse(clock.tick(701, pointer_dist=10, pointer_moved=True))

    def test_manual_sleep_stays_on_with_auto_sleep_disabled(self):
        clock = SleepClock(now=0, sleep_after_s=0)
        clock.sleep()
        self.assertTrue(clock.tick(10**6))


class UiTimingsTests(unittest.TestCase):
    def test_default_config_matches_the_built_in_timings(self):
        # keeps today's behaviour: nothing changes unless config.toml says so
        self.assertEqual(timings_from_config(DEFAULT_CONFIG), UiTimings())
        self.assertEqual(UiTimings().think_after_s, 15.0)
        self.assertEqual(UiTimings().sleep_after_s, 600.0)
        self.assertEqual(UiTimings().wake_radius_px, 150.0)
        self.assertEqual(UiTimings().look_radius_px, 250.0)

    def test_values_in_the_ui_section_are_used(self):
        config = {"ui": {"think_after_s": 5, "sleep_after_s": 60.5, "wake_radius_px": 90, "look_radius_px": 400}}
        self.assertEqual(timings_from_config(config), UiTimings(5.0, 60.5, 90.0, 400.0))

    def test_zero_is_allowed_and_turns_auto_sleep_off(self):
        self.assertEqual(timings_from_config({"ui": {"sleep_after_s": 0}}).sleep_after_s, 0.0)

    def test_missing_or_malformed_config_falls_back_to_defaults(self):
        for config in (None, {}, {"ui": None}, {"ui": "fast"}, {"ui": []}, "nonsense", 42):
            self.assertEqual(timings_from_config(config), UiTimings(), config)

    def test_bad_individual_values_fall_back_one_by_one(self):
        config = {"ui": {"think_after_s": "soon", "sleep_after_s": -5, "wake_radius_px": True, "look_radius_px": 123}}
        self.assertEqual(timings_from_config(config), UiTimings(look_radius_px=123.0))

    def test_not_finite_numbers_are_rejected(self):
        config = {"ui": {"sleep_after_s": math.nan, "look_radius_px": math.inf}}
        self.assertEqual(timings_from_config(config), UiTimings())

    def test_a_config_toml_can_override_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.toml")
            with open(path, "w") as handle:
                handle.write("[ui]\nsleep_after_s = 120\nlook_radius_px = 300\n")
            timings = timings_from_config(load_config(path))
        self.assertEqual(timings, UiTimings(sleep_after_s=120.0, look_radius_px=300.0))


class BubbleScrollTests(unittest.TestCase):
    def test_the_cursor_is_on_the_line_being_typed(self):
        lengths = [5, 3, 4]  # each line also counts the break after it: 6, 4, 5 letters
        self.assertEqual([cursor_line(lengths, n) for n in (0, 5, 6, 9, 10, 14, 15, 99)], [0, 0, 1, 1, 2, 2, 2, 2])
        self.assertEqual(cursor_line([], 3), 0)

    def test_a_short_reply_is_not_scrolled(self):
        self.assertEqual(scroll_top(3, 8, 2), 0)
        self.assertEqual(scroll_top(8, 8, 7), 0)

    def test_the_view_follows_the_line_being_typed(self):
        self.assertEqual([scroll_top(12, 8, cursor) for cursor in range(12)], [0, 0, 0, 0, 0, 0, 0, 0, 1, 2, 3, 4])

    def test_it_never_scrolls_past_the_reply(self):
        self.assertEqual(scroll_top(12, 8, 99), 4)
        self.assertEqual(scroll_top(12, 8, 0, user_top=99), 4)
        self.assertEqual(scroll_top(12, 8, 11, user_top=-5), 0)

    def test_once_the_reader_scrolls_the_view_stays_put(self):
        self.assertEqual(scroll_top(12, 8, 11, user_top=1), 1)
        self.assertEqual(scroll_top(12, 8, 2, user_top=3), 3)


class BubbleTimerTests(unittest.TestCase):
    def test_it_hides_the_bubble_after_the_delay(self):
        timer = BubbleTimer(10)
        self.assertFalse(timer.update(100.0, busy=False, hidden=False))  # starts counting now
        self.assertFalse(timer.update(109.9, busy=False, hidden=False))
        self.assertTrue(timer.update(110.0, busy=False, hidden=False))

    def test_it_does_not_run_while_she_is_busy(self):
        timer = BubbleTimer(10)
        for now in (0.0, 30.0, 60.0):
            self.assertFalse(timer.update(now, busy=True, hidden=False))
        self.assertFalse(timer.update(61.0, busy=False, hidden=False))  # the count starts only when she is done
        self.assertTrue(timer.update(71.0, busy=False, hidden=False))

    def test_going_busy_again_starts_it_over(self):
        timer = BubbleTimer(10)
        timer.update(0.0, False, False)
        timer.update(8.0, True, False)
        self.assertFalse(timer.update(12.0, False, False))
        self.assertFalse(timer.update(21.9, False, False))
        self.assertTrue(timer.update(22.0, False, False))

    def test_touching_it_starts_it_over(self):
        timer = BubbleTimer(10)
        timer.update(0.0, False, False)
        timer.update(9.0, False, False)
        timer.touch()  # the reader is looking at it
        self.assertFalse(timer.update(10.0, False, False))
        self.assertFalse(timer.update(19.9, False, False))
        self.assertTrue(timer.update(20.0, False, False))

    def test_zero_keeps_the_bubble_up_forever(self):
        timer = BubbleTimer(0)
        self.assertFalse(timer.update(0.0, False, False))
        self.assertFalse(timer.update(10_000.0, False, False))

    def test_a_hidden_bubble_is_left_alone(self):
        timer = BubbleTimer(10)
        timer.update(0.0, False, False)
        self.assertFalse(timer.update(50.0, False, True))
        self.assertFalse(timer.update(51.0, False, False))  # shown again: counts from here
        self.assertTrue(timer.update(61.0, False, False))


class BubbleSettingTests(unittest.TestCase):
    def test_it_defaults_to_ten_seconds(self):
        self.assertEqual(UiTimings().bubble_hide_s, 10.0)
        self.assertEqual(timings_from_config(DEFAULT_CONFIG).bubble_hide_s, 10.0)

    def test_it_can_be_set_or_turned_off_and_bad_values_fall_back(self):
        self.assertEqual(timings_from_config({"ui": {"bubble_hide_s": 4}}).bubble_hide_s, 4.0)
        self.assertEqual(timings_from_config({"ui": {"bubble_hide_s": 0}}).bubble_hide_s, 0.0)
        self.assertEqual(timings_from_config({"ui": {"bubble_hide_s": "soon"}}).bubble_hide_s, 10.0)
        self.assertEqual(timings_from_config({"ui": {"bubble_hide_s": -3}}).bubble_hide_s, 10.0)


if __name__ == "__main__":
    unittest.main()
