"""Play 세션 만들기 — checkin 으로 등록한 기기로 Play API 를 쓸 수 있게 한다.

Aurora gplayapi AuthHelper.build 의 checkin 이후 단계와 같은 순서:
  1. AAS → Play 토큰 (service=oauth2:.../googleplay, androidId=gsfId)
  2. uploadDeviceConfig → deviceConfigToken (기기에 맞는 앱·버전 판정에 쓰임)
  3. toc → 약관 확인, 미동의면 acceptTos. 응답의 cookie 가 dfeCookie
     (약관 미동의 세션은 delivery 응답에 다운로드 URL 이 빠진다고 함 — alltechdev 주석)
Play 토큰은 만료되므로 이 과정은 저장된 AAS 로 언제든 다시 할 수 있다.
"""
from __future__ import annotations

from . import auth
from .api import PlayApi

PLAY_SERVICE = "oauth2:https://www.googleapis.com/auth/googleplay"


def _no_log(msg: str) -> None:
    pass


def build(account: dict, device: dict, log=_no_log) -> dict:
    """{gsfId, authToken, deviceConfigToken, dfeCookie} 를 반환."""
    token = auth.exchange_aas_token(account["email"], account["aasToken"], PLAY_SERVICE,
                                    gsf_id=device["gsfId"], app="com.android.vending",
                                    header_app="com.google.android.gms")
    log("Play 토큰 OK")
    session = {"gsfId": device["gsfId"], "authToken": token}
    api = PlayApi(device, session)

    session["deviceConfigToken"] = api.upload_device_config()
    log(f"기기 구성 업로드 OK (deviceConfigToken {'있음' if session['deviceConfigToken'] else '없음'})")

    toc = api.toc()
    if toc.tosContent and toc.tosToken:
        api.accept_tos(toc.tosToken)
        log("약관 동의 OK")
    session["dfeCookie"] = toc.cookie
    log(f"toc OK (dfeCookie {'있음' if toc.cookie else '없음'})")
    return session
