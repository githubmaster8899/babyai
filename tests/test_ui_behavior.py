import unittest

from babyai.ui import behavior
from babyai.ui.behavior import SleepClock, eye_offset, thinking_label


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


if __name__ == "__main__":
    unittest.main()
