"""A tiny stand-in for Ollama's OpenAI-compatible API, behaving the way Ollama documents it:
GET /v1/models lists models; POST /v1/chat/completions answers 404 for a model it doesn't have
('model "x" not found, try pulling it first') and 400 for a model that can't use tools
('... does not support tools'). Not Ollama itself - there is none in the test environment."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeOllama:
    def __init__(self, models=("llama3.1:8b",), no_tools=(), delay=0.0, script=None):
        self.models, self.no_tools, self.delay = list(models), set(no_tools), delay
        self.script = list(script or [])  # chat responses to give, in order; then a default "pong"
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/v1/models":
                    self._send(200, {"object": "list", "data": [{"id": m, "object": "model"} for m in outer.models]})
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                outer.requests.append(body)
                time.sleep(outer.delay)
                model = body.get("model")
                if model not in outer.models:
                    return self._send(404, {"error": {"message": f'model "{model}" not found, try pulling it first', "type": "api_error", "param": None, "code": None}})
                if body.get("tools") and model in outer.no_tools:
                    return self._send(400, {"error": {"message": f"registry.ollama.ai/library/{model} does not support tools", "type": "api_error", "param": None, "code": None}})
                reply = outer.script.pop(0) if outer.script else {"role": "assistant", "content": "pong"}
                self._send(200, {"choices": [{"index": 0, "message": reply, "finish_reason": "stop"}]})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
