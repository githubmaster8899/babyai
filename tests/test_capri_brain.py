import contextlib
import http.client
import io
import json
import os
import socket
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from babyai import capri_brain
from babyai.capri_brain import BrainError, BrainServer, CapriBrain, fts_query, points_at_self, read_openai_reply, to_openai_messages
from babyai.capri_launcher import LaunchResult
from babyai.llm.client import LLMClient
from babyai.memory.store import MemoryStore
from babyai.safety.gate import SafetyGate

TEXT = {"choices": [{"message": {"role": "assistant", "content": "hello from the model"}, "finish_reason": "stop"}]}


def tool_reply(*calls, content=""):
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": [
                        {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
                        for i, (name, args) in enumerate(calls)
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }


class FakeUpstream:
    """A stand-in OpenAI-compatible model server that records what BabyAI sends it."""

    def __init__(self, reply=None):
        self.requests: list[dict] = []
        self.headers: list = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(body)
                outer.headers.append(self.headers)
                status, payload = (reply(body) if callable(reply) else (200, reply or TEXT))
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Harness:
    """The real brain server on a free port, backed by a fake model server and a temporary memory file."""

    def __init__(self, test, level="strict", reply=None, clock=None):
        self.upstream = FakeUpstream(reply)
        test.addCleanup(self.upstream.close)
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.memory_path = os.path.join(tmp.name, "memory.db")
        llm = LLMClient(base_url=self.upstream.url + "/v1", model="upstream-model", api_key="secret", timeout=5)
        kwargs = {"clock": clock} if clock else {}
        self.brain = CapriBrain(llm, self.memory_path, SafetyGate(level), **kwargs)
        self.logs: list[str] = []
        self.server = BrainServer(("127.0.0.1", 0), self.brain, log=self.logs.append)
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        test.addCleanup(self._stop)
        self.port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"

    def _stop(self):
        self.server.shutdown()
        self.server.server_close()

    def request(self, method, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                raw = response.read().decode()
                return response.status, json.loads(raw) if raw.startswith("{") else raw
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def chat(self, messages, **extra):
        return self.request("POST", "/api/chat", {"model": "x", "messages": messages, "stream": False, **extra})

    def facts(self):
        store = MemoryStore(self.memory_path)
        try:
            return [row["fact"] for row in store.connection.execute("SELECT fact FROM facts ORDER BY id")]
        finally:
            store.close()

    def seed(self, *facts):
        store = MemoryStore(self.memory_path)
        for fact in facts:
            store.remember_fact(fact, "test")
        store.close()


CHAT = [{"role": "system", "content": "You are Capri."}, {"role": "user", "content": "what game do I like playing?"}]
HANDS_TOOLS = [{"type": "function", "function": {"name": "open_app", "description": "d", "parameters": {"type": "object"}}}]


class TranslationTests(unittest.TestCase):
    def test_search_terms_drop_filler_and_punctuation(self):
        self.assertEqual(fts_query("What's my favourite game, Doom?!"), '"favourite" OR "game" OR "doom"')
        self.assertEqual(fts_query("a to be", joiner=" AND "), "")
        self.assertEqual(fts_query('") OR NEAR(x'), '"near"')  # quotes and operators cannot get through

    def test_messages_become_openai_messages(self):
        converted = to_openai_messages([{"role": "system", "content": "s"}, {"role": "user", "content": None}, "junk"])
        self.assertEqual(converted, [{"role": "system", "content": "s"}, {"role": "user", "content": ""}])

    def test_images_become_data_urls(self):
        converted = to_openai_messages([{"role": "user", "content": "what is this?", "images": ["QUJD"]}])
        self.assertEqual(
            converted[0]["content"],
            [
                {"type": "text", "text": "what is this?"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}},
            ],
        )

    def test_assistant_tool_calls_in_history_get_openai_ids_and_string_arguments(self):
        history = [{"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "open_app", "arguments": {"name": "Spotify"}}}]}]
        call = to_openai_messages(history)[0]["tool_calls"][0]
        self.assertEqual(call["function"], {"name": "open_app", "arguments": '{"name": "Spotify"}'})
        self.assertEqual(call["type"], "function")
        self.assertTrue(call["id"])

    def test_reply_text_and_tool_calls_are_read(self):
        text, calls, reason = read_openai_reply(tool_reply(("open_app", {"name": "Spotify"}), content="on it"))
        self.assertEqual((text, calls, reason), ("on it", [("open_app", {"name": "Spotify"})], "stop"))

    def test_string_arguments_are_kept_as_strings_for_capri(self):
        _, calls, _ = read_openai_reply(tool_reply(("media", {"action": "volume_set", "level": 80, "x": None}), ("shortcut", {"name": True})))
        self.assertEqual(calls[0][1], {"action": "volume_set", "level": "80"})
        self.assertEqual(calls[1][1], {"name": "true"})

    def test_other_arguments_keep_their_types(self):
        _, calls, _ = read_openai_reply(tool_reply(("use_computer", {"task": "x", "coordinate": [1.5, 2]})))
        self.assertEqual(calls[0][1]["coordinate"], [1.5, 2])

    def test_unreadable_arguments_become_empty(self):
        reply = {"choices": [{"message": {"content": "", "tool_calls": [{"function": {"name": "open_app", "arguments": "{not json"}}]}}]}
        self.assertEqual(read_openai_reply(reply)[1], [("open_app", {})])

    def test_length_cutoff_is_passed_on_and_empty_replies_are_errors(self):
        self.assertEqual(read_openai_reply({"choices": [{"message": {"content": "hm"}, "finish_reason": "length"}]})[2], "length")
        with self.assertRaises(BrainError):
            read_openai_reply({"choices": []})


class ChatTests(unittest.TestCase):
    def test_models_and_health(self):
        h = Harness(self)
        status, body = h.request("GET", "/api/tags")
        self.assertEqual((status, body["models"][0]["name"]), (200, "babyai:latest"))
        self.assertEqual(h.request("GET", "/")[0], 200)
        self.assertEqual(h.request("GET", "/api/version")[0], 200)
        self.assertEqual(h.request("GET", "/nope")[0], 404)

    def test_a_text_reply_comes_back_in_ollama_shape(self):
        h = Harness(self)
        status, body = h.chat(CHAT)
        self.assertEqual(status, 200)
        self.assertEqual(body["message"], {"role": "assistant", "content": "hello from the model"})
        self.assertTrue(body["done"])
        self.assertEqual(body["done_reason"], "stop")

    def test_upstream_gets_babyais_model_key_and_capris_limits(self):
        h = Harness(self)
        h.chat(CHAT, model="gemma4:e4b", options={"num_predict": 90, "temperature": 0.85, "num_ctx": 4096})
        sent = h.upstream.requests[0]
        self.assertEqual(sent["model"], "upstream-model")  # Capri's model choice is ignored
        self.assertEqual((sent["max_tokens"], sent["temperature"]), (90, 0.85))
        self.assertNotIn("tools", sent)
        self.assertEqual(h.upstream.headers[0]["Authorization"], "Bearer secret")

    def test_a_tool_call_reply_keeps_arguments_as_an_object(self):
        h = Harness(self, reply=tool_reply(("open_app", {"name": "Spotify"})))
        status, body = h.chat([{"role": "user", "content": "open spotify"}], tools=HANDS_TOOLS)
        self.assertEqual(status, 200)
        self.assertEqual(body["message"]["tool_calls"], [{"function": {"name": "open_app", "arguments": {"name": "Spotify"}}}])
        self.assertEqual(h.upstream.requests[0]["tools"], HANDS_TOOLS)

    def test_the_load_model_warm_up_never_reaches_the_model(self):
        h = Harness(self)
        status, body = h.chat([])
        self.assertEqual((status, body["done"], body["message"]["content"]), (200, True, ""))
        self.assertEqual(h.upstream.requests, [])

    def test_pulling_a_model_succeeds_without_doing_anything(self):
        h = Harness(self)
        self.assertEqual(h.request("POST", "/api/pull", {"model": "gemma4:e4b", "stream": True}), (200, {"status": "success"}))

    def test_screenshots_are_forwarded_as_images(self):
        h = Harness(self)
        h.chat([{"role": "system", "content": "s"}, {"role": "user", "content": "what is this?", "images": ["QUJD"]}])
        parts = h.upstream.requests[0]["messages"][1]["content"]
        self.assertEqual(parts[1]["image_url"]["url"], "data:image/jpeg;base64,QUJD")

    def test_a_failing_model_server_is_reported_as_an_error(self):
        h = Harness(self, reply=lambda body: (500, {"error": "boom"}))
        status, body = h.chat(CHAT)
        self.assertEqual(status, 502)
        self.assertIn("500", body["error"])

    def test_bad_requests_are_rejected_cleanly(self):
        h = Harness(self)
        self.assertEqual(h.request("POST", "/api/chat", {"messages": "no"})[0], 400)
        req = urllib.request.Request(h.base + "/api/chat", data=b"{oops", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(h.request("POST", "/api/other", {})[0], 404)

    def test_each_chat_leaves_a_one_line_log_without_the_conversation(self):
        h = Harness(self)
        h.chat(CHAT)
        self.assertEqual(len(h.logs), 1)
        self.assertNotIn("game", h.logs[0])


class MemoryTests(unittest.TestCase):
    def test_remembered_facts_join_capris_chat_prompt(self):
        h = Harness(self)
        h.seed("Their favourite game is Doom", "They live in Perth")
        h.chat(CHAT)
        system = h.upstream.requests[0]["messages"][0]["content"]
        self.assertTrue(system.startswith("You are Capri."))  # her personality comes first and is untouched
        self.assertIn("- Their favourite game is Doom", system)
        self.assertNotIn("Perth", system)

    def test_nothing_is_added_when_nothing_matches(self):
        h = Harness(self)
        h.seed("They live in Perth")
        h.chat(CHAT)
        self.assertEqual(h.upstream.requests[0]["messages"][0]["content"], "You are Capri.")

    def test_the_tool_choosing_call_is_left_exactly_as_capri_wrote_it(self):
        h = Harness(self)
        h.seed("Their favourite game is Doom")
        h.chat(CHAT, tools=HANDS_TOOLS)
        self.assertEqual(h.upstream.requests[0]["messages"], CHAT)

    def test_screenshot_questions_are_left_alone(self):
        h = Harness(self)
        h.seed("Their favourite game is Doom")
        h.chat([CHAT[0], {"role": "user", "content": "what game is this?", "images": ["QUJD"]}])
        self.assertEqual(h.upstream.requests[0]["messages"][0]["content"], "You are Capri.")

    def test_capris_remember_notes_are_mirrored_once(self):
        h = Harness(self, reply=tool_reply(("remember", {"text": "They main Tracer"})))
        h.chat(CHAT, tools=HANDS_TOOLS)
        h.chat(CHAT, tools=HANDS_TOOLS)
        self.assertEqual(h.facts(), ["They main Tracer"])

    def test_forgetting_follows_the_safety_level(self):
        for level, expected in (("strict", ["They main Tracer"]), ("moderate", [])):
            with self.subTest(level=level):
                h = Harness(self, level=level, reply=tool_reply(("remember", {"text": "main Tracer", "action": "forget"})))
                h.seed("They main Tracer")
                h.chat(CHAT, tools=HANDS_TOOLS)
                self.assertEqual(h.facts(), expected)

    def test_forgetting_only_removes_notes_that_match_every_word(self):
        h = Harness(self, level="relaxed", reply=tool_reply(("remember", {"text": "game Doom", "action": "forget"})))
        h.seed("Their favourite game is Doom", "They also play a game called Celeste")
        h.chat(CHAT, tools=HANDS_TOOLS)
        self.assertEqual(h.facts(), ["They also play a game called Celeste"])

    def test_chat_still_works_when_memory_fails_halfway(self):
        import sqlite3

        h = Harness(self)
        h.seed("Their favourite game is Doom")
        with mock.patch.object(MemoryStore, "recall_memory", side_effect=sqlite3.OperationalError("disk I/O error")), \
                contextlib.redirect_stderr(io.StringIO()):
            status, body = h.chat(CHAT)
        self.assertEqual((status, body["message"]["content"]), (200, "hello from the model"))
        self.assertEqual(h.upstream.requests[0]["messages"][0]["content"], "You are Capri.")

    def test_chat_still_works_when_memory_cannot_be_opened(self):
        with tempfile.NamedTemporaryFile() as blocker:
            h = Harness(self)
            h.brain.memory_path = os.path.join(blocker.name, "memory.db")  # a file where a folder is needed
            with contextlib.redirect_stderr(io.StringIO()):
                status, body = h.chat(CHAT)
        self.assertEqual((status, body["message"]["content"]), (200, "hello from the model"))


class SafetyTests(unittest.TestCase):
    def test_shutting_down_is_blocked_by_default(self):
        h = Harness(self, reply=tool_reply(("power", {"action": "shut_down"})))
        status, body = h.chat([{"role": "user", "content": "shut down"}], tools=HANDS_TOOLS)
        self.assertEqual(status, 200)
        self.assertNotIn("tool_calls", body["message"])
        self.assertIn("safety gate blocked", body["message"]["content"])
        self.assertIn('safety.level is "strict"', body["message"]["content"])
        self.assertIn("relaxed", body["message"]["content"])

    def test_harmless_power_actions_and_everyday_jobs_still_run(self):
        for name, args in (("power", {"action": "lock"}), ("power", {"action": "cancel"}), ("open_app", {"name": "Spotify"}),
                           ("use_computer", {"task": "send hi"}), ("app_window", {"name": "Discord", "action": "close"})):
            with self.subTest(tool=name, args=args):
                h = Harness(self, reply=tool_reply((name, args)))
                _, body = h.chat([{"role": "user", "content": "go"}], tools=HANDS_TOOLS)
                self.assertEqual(body["message"]["tool_calls"][0]["function"]["name"], name)

    def test_a_relaxed_safety_level_allows_shutting_down(self):
        h = Harness(self, level="relaxed", reply=tool_reply(("power", {"action": "restart"})))
        _, body = h.chat([{"role": "user", "content": "restart"}], tools=HANDS_TOOLS)
        self.assertEqual(body["message"]["tool_calls"][0]["function"]["arguments"], {"action": "restart"})

    def test_only_the_blocked_call_is_dropped(self):
        h = Harness(self, reply=tool_reply(("open_app", {"name": "Discord"}), ("power", {"action": "sign_out"})))
        _, body = h.chat([{"role": "user", "content": "go"}], tools=HANDS_TOOLS)
        self.assertEqual([c["function"]["name"] for c in body["message"]["tool_calls"]], ["open_app"])

    def test_capri_is_told_about_the_block_once_so_she_does_not_claim_she_did_it(self):
        now = [100.0]
        h = Harness(self, clock=lambda: now[0], reply=lambda body: (200, tool_reply(("power", {"action": "shut_down"})) if body.get("tools") else TEXT))
        h.chat([{"role": "user", "content": "shut down"}], tools=HANDS_TOOLS)
        h.chat(CHAT)
        self.assertIn("did NOT do it", h.upstream.requests[1]["messages"][0]["content"])
        h.chat(CHAT)
        self.assertNotIn("did NOT do it", h.upstream.requests[2]["messages"][0]["content"])

    def test_the_block_note_expires(self):
        now = [100.0]
        h = Harness(self, clock=lambda: now[0], reply=lambda body: (200, tool_reply(("power", {"action": "shut_down"})) if body.get("tools") else TEXT))
        h.chat([{"role": "user", "content": "shut down"}], tools=HANDS_TOOLS)
        now[0] += 3600
        h.chat(CHAT)
        self.assertNotIn("did NOT do it", h.upstream.requests[1]["messages"][0]["content"])


class ServerGuardTests(unittest.TestCase):
    def raw(self, h, method, path, headers=None, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", h.port, timeout=5)
        connection.putrequest(method, path, skip_host=bool(headers and "Host" in headers))
        for key, value in (headers or {}).items():
            connection.putheader(key, value)
        connection.endheaders(body)
        response = connection.getresponse()
        status = response.status
        connection.close()
        return status

    def test_requests_for_another_host_name_are_refused(self):
        h = Harness(self)
        self.assertEqual(self.raw(h, "GET", "/api/tags", {"Host": "evil.example:11434"}), 403)
        self.assertEqual(self.raw(h, "GET", "/api/tags", {"Host": f"localhost:{h.port}"}), 200)

    def test_web_pages_cannot_post_to_the_brain(self):
        h = Harness(self)
        body = json.dumps({"messages": CHAT}).encode()
        for origin, expected in (("https://evil.example", 403), ("http://localhost:3000", 200), ("null", 403), ("", 403), ("file://", 403)):
            with self.subTest(origin=origin):
                headers = {"Origin": origin, "Content-Length": str(len(body)), "Content-Type": "text/plain"}
                self.assertEqual(self.raw(h, "POST", "/api/chat", headers, body), expected)

    def test_a_refused_post_does_not_poison_the_connection(self):
        h = Harness(self)
        body = json.dumps({"messages": CHAT}).encode()
        connection = http.client.HTTPConnection("127.0.0.1", h.port, timeout=5)
        connection.request("POST", "/api/chat", body, {"Origin": "https://evil.example", "Content-Type": "text/plain"})
        first = connection.getresponse()
        first.read()
        self.assertEqual(first.status, 403)
        self.assertTrue(first.will_close)  # the server closed it instead of misreading the unread body as a new request
        connection.close()

    def test_huge_requests_are_refused_before_reading_them(self):
        h = Harness(self)
        self.assertEqual(self.raw(h, "POST", "/api/chat", {"Content-Length": str(capri_brain.MAX_BODY + 1)}), 413)

    def test_listens_on_loopback_only(self):
        h = Harness(self)
        self.assertEqual(h.server.server_address[0], "127.0.0.1")


class StartupTests(unittest.TestCase):
    def test_brain_pointing_at_its_own_port_is_detected(self):
        self.assertTrue(points_at_self("http://localhost:11434/v1", 11434))  # BabyAI's default config
        self.assertTrue(points_at_self("http://127.0.0.1:11434/v1", 11434))
        self.assertFalse(points_at_self("http://127.0.0.1:11435/v1", 11434))
        self.assertFalse(points_at_self("https://api.example.com/v1", 11434))
        self.assertFalse(points_at_self("http://gpu-box:11434/v1", 11434))

    def config(self, tmp, base_url):
        path = os.path.join(tmp, "config.toml")
        with open(path, "w") as handle:
            handle.write(f'[llm]\nbase_url = "{base_url}"\nmodel = "m"\n[memory]\ndb_path = "{tmp}/memory.db"\n')
        return path

    def test_default_config_explains_how_to_move_ollama(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as out:
            code = capri_brain.serve_capri(os.path.join(tmp, "missing.toml"))
        self.assertEqual(code, 2)
        self.assertIn("OLLAMA_HOST=127.0.0.1:11435", out.getvalue())
        self.assertIn("base_url", out.getvalue())

    def test_a_busy_port_is_reported_not_crashed_on(self):
        with tempfile.TemporaryDirectory() as tmp, socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen()
            config = self.config(tmp, "http://127.0.0.1:9/v1")
            with contextlib.redirect_stdout(io.StringIO()) as out:
                code = capri_brain.serve_capri(config, port=busy.getsockname()[1])
        self.assertEqual(code, 2)
        self.assertIn("Could not listen", out.getvalue())

    @unittest.skipIf(os.name == "nt", "uses a POSIX stand-in for the Capri app")
    def test_with_launch_the_brain_runs_until_capri_closes(self):
        with tempfile.TemporaryDirectory() as tmp, socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
            probe.close()
            config = self.config(tmp, "http://127.0.0.1:9/v1")
            fake = LaunchResult(True, "Started Capri", subprocess.Popen(["sleep", "1.2"]))
            seen = {}

            def watch():  # the brain must be answering while "Capri" is open
                for _ in range(40):
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/tags", timeout=1) as response:
                            seen["tags"] = json.load(response)
                            return
                    except OSError:
                        threading.Event().wait(0.05)

            watcher = threading.Thread(target=watch)
            watcher.start()
            with mock.patch("babyai.capri_launcher.launch_capri", return_value=fake), contextlib.redirect_stdout(io.StringIO()):
                code = capri_brain.serve_capri(config, port=port, launch=True)
            watcher.join()
            with self.assertRaises(OSError):  # and it is shut down afterwards
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/tags", timeout=1)
        self.assertEqual(code, 0)
        self.assertEqual(seen["tags"]["models"][0]["name"], "babyai:latest")


if __name__ == "__main__":
    unittest.main()
