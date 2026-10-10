import ssl
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

import pytest

from playstore import auth


@pytest.fixture
def auth_server(monkeypatch):
    """/auth 대역. 받은 요청을 기록하고, state 에 넣어 둔 응답을 돌려준다."""
    state = {"status": 200, "body": "", "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"])).decode()
            form = {k: v[0] for k, v in parse_qs(body, keep_blank_values=True).items()}
            state["requests"].append({"headers": self.headers, "form": form})
            out = state["body"].encode()
            self.send_response(state["status"])
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *args):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setattr(auth, "AUTH_URL", f"http://127.0.0.1:{srv.server_port}/auth")
    yield state
    srv.shutdown()


def test_parse_response_keeps_spaces_and_equals():
    text = "Token=aas_et/AbC==\nfirstName=Gil Dong\nEmail=a@b.com\n"
    assert auth.parse_response(text) == {
        "Token": "aas_et/AbC==", "firstName": "Gil Dong", "Email": "a@b.com"}


def test_exchange_oauth_token_sends_aurora_params(auth_server):
    auth_server["body"] = "Token=aas_et/XYZ\nEmail=burner@example.com\n"

    assert auth.exchange_oauth_token("burner@example.com", "oauth2_4/abc") == "aas_et/XYZ"

    req = auth_server["requests"][0]
    assert req["headers"]["app"] == "com.google.android.gms"
    assert req["form"] == {
        "lang": "en-US",
        "google_play_services_version": "19629032",
        "sdk_version": "28",
        "device_country": "us",
        "Email": "burner@example.com",
        "service": "ac2dm",
        "get_accountid": "1",
        "ACCESS_TOKEN": "1",
        "callerPkg": "com.google.android.gms",
        "add_account": "1",
        "Token": "oauth2_4/abc",
        "callerSig": auth.CALLER_SIG,
        "droidguard_results": "null",
    }


def test_exchange_rejected(auth_server):
    auth_server.update(status=403, body="Error=BadAuthentication\n")
    with pytest.raises(auth.AuthError, match="HTTP 403: BadAuthentication"):
        auth.exchange_oauth_token("a@b.com", "oauth2_4/used")


def test_exchange_needs_browser_reports_url(auth_server):
    auth_server.update(status=403, body="Error=NeedsBrowser\nUrl=https://accounts.google.com/x\n")
    with pytest.raises(auth.AuthError, match="Url=https://accounts.google.com/x"):
        auth.exchange_oauth_token("a@b.com", "oauth2_4/abc")


def test_exchange_without_token(auth_server):
    auth_server["body"] = "Email=a@b.com\n"
    with pytest.raises(auth.AuthError, match="Token"):
        auth.exchange_oauth_token("a@b.com", "oauth2_4/abc")


def test_auth_adapter_tls_context():
    ctx = auth._AuthAdapter().poolmanager.connection_pool_kw["ssl_context"]
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert not ctx.options & ssl.OP_NO_TICKET
    ctx.set_alpn_protocols(["http/1.1"])  # 무시돼야 한다
