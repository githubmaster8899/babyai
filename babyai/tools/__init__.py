import unittest

from babyai.config.loader import load_config


class AgentConfigTests(unittest.TestCase):
    def test_default_config_loads(self):
        config = load_config()
        self.assertIn("llm", config)
        self.assertIn("agent", config)
        self.assertIn("memory", config)
        self.assertIn("voice", config)
        self.assertIn("safety", config)


if __name__ == "__main__":
    unittest.main()
