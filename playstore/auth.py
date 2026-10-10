"""구글 계정 인증 엔드포인트(android.clients.google.com/auth) 클라이언트.

로그인 2단계: 브라우저에서 얻은 1회용 oauth_token(oauth2_4/...) → 장기 AAS 토큰(aas_et/...).
요청 파라미터는 Aurora Store 개인 계정 로그인(AC2DMTask)과 같다.
이후 단계(AAS → ac2dm / Play 토큰)도 같은 엔드포인트를 쓰므로 여기에 모은다.
"""
from __future__ import annotations

import ssl

import requests
from requests.adapters import HTTPAdapter

AUTH_URL = "https://android.clients.google.com/auth"

# com.android.vending 서명 SHA-1. 구글 인증 클라이언트들이 공통으로 쓰는 공개 상수.
CALLER_SIG = "38918a453d07199354f8b19af05ec6562ced5788"
PLAY_SERVICES_VERSION = 19629032


class AuthError(Exception):
    """구글이 인증 요청을 거절함 (Error=BadAuthentication 등)."""


class _NoAlpnContext(ssl.SSLContext):
    def set_alpn_protocols(self, protocols):
        # gpsoauth: ALPN 을 보내면 /auth 가 403 BadAuthentication 을 준다.
        pass


class _AuthAdapter(HTTPAdapter):
    """/auth 전용 TLS 설정 (gpsoauth AuthHTTPAdapter 와 같은 조정)."""

    def init_poolmanager(self, *args, **kwargs):
        ctx = _NoAlpnContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.load_verify_locations(requests.certs.where())
        # urllib3 기본 컨텍스트의 OP_NO_TICKET 도 403 의 원인 → 끈 상태로 둔다
        ctx.options &= ~ssl.OP_NO_TICKET
        kwargs["ssl_context"] = ctx
        super().init_poolmanager(*args, **kwargs)


def parse_response(text: str) -> dict[str, str]:
    """'Key=Value' 줄 단위 응답 → dict. 값에 공백이나 '=' 가 들어갈 수 있어 줄로 나눈다."""
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
        for k in ("ErrorDetail", "Info", "Url"):  # NeedsBrowser 면 Url 이 온다
            if k in data:
                detail += f" {k}={data[k]}"
        raise AuthError(f"HTTP {resp.status_code}: {detail}")
    return data


def exchange_oauth_token(email: str, oauth_token: str) -> str:
    """oauth_token → AAS 토큰. oauth_token 은 1회용이고 몇 분 안에 만료된다."""
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
