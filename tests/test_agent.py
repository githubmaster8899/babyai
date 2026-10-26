import unittest

from babyai.tools.registry import ToolRegistry, build_default_registry


class ToolRegistryTests(unittest.TestCase):
    def test_registry_has_21_tools(self):
        registry = build_default_registry()
        self.assertEqual(len(registry.tools), 21)

    def test_registry_names_match_expected(self):
        registry = build_default_registry()
        expected = {
            "read_file",
            "write_file",
            "edit_file",
            "list_directory",
            "find_files",
            "search_files",
            "run_shell",
            "get_system_info",
            "manage_processes",
            "web_search",
            "fetch_url",
            "remember_fact",
            "recall_memory",
            "forget_memory",
            "ask_user",
            "notify",
            "speak",
            "listen",
            "capture_screen",
            "get_clipboard",
            "set_clipboard",
        }
        self.assertSetEqual(set(registry.tools.keys()), expected)


if __name__ == "__main__":
    unittest.main()
