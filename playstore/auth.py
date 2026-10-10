"""Client for the Google account auth endpoint (android.clients.google.com/auth).

Login step 2: one-time oauth_token from the browser (oauth2_4/...) → long-lived AAS token (aas_et/...).
The request parameters match Aurora Store's personal account login (AC2DMTask).
Later steps (AAS → ac2dm / Play token) use the same endpoint, so they live here too.
"""
from __future__ import annotations

import ssl

import requests
from requests.adapters import HTTPAdapter

AUTH_URL = "https://android.clients.google.com/auth"

# Signing certificate SHA-1 of com.android.vending. A public constant shared by Google auth clients.
CALLER_SIG = "38918a453d07199354f8b19af05ec6562ced5788"
PLAY_SERVICES_VERSION = 19629032


class AuthError(Exception):
    """Google rejected the auth request (Error=BadAuthentication, etc.)."""


class _NoAlpnContext(ssl.SSLContext):
    def set_alpn_protocols(self, protocols):
        # gpsoauth: /auth returns 403 BadAuthentication if ALPN is sent.
        pass


class _AuthAdapter(HTTPAdapter):
    """TLS settings for /auth only (same tweaks as gpsoauth's AuthHTTPAdapter)."""

    def init_poolmanager(self, *args, **kwargs):
        ctx = _NoAlpnContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.load_verify_locations(requests.certs.where())
        # OP_NO_TICKET in urllib3's default context also causes 403, so leave it cleared
        ctx.options &= ~ssl.OP_NO_TICKET
        kwargs["ssl_context"] = ctx
        super().init_poolmanager(*args, **kwargs)


def parse_response(text: str) -> dict[str, str]:
    """Line-based 'Key=Value' response → dict. Split by line because values may contain spaces or '='."""
    out = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            out[key] = value
    return out


def _post(params: dict, headers: dict) -> dict[str, str]:
    with requests.Session() as s:
        s.mount("https://", _AuthAdapter())
        resp = s.post(AUTH_URL, data=params, timeout=30,
                      headers={"Accept-Encoding": "identity", **headers})
    data = parse_response(resp.text)
    if resp.status_code != 200 or "Error" in data:
        detail = data.get("Error") or resp.text[:200]
        for k in ("ErrorDetail", "Info", "Url"):  # NeedsBrowser comes with a Url
            if k in data:
                detail += f" {k}={data[k]}"
        raise AuthError(f"HTTP {resp.status_code}: {detail}")
    return data


def exchange_oauth_token(email: str, oauth_token: str) -> str:
    """oauth_token → AAS token. The oauth_token is single-use and expires within minutes."""
    data = _post(
        {
            "lang": "en-US",
            "google_play_services_version": PLAY_SERVICES_VERSION,
            "sdk_version": 28,
            "device_country": "us",
            "Email": email,
            "service": "ac2dm",
            "get_accountid": 1,
            "ACCESS_TOKEN": 1,
            "callerPkg": "com.google.android.gms",
            "add_account": 1,
            "Token": oauth_token,
            "callerSig": CALLER_SIG,
            "droidguard_results": "null",
        },
        {"app": "com.google.android.gms", "User-Agent": ""},
    )
    token = data.get("Token")
    if not token:
        raise AuthError(f"응답에 Token 이 없음 (keys={sorted(data)})")
    return token


def exchange_aas_token(email: str, aas_token: str, service: str, *, gsf_id: str | None = None,
                       app: str | None = None, header_app: str = "com.android.vending") -> str:
    """AAS token → per-service token (same request as Aurora GooglePlayApi.generateToken).

    service="ac2dm"  : token that links the account to the device during checkin
    service="oauth2:https://www.googleapis.com/auth/googleplay" : Play API Bearer token
    """
    params = {
        "sdk_version": 28,
        "Email": email,
        "google_play_services_version": PLAY_SERVICES_VERSION,
        "device_country": "us",
        "lang": "en",
        "callerSig": CALLER_SIG,
        "client_sig": CALLER_SIG,
        "callerPkg": "com.google.android.gms",
        "Token": aas_token,
        "oauth2_foreground": "1",
        "token_request_options": "CAA4AVAB",
        "check_email": "1",
        "system_partition": "1",
        "service": service,
    }
    if gsf_id:
        params["androidId"] = gsf_id
    if app:
        params["app"] = app
    data = _post(params, {"app": header_app, "User-Agent": "GoogleAuth/1.4"})
    token = data.get("Auth") or data.get("Token")
    if not token:
        raise AuthError(f"{service} 토큰이 응답에 없음 (keys={sorted(data)})")
    return token
