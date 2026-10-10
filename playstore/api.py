"""Play 스토어 내부 API(fdfe) 클라이언트.

요청은 android.clients.google.com/fdfe/<경로>, 응답은 모두 ResponseWrapper protobuf 다.
헤더 구성은 Aurora Store HeaderProvider (alltechdev get_auth_headers 경유)를 따른다.
"""
from __future__ import annotations

import base64
from collections import deque

import requests
from google.protobuf.message import DecodeError

from . import googleplay_pb2 as pb
from .device import Profile

FDFE_URL = "https://android.clients.google.com/fdfe/"

# Aurora 가 보내는 X-DFE-Encoded-Targets 값 (실험·기능 대상 목록, 불투명 상수)
ENCODED_TARGETS = "CAESN/qigQYC2AMBFfUbyA7SM5Ij/CvfBoIDgxXrBPsDlQUdMfOLAfoFrwEHgAcBrQYhoA0cGt4MKK0Y2gI"

# search 를 기기 프로필의 최신 Play User-Agent 로 보내면 결과 묶음이 빈 채로 온다
# ("은행", "calculator" 모두 앱 0개, 실제 응답에서 확인). Aurora 의 구버전 Play User-Agent 로 보내면 채워져 온다.
LEGACY_USER_AGENT = "Android-Finsky/29.2.15-21 [0] [PR] 426536134 (api=3,versionCode=82921510,sdk=25)"


class PlayApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class NotDownloadable(Exception):
    """유료 앱, 기기 비호환 등으로 다운로드 URL 을 받을 수 없음."""


def _b64url_hex(s: str) -> str:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).hex()


def _apps(doc: pb.DocV2) -> list[pb.DocV2]:
    """묶음(클러스터) 트리 안의 앱 문서들 (docType 1). 관련 검색어(53) 같은 다른 문서는 건너뛴다."""
    if doc.docType == 1:
        return [doc]
    return [app for child in doc.child for app in _apps(child)]


def certificate_hashes(doc: pb.DocV2) -> list[tuple[str, str]]:
    """details 응답의 서명 인증서 해시 [(SHA-1, SHA-256) hex]. 구매·다운로드 없이 얻는다."""
    return [(_b64url_hex(c.sha1), _b64url_hex(c.sha256))
            for c in doc.details.appDetails.certificateSet]


class PlayApi:
    """device: checkin 결과(device.json), session: Play 세션 토큰들(session.json)."""

    def __init__(self, device: dict, session: dict):
        self.profile = Profile.load(device["profile"])
        self.device = device
        self.session = session
        self._http = requests.Session()

    def headers(self) -> dict:
        locale = self.device["locale"]
        h = {
            "Authorization": f"Bearer {self.session['authToken']}",
            "User-Agent": self.profile.user_agent(),
            "X-DFE-Device-Id": self.device["gsfId"],
            "X-DFE-Device-Checkin-Consistency-Token": self.device["deviceCheckinConsistencyToken"],
            "Accept-Language": locale.replace("_", "-"),
            "X-DFE-UserLanguages": locale,
            "X-DFE-MCCMNC": self.profile.props["CellOperator"],
            "X-DFE-Encoded-Targets": ENCODED_TARGETS,
            "X-DFE-Client-Id": "am-android-google",
            "X-DFE-Network-Type": "4",
            "X-DFE-Content-Filters": "",
            "X-DFE-Request-Params": "timeoutMs=4000",
            "X-DFE-No-Prefetch": "true",
            "X-Limit-Ad-Tracking-Enabled": "false",
            "X-Ad-Id": "",
        }
        if self.session.get("deviceConfigToken"):
            h["X-DFE-Device-Config-Token"] = self.session["deviceConfigToken"]
        if self.session.get("dfeCookie"):
            h["X-DFE-Cookie"] = self.session["dfeCookie"]
        return h

    def request(self, method: str, path: str, *, params=None, data=None,
                extra_headers: dict | None = None) -> pb.ResponseWrapper:
        resp = self._http.request(method, FDFE_URL + path, params=params, data=data, timeout=30,
                                  headers={**self.headers(), **(extra_headers or {})})
        try:
            wrapper = pb.ResponseWrapper.FromString(resp.content)
        except DecodeError:
            wrapper = None
        if resp.status_code != 200:
            message = wrapper and wrapper.commands.displayErrorMessage
            raise PlayApiError(resp.status_code, message or resp.text[:200])
        if wrapper is None:
            raise PlayApiError(resp.status_code, f"protobuf 가 아닌 응답: {resp.content[:80]!r}")
        return wrapper

    def upload_device_config(self) -> str:
        """기기 구성(화면·기능·ABI 등)을 계정에 올리고 deviceConfigToken 을 받는다."""
        body = pb.UploadDeviceConfigRequest(deviceConfiguration=self.profile.device_config())
        w = self.request("POST", "uploadDeviceConfig", data=body.SerializeToString(), extra_headers={
            "Content-Type": "application/x-protobuf",
            # gpapi getDeviceUploadHeaders / Aurora uploadDeviceConfig 와 같은 추가 헤더
            "X-DFE-Enabled-Experiments": "cl:billing.select_add_instrument_by_default",
            "X-DFE-Unsupported-Experiments": (
                "nocache:billing.use_charging_poller,market_emails,buyer_currency,prod_baseline,"
                "checkin.set_asset_paid_app_field,shekel_test,content_ratings,buyer_currency_in_app,"
                "nocache:encrypted_apk,recent_changes"),
            "X-DFE-SmallestScreenWidthDp": "320",
            "X-DFE-Filter-Level": "3",
        })
        return w.payload.uploadDeviceConfigResponse.uploadDeviceConfigToken

    def toc(self) -> pb.TocResponse:
        return self.request("GET", "toc").payload.tocResponse

    def accept_tos(self, tos_token: str) -> None:
        self.request("POST", "acceptTos", data={"tost": tos_token, "toscme": "false"})

    def details(self, package: str) -> pb.DocV2:
        return self.request("GET", "details", params={"doc": package}).payload.detailsResponse.docV2

    def _search_page(self, path: str, params: dict | None = None) -> tuple[list[pb.DocV2], list[str]]:
        """검색 결과 한 페이지 → (앱 목록, 이어지는 페이지 경로들).

        첫 페이지(search)는 결과를 preFetch 에, 이후 페이지는 payload 에 담아 준다.
        어느 쪽이든 listResponse.doc = 스트림 → 묶음들 → 앱들이다. 이어지는 경로는 두 종류:
          묶음의 nextPageUrl (getCluster?…) = 그 묶음의 다음 결과
          스트림의 nextPageUrl (searchList?…) = 다음 묶음들
        """
        w = self.request("GET", path, params=params, extra_headers={"User-Agent": LEGACY_USER_AGENT})
        apps, nexts = [], []
        for wrapper in [w, *(p.response for p in w.preFetch)]:
            for stream in wrapper.payload.listResponse.doc:
                for cluster in stream.child:
                    apps += _apps(cluster)
                    nexts.append(cluster.containerMetadata.nextPageUrl)
                nexts.append(stream.containerMetadata.nextPageUrl)
        return apps, [n for n in nexts if n]

    def search(self, query: str, limit: int = 20) -> list[pb.DocV2]:
        """앱 검색. 이어지는 페이지를 앞에서부터 따라가며 중복 없이 limit 개까지 모은다.

        "은행" 기준 첫 페이지 20개, 그 묶음의 다음 결과 5개, 다음 묶음부터 7개씩 온다.
        """
        found: dict[str, pb.DocV2] = {}
        queue = deque([("search", {"q": query, "c": "3", "ksm": "1"})])
        queued: set[str] = set()
        while queue and len(found) < limit:
            apps, nexts = self._search_page(*queue.popleft())
            before = len(found)
            for app in apps:
                found.setdefault(app.docid, app)
            if len(found) == before:
                # 새 앱이 없는 페이지의 경로는 따라가지 않는다 — 빈 페이지만 계속 이어지는 응답에 갇히지 않게
                continue
            for n in nexts:
                if n not in queued:
                    queued.add(n)
                    queue.append((n, None))
        return list(found.values())[:limit]

    def purchase(self, package: str, version_code: int) -> str:
        """무료 앱을 계정 보관함에 추가('획득')한다. downloadToken 이 오면 delivery 에 넘긴다."""
        w = self.request("POST", "purchase", data={"doc": package, "ot": "1", "vc": str(version_code)})
        return w.payload.buyResponse.downloadToken

    def delivery(self, package: str, version_code: int,
                 download_token: str = "") -> pb.AndroidAppDeliveryData:
        params = {"doc": package, "ot": "1", "vc": str(version_code)}
        if download_token:
            params["dtok"] = download_token
        return self.request("GET", "delivery", params=params).payload.deliveryResponse.appDeliveryData

    def app_delivery(self, package: str,
                     version_code: int | None = None) -> tuple[pb.DocV2, pb.AndroidAppDeliveryData]:
        """details → delivery (보관함에 없으면 purchase 후 재시도). 다운로드 URL 이 든 데이터를 반환.

        version_code 를 주면 그 버전을, 안 주면 details 의 최신 버전을 받는다.
        Play 에 없는 버전이면 delivery 가 오류 없이 다운로드 URL 만 비워서 준다.
        """
        doc = self.details(package)
        paid = next((o for o in doc.offer if o.offerType == 1 and o.micros > 0), None)
        if paid:
            raise NotDownloadable(f"유료 앱 ({paid.formattedAmount})")
        vc = version_code or doc.details.appDetails.versionCode
        if not vc:
            raise NotDownloadable("이 기기 프로필에 호환되는 버전이 없음 (versionCode=0)")

        try:
            data = self.delivery(package, vc)
        except PlayApiError:
            data = None
        if not (data and data.downloadUrl):
            data = self.delivery(package, vc, self.purchase(package, vc))
        if not data.downloadUrl:
            raise NotDownloadable(f"versionCode {vc} 의 다운로드 URL 이 없음 (Play 에 없는 버전이거나 기기 비호환)"
                                  if version_code else "delivery 응답에 다운로드 URL 이 없음")
        return doc, data
