import shutil
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

TEST_PROFILE = Path(__file__).parent / "data" / "test_device.properties"


@pytest.fixture(autouse=True)
def playstore_home(tmp_path_factory, monkeypatch):
    """Every test gets its own PLAYSTORE_HOME, with the fictional test_device profile in profiles/.

    It is kept out of tmp_path so tests that inspect tmp_path see only their own files.
    """
    home = tmp_path_factory.mktemp("playstore-home")
    (home / "profiles").mkdir()
    shutil.copy(TEST_PROFILE, home / "profiles")
    monkeypatch.setenv("PLAYSTORE_HOME", str(home))
    return home


class FakeServer:
    """Local HTTP stand-in. respond() sets per-path responses; received requests pile up in requests.

    Calling respond() several times for one path answers in that order and keeps repeating the last one.
    """

    def __init__(self):
        self.routes, self.requests = {}, []
        self._srv = HTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self._srv.server_port}"
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    def respond(self, body: bytes | str, status: int = 200, path: str | None = None):
        """Without path, this answers every request not matched by another path."""
        self.routes.setdefault(path, []).append(
            (status, body.encode() if isinstance(body, str) else body))

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
                queue = server.routes.get(url.path) or server.routes.get(None) or [(404, b"")]
                status, out = queue.pop(0) if len(queue) > 1 else queue[0]
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
