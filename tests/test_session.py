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
    play_server.respond(delivery(""), path="/fdfe/delivery")  # not in the library yet
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


@pytest.mark.parametrize("latest", [1561301413, 0])  # 0: the given version is tried even if the latest is incompatible with the device
def test_app_delivery_version_code(play_server, latest):
    serve_details(play_server, version_code=latest)
    play_server.respond(delivery("https://play.example/base.apk"), path="/fdfe/delivery")

    _, data = PlayApi(DEVICE, SESSION).app_delivery("com.google.android.youtube", 1550000000)

    assert data.downloadUrl == "https://play.example/base.apk"
    (req,) = play_server.by_path("/fdfe/delivery")
    assert parse_qs(req["query"])["vc"] == ["1550000000"]


def test_app_delivery_missing_version(play_server):
    # A version Play doesn't have: the real server returns no error, just an empty downloadUrl
    serve_details(play_server)
    play_server.respond(delivery(""), path="/fdfe/delivery")
    play_server.respond(wrap(buyResponse=pb.BuyResponse(downloadToken="dtok-1")), path="/fdfe/purchase")

    with pytest.raises(api.NotDownloadable, match="versionCode 1 의 다운로드 URL 이 없음"):
        PlayApi(DEVICE, SESSION).app_delivery("com.google.android.youtube", 1)


def test_certificate_hashes_from_details():
    # certificateSet values (base64url) from a real YouTube 21.40.161 details response: 2 certificates
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


def app(pkg: str) -> pb.DocV2:
    return pb.DocV2(docid=pkg, docType=1, title=pkg.upper())


def stream(*clusters: pb.DocV2, next_page: str = "") -> pb.ListResponse:
    return pb.ListResponse(doc=[pb.DocV2(
        child=clusters, containerMetadata=pb.ContainerMetadata(nextPageUrl=next_page))])


def cluster(*children: pb.DocV2, next_page: str = "") -> pb.DocV2:
    return pb.DocV2(docType=45, child=children, containerMetadata=pb.ContainerMetadata(nextPageUrl=next_page))


def serve_search(srv, first: pb.ListResponse):
    # The first page carries results in preFetch and only a searchResponse in payload (real response layout)
    srv.respond(pb.ResponseWrapper(
        payload=pb.Payload(searchResponse=pb.SearchResponse(originalQuery="은행")),
        preFetch=[pb.PreFetch(response=pb.ResponseWrapper(payload=pb.Payload(listResponse=first)))],
    ).SerializeToString(), path="/fdfe/search")


def test_search_follows_cluster_then_stream_pages(play_server):
    related = pb.DocV2(docType=53, title="국민은행")  # related search, not an app
    serve_search(play_server, stream(
        cluster(app("a.one"), app("a.two"), next_page="getCluster?enpt=E1"),
        next_page="searchList?q=%EC%9D%80%ED%96%89&ctntkn=T1"))
    play_server.respond(wrap(listResponse=stream(cluster(app("a.three")))), path="/fdfe/getCluster")
    play_server.respond(wrap(listResponse=stream(cluster(related), cluster(app("a.two"), app("a.four")))),
                        path="/fdfe/searchList")

    apps = PlayApi(DEVICE, SESSION).search("은행")

    # The first cluster's next results (getCluster) come before the next clusters (searchList)
    assert [a.docid for a in apps] == ["a.one", "a.two", "a.three", "a.four"]
    assert [r["path"] for r in play_server.requests] == ["/fdfe/search", "/fdfe/getCluster", "/fdfe/searchList"]
    first, by_cluster, by_stream = play_server.requests
    assert parse_qs(first["query"]) == {"q": ["은행"], "c": ["3"], "ksm": ["1"]}
    assert parse_qs(by_cluster["query"]) == {"enpt": ["E1"]}
    assert parse_qs(by_stream["query"])["ctntkn"] == ["T1"]
    for req in play_server.requests:
        assert req["headers"]["User-Agent"] == api.LEGACY_USER_AGENT
        assert req["headers"]["Authorization"] == "Bearer play-token"


def test_search_limit_skips_next_page(play_server):
    serve_search(play_server, stream(cluster(app("a.one"), app("a.two"), app("a.three")),
                                     next_page="searchList?q=x&ctntkn=T1"))

    assert [a.docid for a in PlayApi(DEVICE, SESSION).search("x", limit=2)] == ["a.one", "a.two"]
    assert not play_server.by_path("/fdfe/searchList")


def test_search_stops_on_page_without_new_apps(play_server):
    serve_search(play_server, stream(cluster(app("a.one")), next_page="searchList?q=x&ctntkn=T1"))
    play_server.respond(wrap(listResponse=stream(next_page="searchList?q=x&ctntkn=T2")),
                        path="/fdfe/searchList")

    assert [a.docid for a in PlayApi(DEVICE, SESSION).search("x")] == ["a.one"]
    assert len(play_server.by_path("/fdfe/searchList")) == 1


def test_search_no_results(play_server):
    serve_search(play_server, stream())
    assert PlayApi(DEVICE, SESSION).search("zzzz") == []
