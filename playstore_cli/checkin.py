"""Virtual device registration (checkin): Google issues the device an androidId (= gsfId).

Every Play API request carries this gsfId as X-DFE-Device-Id.
Like gpapi GooglePlayAPI.checkin, it is called twice:
  1st: check in with id=0 (new device) → androidId and securityToken are issued
  2nd: check in again with the issued id, linking the account to the device via accountCookie
"""
from __future__ import annotations

import requests

from . import googleplay_pb2 as pb
from .device import Profile

CHECKIN_URL = "https://android.clients.google.com/checkin"


class CheckinError(Exception):
    pass


def _post(req: pb.AndroidCheckinRequest, headers: dict) -> pb.AndroidCheckinResponse:
    resp = requests.post(CHECKIN_URL, data=req.SerializeToString(), headers=headers, timeout=30)
    if resp.status_code != 200:
        raise CheckinError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    return pb.AndroidCheckinResponse.FromString(resp.content)


def checkin(profile: Profile, email: str, ac2dm_token: str, *,
            locale: str, timezone: str) -> dict:
    """Register the device and return {gsfId, securityToken, deviceCheckinConsistencyToken}."""
    headers = {"User-Agent": profile.user_agent(), "Content-Type": "application/x-protobuffer"}
    req = profile.checkin_request(locale, timezone)

    first = _post(req, headers)
    if not first.androidId:
        raise CheckinError("no androidId in the response")

    req.id = first.androidId
    req.securityToken = first.securityToken
    req.accountCookie.extend([f"[{email}]", ac2dm_token])
    _post(req, headers)

    return {
        "gsfId": format(first.androidId, "x"),
        "securityToken": first.securityToken,
        "deviceCheckinConsistencyToken": first.deviceCheckinConsistencyToken,
    }
