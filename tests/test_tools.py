import os
import tempfile
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


if __name__ == "__main__":
    unittest.main()
