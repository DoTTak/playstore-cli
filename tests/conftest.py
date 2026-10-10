import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit

import pytest


class FakeServer:
    """로컬 HTTP 대역. respond() 로 경로별 응답을 정하고, 받은 요청은 requests 에 쌓인다."""

    def __init__(self):
        self.routes, self.requests = {}, []
        self._srv = HTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self._srv.server_port}"
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    def respond(self, body: bytes | str, status: int = 200, path: str | None = None):
        """path 를 생략하면 다른 경로에 걸리지 않은 모든 요청의 응답이 된다."""
        self.routes[path] = (status, body.encode() if isinstance(body, str) else body)

    def by_path(self, path: str) -> list[dict]:
        return [r for r in self.requests if r["path"] == path]

    def _handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self):
                url = urlsplit(self.path)
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                server.requests.append({"method": self.command, "path": url.path, "query": url.query,
                                        "headers": self.headers, "body": body})
                status, out = server.routes.get(url.path) or server.routes.get(None, (404, b""))
                self.send_response(status)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            do_GET = do_POST = _handle

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
