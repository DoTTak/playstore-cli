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


def serve_details(srv, *, version_code=1561301413, offer=None):
    doc = pb.DocV2(docid="com.google.android.youtube", title="YouTube",
                   offer=[offer or pb.Offer(micros=0, offerType=1)],
                   details=pb.DocumentDetails(appDetails=pb.AppDetails(versionCode=version_code)))
    srv.respond(wrap(detailsResponse=pb.DetailsResponse(docV2=doc)), path="/fdfe/details")


def delivery(url: str = "") -> bytes:
    return wrap(deliveryResponse=pb.DeliveryResponse(appDeliveryData=pb.AndroidAppDeliveryData(
        downloadUrl=url, downloadAuthCookie=[pb.HttpCookie(name="MarketDA", value="c")])))


def test_app_delivery_owned_app_skips_purchase(play_server):
    serve_details(play_server)
    play_server.respond(delivery("https://play.example/base.apk"), path="/fdfe/delivery")

    doc, data = PlayApi(DEVICE, SESSION).app_delivery("com.google.android.youtube")

    assert data.downloadUrl == "https://play.example/base.apk"
    assert not play_server.by_path("/fdfe/purchase")
    (req,) = play_server.by_path("/fdfe/delivery")
    assert parse_qs(req["query"]) == {"doc": ["com.google.android.youtube"], "ot": ["1"], "vc": ["1561301413"]}


def test_app_delivery_purchases_then_retries(play_server):
    serve_details(play_server)
    play_server.respond(delivery(""), path="/fdfe/delivery")  # 아직 보관함에 없음
    play_server.respond(delivery("https://play.example/base.apk"), path="/fdfe/delivery")
    play_server.respond(wrap(buyResponse=pb.BuyResponse(downloadToken="dtok-1")), path="/fdfe/purchase")

    _, data = PlayApi(DEVICE, SESSION).app_delivery("com.google.android.youtube")

    assert data.downloadUrl == "https://play.example/base.apk"
    (buy,) = play_server.by_path("/fdfe/purchase")
    assert form(buy) == {"doc": ["com.google.android.youtube"], "ot": ["1"], "vc": ["1561301413"]}
    assert parse_qs(play_server.by_path("/fdfe/delivery")[1]["query"])["dtok"] == ["dtok-1"]


def test_app_delivery_paid_app(play_server):
    serve_details(play_server, offer=pb.Offer(micros=1_100_000_000, formattedAmount="₩1,100", offerType=1))
    with pytest.raises(api.NotDownloadable, match="₩1,100"):
        PlayApi(DEVICE, SESSION).app_delivery("com.google.android.youtube")
    assert not play_server.by_path("/fdfe/purchase")


def test_app_delivery_incompatible(play_server):
    serve_details(play_server, version_code=0)
    with pytest.raises(api.NotDownloadable, match="versionCode=0"):
        PlayApi(DEVICE, SESSION).app_delivery("com.google.android.youtube")


def test_certificate_hashes_from_details():
    # YouTube 21.40.161 details 실제 응답의 certificateSet 값 (base64url) — 인증서 2개
    doc = pb.DocV2(details=pb.DocumentDetails(appDetails=pb.AppDetails(certificateSet=[
        pb.CertificateSet(sha1="S6cT3-zpPUdXLcXoRaeoLEqJHy8",
                          sha256="f9LOoww6YCLrKUGc6Pb5LOikvTWwzIee08ymy_XpmS0"),
        pb.CertificateSet(sha1="JLskwF5H4K76aKWKdmF52bYTpgA",
                          sha256="PXoSIwGao52eoONDarfAiWv7T7Z59N5f58I_MmyPmUo")])))

    assert api.certificate_hashes(doc) == [
        ("4ba713dfece93d47572dc5e845a7a82c4a891f2f",
         "7fd2cea30c3a6022eb29419ce8f6f92ce8a4bd35b0cc879ed3cca6cbf5e9992d"),
        ("24bb24c05e47e0aefa68a58a766179d9b613a600",
         "3d7a1223019aa39d9ea0e3436ab7c0896bfb4fb679f4de5fe7c23f326c8f994a")]


def test_certificate_hashes_missing():
    assert api.certificate_hashes(pb.DocV2()) == []
