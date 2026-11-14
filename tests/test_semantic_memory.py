import os
import tempfile
import unittest

from babyai.memory.db import MemoryStore, semantic_score, semantic_tokens


class SemanticRecallTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.store = MemoryStore(os.path.join(self.dir.name, "m.db"))
        self.addCleanup(self.store.close)
        self.store.remember_fact("The user's favourite food is ramen.", "user")
        self.store.remember_fact("The user has a cat called Miso.", "user")
        self.store.remember_fact("The user is learning the guitar.", "user")

    def facts(self, query, limit=5):
        return [item["fact"] for item in self.store.search_memory(query, limit=limit)]

    def test_a_question_about_eating_finds_the_food_fact(self):
        found = self.facts("what do I like to eat?")
        self.assertTrue(any("ramen" in fact for fact in found), found)

    def test_a_question_about_pets_finds_the_cat_fact(self):
        found = self.facts("do I have any pets?")
        self.assertTrue(any("Miso" in fact for fact in found), found)

    def test_a_word_that_merely_starts_like_a_fact_word_finds_nothing(self):
        self.assertEqual(self.store.search_semantic("category"), [])

    def test_an_unrelated_word_finds_nothing(self):
        self.assertEqual(self.store.search_semantic("quantum chromodynamics"), [])

    def test_exact_matches_are_still_first(self):
        found = self.store.search_memory("guitar")
        self.assertIn("guitar", found[0]["fact"])

    def test_semantic_results_do_not_duplicate_exact_ones(self):
        found = self.store.search_memory("ramen")
        self.assertEqual(len([item for item in found if "ramen" in item["fact"]]), 1)

    def test_limit_is_respected(self):
        self.assertLessEqual(len(self.store.search_semantic("food", limit=1)), 1)

    def test_conversations_are_still_searched(self):
        self.store.append_conversation("s1", "user", "We talked about a pizza oven.")
        found = self.store.search_memory("pizza")
        self.assertTrue(any(item.get("content") for item in found))

    def test_recent_facts_are_newest_first_and_bounded(self):
        facts = self.store.recent_facts(limit=2)
        self.assertEqual(len(facts), 2)
        self.assertIn("guitar", facts[0]["fact"])
        self.assertEqual(len(self.store.recent_facts(limit=0)), 1)


class SemanticScoringTests(unittest.TestCase):
    def test_shared_words_score_highest(self):
        exact = semantic_score(semantic_tokens("guitar"), semantic_tokens("learning the guitar"))
        concept = semantic_score(semantic_tokens("eat"), semantic_tokens("favourite food is ramen"))
        unrelated = semantic_score(semantic_tokens("guitar"), semantic_tokens("a cat called Miso"))
        self.assertGreater(exact, concept)
        self.assertGreater(concept, unrelated)
        self.assertEqual(unrelated, 0.0)

    def score(self, query, fact):
        return semantic_score(semantic_tokens(query), semantic_tokens(fact))

    def test_a_word_and_its_longer_form_count_as_half_a_match(self):
        self.assertAlmostEqual(self.score("eating", "I eat noodles"), 0.5 / 1.5)
        self.assertAlmostEqual(self.score("cats", "the cat sleeps"), 0.5 / 1.5)
        self.assertAlmostEqual(self.score("recommendation", "a recommend list"), 0.5 / 1.5)

    def test_a_short_word_is_not_a_stem_of_just_any_longer_word(self):
        self.assertEqual(self.score("category", "a cat called Miso"), 0.0)
        self.assertEqual(self.score("program", "the pro model"), 0.0)
        self.assertEqual(self.score("airplane", "my tv is on ai"), 0.0)

    def test_empty_text_scores_zero(self):
        self.assertEqual(semantic_score([], ["ramen"]), 0.0)
        self.assertEqual(semantic_score(["ramen"], []), 0.0)


if __name__ == "__main__":
    unittest.main()
