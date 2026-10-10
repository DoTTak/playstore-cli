import ssl
from urllib.parse import parse_qs

import pytest

from playstore_cli import auth


@pytest.fixture
def auth_server(fake_server, monkeypatch):
    monkeypatch.setattr(auth, "AUTH_URL", fake_server.url + "/auth")
    return fake_server


def form(req) -> dict:
    return {k: v[0] for k, v in parse_qs(req["body"].decode(), keep_blank_values=True).items()}


def test_parse_response_keeps_spaces_and_equals():
    text = "Token=aas_et/AbC==\nfirstName=Gil Dong\nEmail=a@b.com\n"
    assert auth.parse_response(text) == {
        "Token": "aas_et/AbC==", "firstName": "Gil Dong", "Email": "a@b.com"}


def test_exchange_oauth_token_sends_aurora_params(auth_server):
    auth_server.respond("Token=aas_et/XYZ\nEmail=burner@example.com\n")

    assert auth.exchange_oauth_token("burner@example.com", "oauth2_4/abc") == "aas_et/XYZ"

    req = auth_server.requests[0]
    assert req["headers"]["app"] == "com.google.android.gms"
    assert form(req) == {
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
    auth_server.respond("Error=BadAuthentication\n", status=403)
    with pytest.raises(auth.AuthError, match="HTTP 403: BadAuthentication"):
        auth.exchange_oauth_token("a@b.com", "oauth2_4/used")


def test_exchange_needs_browser_reports_url(auth_server):
    auth_server.respond("Error=NeedsBrowser\nUrl=https://accounts.google.com/x\n", status=403)
    with pytest.raises(auth.AuthError, match="Url=https://accounts.google.com/x"):
        auth.exchange_oauth_token("a@b.com", "oauth2_4/abc")


def test_exchange_without_token(auth_server):
    auth_server.respond("Email=a@b.com\n")
    with pytest.raises(auth.AuthError, match="Token"):
        auth.exchange_oauth_token("a@b.com", "oauth2_4/abc")


def test_exchange_aas_for_ac2dm(auth_server):
    auth_server.respond("SID=x\nAuth=ac2dm-token\n")

    assert auth.exchange_aas_token("a@b.com", "aas_et/XYZ", "ac2dm") == "ac2dm-token"

    req = auth_server.requests[0]
    f = form(req)
    assert (req["headers"]["app"], req["headers"]["User-Agent"]) == ("com.android.vending", "GoogleAuth/1.4")
    assert (f["service"], f["Token"], f["token_request_options"]) == ("ac2dm", "aas_et/XYZ", "CAA4AVAB")
    assert "androidId" not in f and "app" not in f


def test_exchange_aas_for_play_token(auth_server):
    auth_server.respond("Auth=ya29.play\n")
    service = "oauth2:https://www.googleapis.com/auth/googleplay"

    token = auth.exchange_aas_token("a@b.com", "aas_et/XYZ", service, gsf_id="3a1b2c",
                                    app="com.android.vending", header_app="com.google.android.gms")

    f = form(auth_server.requests[0])
    assert token == "ya29.play"
    assert (f["service"], f["androidId"], f["app"]) == (service, "3a1b2c", "com.android.vending")


def test_auth_adapter_tls_context():
    ctx = auth._AuthAdapter().poolmanager.connection_pool_kw["ssl_context"]
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert not ctx.options & ssl.OP_NO_TICKET
    ctx.set_alpn_protocols(["http/1.1"])  # must be ignored
