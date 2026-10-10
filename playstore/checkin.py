"""가상 기기 등록(checkin) — 구글이 기기에 androidId(= gsfId)를 발급한다.

Play API 요청은 모두 이 gsfId 를 X-DFE-Device-Id 로 싣는다.
gpapi GooglePlayAPI.checkin 처럼 두 번 호출한다:
  1차: id=0(새 기기)으로 체크인 → androidId, securityToken 발급
  2차: 발급받은 id 로 다시 체크인하면서 accountCookie 로 계정을 기기에 연결
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
    """기기를 등록하고 {gsfId, securityToken, deviceCheckinConsistencyToken} 을 반환."""
    headers = {"User-Agent": profile.user_agent(), "Content-Type": "application/x-protobuffer"}
    req = profile.checkin_request(locale, timezone)

    first = _post(req, headers)
    if not first.androidId:
        raise CheckinError("응답에 androidId 가 없음")

    req.id = first.androidId
    req.securityToken = first.securityToken
    req.accountCookie.extend([f"[{email}]", ac2dm_token])
    _post(req, headers)

    return {
        "gsfId": format(first.androidId, "x"),
        "securityToken": first.securityToken,
        "deviceCheckinConsistencyToken": first.deviceCheckinConsistencyToken,
    }
