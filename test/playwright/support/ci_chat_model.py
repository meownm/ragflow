"""Small OpenAI-compatible chat endpoint for isolated CI browser journeys."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


MODELS = {"ci-chat-primary", "ci-chat-secondary"}
ANSWER = "The test document is available in the dataset."


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health":
            self.send_error(404)
            return
        self._write_json({"status": "ok"})

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self.send_error(400)
            return
        model = request.get("model")
        if model not in MODELS or not request.get("messages"):
            self.send_error(400, "Unknown model or empty messages")
            return

        if not request.get("stream"):
            self._write_json(
                {
                    "id": "chatcmpl-ci",
                    "object": "chat.completion",
                    "created": 0,
                    "model": model,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": ANSWER}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 8, "total_tokens": 9},
                }
            )
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for delta, finish_reason in [({"role": "assistant", "content": ANSWER}, None), ({}, "stop")]:
            chunk = {
                "id": "chatcmpl-ci",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
            }
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _write_json(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
