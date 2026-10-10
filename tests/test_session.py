from urllib.parse import parse_qs

import pytest

from playstore import api, auth, session
from playstore import googleplay_pb2 as pb
from playstore.api import PlayApi, PlayApiError

ACCOUNT = {"email": "a@b.com", "aasToken": "aas_et/XYZ"}
DEVICE = {"profile": "pixel_9a", "locale": "ko_KR", "timezone": "Asia/Seoul",
          "gsfId": "3a1b2c3d4e5f6071", "deviceCheckinConsistencyToken": "consistency"}
SESSION = {"gsfId": DEVICE["gsfId"], "authToken": "play-token",
           "deviceConfigToken": "cfg-token", "dfeCookie": "dfe-cookie"}


@pytest.fixture
def play_server(fake_server, monkeypatch):
    monkeypatch.setattr(auth, "AUTH_URL", fake_server.url + "/auth")
    monkeypatch.setattr(api, "FDFE_URL", fake_server.url + "/fdfe/")
    return fake_server


def wrap(**payload) -> bytes:
    return pb.ResponseWrapper(payload=pb.Payload(**payload)).SerializeToString()


def form(req) -> dict:
    return parse_qs(req["body"].decode())


def serve_session(srv, toc: pb.TocResponse):
    srv.respond("Auth=play-token\n", path="/auth")
    srv.respond(wrap(uploadDeviceConfigResponse=pb.UploadDeviceConfigResponse(
        uploadDeviceConfigToken="cfg-token")), path="/fdfe/uploadDeviceConfig")
    srv.respond(wrap(tocResponse=toc), path="/fdfe/toc")
    srv.respond(wrap(acceptTosResponse=pb.AcceptTosResponse()), path="/fdfe/acceptTos")


def test_build_session_accepts_tos(play_server):
    serve_session(play_server, pb.TocResponse(tosContent="약관", tosToken="tost", cookie="dfe-cookie"))

    assert session.build(ACCOUNT, DEVICE) == SESSION

    (token_req,) = play_server.by_path("/auth")
    f = form(token_req)
    assert (f["service"], f["androidId"], f["app"]) == (
        [session.PLAY_SERVICE], [DEVICE["gsfId"]], ["com.android.vending"])

    (upload,) = play_server.by_path("/fdfe/uploadDeviceConfig")
    assert upload["headers"]["Authorization"] == "Bearer play-token"
    assert upload["headers"]["X-DFE-Device-Id"] == DEVICE["gsfId"]
    assert upload["headers"]["X-DFE-Device-Checkin-Consistency-Token"] == "consistency"
    config = pb.UploadDeviceConfigRequest.FromString(upload["body"]).deviceConfiguration
    assert list(config.nativePlatform) == ["arm64-v8a"]

    (toc,) = play_server.by_path("/fdfe/toc")
    assert toc["headers"]["X-DFE-Device-Config-Token"] == "cfg-token"
    (accept,) = play_server.by_path("/fdfe/acceptTos")
    assert form(accept) == {"tost": ["tost"], "toscme": ["false"]}


def test_build_session_without_tos_skips_accept(play_server):
    serve_session(play_server, pb.TocResponse(cookie="dfe-cookie"))

    assert session.build(ACCOUNT, DEVICE)["dfeCookie"] == "dfe-cookie"
    assert not play_server.by_path("/fdfe/acceptTos")


def test_details(play_server):
    doc = pb.DocV2(docid="com.google.android.youtube", title="YouTube", details=pb.DocumentDetails(
        appDetails=pb.AppDetails(versionCode=1561301413, versionString="21.40.161")))
    play_server.respond(wrap(detailsResponse=pb.DetailsResponse(docV2=doc)), path="/fdfe/details")

    assert PlayApi(DEVICE, SESSION).details("com.google.android.youtube") == doc

    (req,) = play_server.requests
    assert (req["method"], req["query"]) == ("GET", "doc=com.google.android.youtube")
    assert req["headers"]["X-DFE-Cookie"] == "dfe-cookie"
    assert req["headers"]["Accept-Language"] == "ko-KR"
    assert req["headers"]["User-Agent"].startswith("Android-Finsky/")


def test_details_not_found(play_server):
    play_server.respond(pb.ResponseWrapper(commands=pb.ServerCommands(
        displayErrorMessage="Item not found.")).SerializeToString(), status=404, path="/fdfe/details")

    with pytest.raises(PlayApiError, match="HTTP 404: Item not found.") as e:
        PlayApi(DEVICE, SESSION).details("com.example.nope")
    assert e.value.status == 404


def test_details_unauthorized(play_server):
    play_server.respond("<html>Unauthorized</html>", status=401, path="/fdfe/details")
    with pytest.raises(PlayApiError) as e:
        PlayApi(DEVICE, SESSION).details("com.google.android.youtube")
    assert e.value.status == 401
