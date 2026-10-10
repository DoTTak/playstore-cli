"""Client for the Play Store's internal API (fdfe).

Requests go to android.clients.google.com/fdfe/<path>; every response is a ResponseWrapper protobuf.
Headers follow Aurora Store's HeaderProvider (via alltechdev's get_auth_headers).
"""
from __future__ import annotations

import base64
from collections import deque

import requests
from google.protobuf.message import DecodeError

from . import googleplay_pb2 as pb
from .device import Profile

FDFE_URL = "https://android.clients.google.com/fdfe/"

# X-DFE-Encoded-Targets value sent by Aurora (list of experiment/feature targets, an opaque constant)
ENCODED_TARGETS = "CAESN/qigQYC2AMBFfUbyA7SM5Ij/CvfBoIDgxXrBPsDlQUdMfOLAfoFrwEHgAcBrQYhoA0cGt4MKK0Y2gI"

# Sending search with the device profile's current Play User-Agent returns empty result clusters
# (0 apps for both "bank" in Korean and "calculator", seen in real responses). With Aurora's legacy
# Play User-Agent the clusters come back filled.
LEGACY_USER_AGENT = "Android-Finsky/29.2.15-21 [0] [PR] 426536134 (api=3,versionCode=82921510,sdk=25)"

# sort values for rev (checked against real responses). Relevance order matches the reviews shown on
# the web details page. 1 sorts by highest rating, and 3 and 4 return the same as 2, so they are left out.
REVIEW_SORT = {"relevant": "2", "newest": "0"}
# Reviews per rev request. n=1000 also comes back in one response (about 500 KB), but paging keeps
# each response small
REVIEW_PAGE_SIZE = 100


class PlayApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class NotDownloadable(Exception):
    """No download URL is available (paid app, incompatible with the device, etc.)."""


def _b64url_hex(s: str) -> str:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).hex()


def _apps(doc: pb.DocV2) -> list[pb.DocV2]:
    """App documents (docType 1) inside a cluster tree. Other documents, such as related searches (53), are skipped."""
    if doc.docType == 1:
        return [doc]
    return [app for child in doc.child for app in _apps(child)]


def certificate_hashes(doc: pb.DocV2) -> list[tuple[str, str]]:
    """Signing certificate hashes from a details response, [(SHA-1, SHA-256) hex]. No purchase or download needed."""
    return [(_b64url_hex(c.sha1), _b64url_hex(c.sha256))
            for c in doc.details.appDetails.certificateSet]


class PlayApi:
    """device: checkin result (device.json), session: Play session tokens (session.json)."""

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
        """Upload the device configuration (screen, features, ABIs, ...) to the account and get a deviceConfigToken."""
        body = pb.UploadDeviceConfigRequest(deviceConfiguration=self.profile.device_config())
        w = self.request("POST", "uploadDeviceConfig", data=body.SerializeToString(), extra_headers={
            "Content-Type": "application/x-protobuf",
            # Extra headers, same as gpapi getDeviceUploadHeaders / Aurora uploadDeviceConfig
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
        """One page of search results → (apps, paths of the pages that follow).

        The first page (search) puts its results in preFetch; later pages put them in the payload.
        Either way listResponse.doc = stream → clusters → apps. There are two kinds of follow-up paths:
          a cluster's nextPageUrl (getCluster?…) = the next results of that cluster
          the stream's nextPageUrl (searchList?…) = the next clusters
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
        """Search apps. Follow the follow-up pages in order and collect up to limit apps without duplicates.

        For "bank" (in Korean): 20 on the first page, 5 more from that cluster, then about 7 per later cluster.
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
                # Don't follow paths from a page without new apps, so we never get stuck on an endless run of empty pages
                continue
            for n in nexts:
                if n not in queued:
                    queued.add(n)
                    queue.append((n, None))
        return list(found.values())[:limit]

    def reviews(self, package: str, limit: int = 20, *, sort: str = "relevant",
                rating: int = 0) -> list[pb.Review]:
        """App reviews. Follows nextPageUrl and collects up to limit reviews.

        sort is a REVIEW_SORT key; rating (1-5) returns only reviews with that many stars.
        The next page path carries sort and rating over. An unknown app gives an empty list, not an error.
        """
        params = {"doc": package, "n": str(min(limit, REVIEW_PAGE_SIZE)), "sort": REVIEW_SORT[sort]}
        if rating:
            params["rating"] = str(rating)
        path, found = "rev", []
        while path and len(found) < limit:
            page = self.request("GET", path, params=params).payload.reviewResponse
            if not page.getResponse.review:
                break
            found += page.getResponse.review
            path, params = page.nextPageUrl, None
        return found[:limit]

    def purchase(self, package: str, version_code: int) -> str:
        """Add ("acquire") a free app to the account library. Pass any returned downloadToken to delivery."""
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
        """details → delivery (purchase and retry if the app is not in the library). Returns data with the download URL.

        With version_code, that version is fetched; without it, the latest version from details.
        For a version Play doesn't have, delivery returns no error, just an empty download URL.
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
