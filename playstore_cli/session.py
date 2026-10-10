"""Play session setup: lets the device registered by checkin use the Play API.

Same steps, in the same order, as what follows checkin in Aurora gplayapi AuthHelper.build:
  1. AAS → Play token (service=oauth2:.../googleplay, androidId=gsfId)
  2. uploadDeviceConfig → deviceConfigToken (used to pick apps and versions that fit the device)
  3. toc → check the terms of service, acceptTos if not yet accepted. The response cookie is the dfeCookie
     (reportedly, sessions that have not accepted the ToS get delivery responses without a download URL
     — per an alltechdev comment)
The Play token expires, so this can be redone at any time from the saved AAS token.
"""
from __future__ import annotations

from . import auth
from .api import PlayApi

PLAY_SERVICE = "oauth2:https://www.googleapis.com/auth/googleplay"


def _no_log(msg: str) -> None:
    pass


def build(account: dict, device: dict, log=_no_log) -> dict:
    """Return {gsfId, authToken, deviceConfigToken, dfeCookie}."""
    token = auth.exchange_aas_token(account["email"], account["aasToken"], PLAY_SERVICE,
                                    gsf_id=device["gsfId"], app="com.android.vending",
                                    header_app="com.google.android.gms")
    log("Play token OK")
    session = {"gsfId": device["gsfId"], "authToken": token}
    api = PlayApi(device, session)

    session["deviceConfigToken"] = api.upload_device_config()
    log(f"device config upload OK (deviceConfigToken {'present' if session['deviceConfigToken'] else 'missing'})")

    toc = api.toc()
    if toc.tosContent and toc.tosToken:
        api.accept_tos(toc.tosToken)
        log("terms of service accepted")
    session["dfeCookie"] = toc.cookie
    log(f"toc OK (dfeCookie {'present' if toc.cookie else 'missing'})")
    return session
