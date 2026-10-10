import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


class FakeServer:
    """로컬 HTTP 대역. respond() 로 응답을 정하고, 받은 요청은 requests 에 쌓인다."""

    def __init__(self):
        self.status, self.body, self.requests = 200, b"", []
        self._srv = HTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self._srv.server_port}"
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    def respond(self, body: bytes | str, status: int = 200):
        self.body = body.encode() if isinstance(body, str) else body
        self.status = status

    def _handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                server.requests.append({"path": self.path, "headers": self.headers, "body": body})
                self.send_response(server.status)
                self.send_header("Content-Length", str(len(server.body)))
                self.end_headers()
                self.wfile.write(server.body)

            def log_message(self, *args):
                pass

        return Handler

    def close(self):
        self._srv.shutdown()


@pytest.fixture
def fake_server(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    srv = FakeServer()
    yield srv
    srv.close()
