"""Deterministic HTTP stand-in; nginx and deployment operations remain real."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import time


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        if self.path == "/api/v1/system/healthz":
            self.reply(503 if os.getenv("FIXTURE_UNREADY") else 200, json.dumps({"status": "ok"}).encode())
        elif self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            for value in ("start", "finish"):
                if value == "finish":
                    time.sleep(15)
                self.wfile.write(f"data: {os.environ['FIXTURE_VERSION']}:{value}\n\n".encode())
                self.wfile.flush()
            self.close_connection = True
        elif self.path.startswith("/request-target/"):
            self.reply(200, self.path.encode())
        else:
            self.reply(200, os.environ["FIXTURE_VERSION"].encode())

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        self.reply(200, self.rfile.read(size))

    def reply(self, status, content):
        self.send_response(status)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *_args):
        pass


ThreadingHTTPServer(("0.0.0.0", 80), Handler).serve_forever()
