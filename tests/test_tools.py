import os
import tempfile
import threading
import unittest

from babyai.memory.store import MemoryStore


class MemoryStoreTests(unittest.TestCase):
    def test_remember_and_search(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            store = MemoryStore(path)
            store.remember_fact("The user likes Python.", source="user")
            results = store.search_memory("Python")
            self.assertTrue(any("Python" in item["fact"] for item in results))
        finally:
            os.remove(path)


class MemorySafetyTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = MemoryStore(os.path.join(self.dir.name, "m.db"))
        self.store.remember_fact("The user's favorite food is pineapple pizza.", "user")
        self.store.remember_fact("The user is learning the guitar.", "user")

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def test_ordinary_questions_do_not_crash_the_search(self):
        for query in ("what's the user's favorite food?", "pizza-lover", 'say "pizza"', "pineapple AND", "user:pizza", "pizza!!", "  "):
            self.store.search_memory(query)  # none of these may raise

    def test_a_natural_question_finds_the_fact(self):
        found = self.store.search_memory("what's the user's favorite food?")
        self.assertTrue(any("pineapple" in item["fact"] for item in found))
        self.assertTrue(any("pineapple" in item["fact"] for item in self.store.search_memory("what do I like on pizza?")))

    def test_nothing_to_look_for_finds_nothing(self):
        self.assertEqual(self.store.search_memory("?!"), [])
        self.assertEqual(self.store.forget_memory(query="?!"), 0)

    def test_forgetting_needs_every_word_to_match(self):
        self.assertEqual(self.store.forget_memory(query="pineapple guitar"), 0)  # no single fact has both
        self.assertEqual(len(self.store.search_memory("pineapple")), 1)
        self.assertEqual(self.store.forget_memory(query="pineapple pizza"), 1)
        self.assertEqual(self.store.search_memory("pineapple"), [])
        self.assertTrue(self.store.search_memory("guitar"))  # the other fact is untouched

    def test_it_works_from_another_thread(self):
        outcome = {}

        def worker():
            try:
                self.store.remember_fact("The user has a cat called Miso.", "user")
                outcome["found"] = self.store.search_memory("cat")
            except Exception as exc:  # pragma: no cover - the failure being guarded against
                outcome["error"] = exc

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        self.assertNotIn("error", outcome)
        self.assertTrue(outcome["found"])
        self.assertTrue(self.store.search_memory("Miso"))  # and the main thread sees what the worker wrote


if __name__ == "__main__":
    unittest.main()
