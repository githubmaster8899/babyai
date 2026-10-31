import unittest

from babyai.ui import behavior
from babyai.ui.behavior import thinking_label


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


if __name__ == "__main__":
    unittest.main()
