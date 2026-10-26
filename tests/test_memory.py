import unittest

from babyai.safety.gate import SafetyGate


class SafetyGateTests(unittest.TestCase):
    def test_dangerous_shell_command_is_denied(self):
        gate = SafetyGate(level="strict")
        result = gate.check("run_shell", {"command": "rm -rf /"}, interactive=True)
        self.assertFalse(result["allowed"])

    def test_medium_risk_requires_confirmation(self):
        gate = SafetyGate(level="strict")
        result = gate.check("write_file", {"path": "output.txt", "content": "hello"}, interactive=True)
        self.assertTrue(result["needs_confirmation"])

    def test_permissive_mode_allows_non_critical_action(self):
        gate = SafetyGate(level="permissive")
        result = gate.check("list_directory", {"path": "."}, interactive=True)
        self.assertTrue(result["allowed"])


if __name__ == "__main__":
    unittest.main()
