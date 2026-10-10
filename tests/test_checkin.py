import pytest

from playstore import checkin
from playstore import googleplay_pb2 as pb
from playstore.device import Profile


@pytest.fixture
def checkin_server(fake_server, monkeypatch):
    monkeypatch.setattr(checkin, "CHECKIN_URL", fake_server.url + "/checkin")
    return fake_server


def test_checkin_registers_then_binds_account(checkin_server):
    checkin_server.respond(pb.AndroidCheckinResponse(
        androidId=0x3A1B2C3D4E5F6071, securityToken=0x1122334455667788,
        deviceCheckinConsistencyToken="consistency").SerializeToString())

    result = checkin.checkin(Profile.load(), "a@b.com", "ac2dm-token",
                             locale="ko_KR", timezone="Asia/Seoul")

    assert result == {"gsfId": "3a1b2c3d4e5f6071", "securityToken": 0x1122334455667788,
                      "deviceCheckinConsistencyToken": "consistency"}
    first, second = (pb.AndroidCheckinRequest.FromString(r["body"]) for r in checkin_server.requests)
    assert checkin_server.requests[0]["headers"]["Content-Type"] == "application/x-protobuffer"
    assert first.id == 0 and not first.accountCookie
    assert (second.id, second.securityToken) == (0x3A1B2C3D4E5F6071, 0x1122334455667788)
    assert list(second.accountCookie) == ["[a@b.com]", "ac2dm-token"]


def test_checkin_without_android_id(checkin_server):
    checkin_server.respond(pb.AndroidCheckinResponse(statsOk=True).SerializeToString())
    with pytest.raises(checkin.CheckinError, match="androidId"):
        checkin.checkin(Profile.load(), "a@b.com", "t", locale="ko_KR", timezone="Asia/Seoul")


def test_checkin_http_error(checkin_server):
    checkin_server.respond("bad request", status=400)
    with pytest.raises(checkin.CheckinError, match="HTTP 400"):
        checkin.checkin(Profile.load(), "a@b.com", "t", locale="ko_KR", timezone="Asia/Seoul")
