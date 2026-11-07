import os
import tempfile
import unittest

from babyai.agent.core import BabyAI
from babyai.llm.client import LLMClient, LLMRequestError
from babyai.ui.persona import apply_character
from tests.fake_ollama import FakeOllama


def assistant(url, model="llama3.1:8b", character=True, timeout=None):
    tmp = tempfile.TemporaryDirectory()
    config = os.path.join(tmp.name, "config.toml")
    extra = f"timeout = {timeout}\n" if timeout else ""
    with open(config, "w") as handle:
        handle.write(f'[llm]\nbase_url = "{url}"\nmodel = "{model}"\n{extra}[memory]\ndb_path = "{tmp.name}/m.db"\n')
    ai = BabyAI(config_path=config)
    ai._tmp = tmp
    if character:
        apply_character(ai)
    return ai


class ClientTests(unittest.TestCase):
    def test_lists_models_and_chats(self):
        with FakeOllama(models=["llama3.2:3b", "qwen2.5:7b"]) as server:
            client = LLMClient(server.url, "qwen2.5:7b")
            self.assertEqual(client.list_models(), ["llama3.2:3b", "qwen2.5:7b"])
            self.assertEqual(client.complete([{"role": "user", "content": "ping"}])["choices"][0]["message"]["content"], "pong")

    def test_the_servers_own_explanation_is_kept(self):
        with FakeOllama() as server:
            with self.assertRaises(LLMRequestError) as caught:
                LLMClient(server.url, "nope").complete([{"role": "user", "content": "x"}])
        self.assertEqual(caught.exception.status, 404)
        self.assertIn("try pulling it first", caught.exception.detail)
        self.assertIn("try pulling it first", str(caught.exception))

    def test_no_server_means_no_models_not_a_crash(self):
        self.assertEqual(LLMClient("http://127.0.0.1:9/v1", "x").list_models(timeout=1), [])

    def test_an_api_key_is_only_sent_when_there_is_one(self):
        self.assertNotIn("Authorization", LLMClient("http://x/v1", "m")._headers())
        self.assertEqual(LLMClient("http://x/v1", "m", api_key="k")._headers()["Authorization"], "Bearer k")


class FirstConversationTests(unittest.TestCase):
    def test_it_just_works_when_the_model_is_there(self):
        with FakeOllama(script=[{"role": "assistant", "content": "Hey. Nova to meet you."}]) as server:
            ai = assistant(server.url)
            self.assertEqual(ai.process_user_input("hi"), "Hey. Nova to meet you.")
            sent = server.requests[0]
            self.assertEqual(sent["model"], "llama3.1:8b")
            self.assertTrue(sent["tools"], "tools are offered")
            self.assertEqual(sent["messages"][0]["role"], "system")
            self.assertTrue(sent["messages"][0]["content"].startswith("You are Capri"))

    def test_a_missing_model_lists_what_the_server_does_have(self):
        with FakeOllama(models=["llama3.2:3b", "qwen2.5:7b"]) as server:
            ai = assistant(server.url, model="llama-3.1-8b")
            reply = ai.process_user_input("hi")
        self.assertIn("no model called 'llama-3.1-8b'", reply)
        self.assertIn("llama3.2:3b, qwen2.5:7b", reply)
        self.assertIn("[llm] model", reply)
        self.assertEqual(ai.context.history, [])

    def test_a_missing_model_on_an_empty_server_says_how_to_pull(self):
        with FakeOllama(models=[]) as server:
            reply = assistant(server.url, model="llama3.1:8b").process_user_input("hi")
        self.assertIn("ollama pull llama3.1:8b", reply)

    def test_a_model_without_tool_support_still_chats(self):
        with FakeOllama(models=["gemma:2b"], no_tools=["gemma:2b"]) as server:
            ai = assistant(server.url, model="gemma:2b")
            self.assertEqual(ai.process_user_input("hi"), "pong")
            self.assertEqual(ai.process_user_input("again"), "pong")
            with_tools = [bool(r.get("tools")) for r in server.requests]
        self.assertEqual(with_tools, [True, False, False])  # one refused attempt, then no tools at all

    def test_a_slow_model_is_explained_not_blamed_on_the_network(self):
        with FakeOllama(delay=2.5) as server:
            reply = assistant(server.url, timeout=1).process_user_input("hi")
        self.assertIn("took too long", reply)
        self.assertIn("[llm] timeout", reply)

    def test_nothing_listening_says_to_start_a_server(self):
        reply = assistant("http://127.0.0.1:9/v1").process_user_input("hi")
        self.assertIn("http://127.0.0.1:9/v1", reply)
        self.assertIn("Start a model server", reply)

    def test_the_cli_without_the_character_keeps_its_plain_reply(self):
        with FakeOllama(models=["other"]) as server:
            reply = assistant(server.url, character=False).process_user_input("hello")
        self.assertIn("BabyAI", reply)


if __name__ == "__main__":
    unittest.main()
