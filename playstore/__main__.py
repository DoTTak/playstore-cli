"""uv run python -m playstore <command>"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import requests

from . import apksig, auth, browser, checkin, download, session, store
from .api import REVIEW_SORT, TOP_CHARTS, NotDownloadable, PlayApi, PlayApiError, certificate_hashes
from .device import DEFAULT_PROFILE, Profile


class _Missing(Exception):
    """A previous step's result (login, checkin) is missing."""


def _mask(token: str) -> str:
    return f"{token[:12]}… ({len(token)} chars)"


def _account() -> dict:
    account = store.load_account()
    if not account:
        raise _Missing("No saved account. Run login first.")
    return account


def _device() -> dict:
    device = store.load_device()
    if not device:
        raise _Missing("No registered device. Run checkin first.")
    return device


def _new_session(account: dict, device: dict) -> dict:
    print("Creating a Play session...")
    sess = session.build(account, device, log=lambda m: print(f"  {m}"))
    store.save_session(sess)
    return sess


def _play_api(*, fresh: bool = False) -> PlayApi:
    account, device = _account(), _device()
    sess = None if fresh else store.load_session()
    if not sess or sess.get("gsfId") != device["gsfId"]:  # a newly registered device needs a new session
        sess = _new_session(account, device)
    return PlayApi(device, sess)


def _with_api(call):
    """Run call(api). If the Play token expired (401), rebuild the session from the saved AAS token and retry once."""
    try:
        return call(_play_api())
    except PlayApiError as e:
        if e.status != 401:
            raise
        print("The Play token expired; creating a new session.")
        return call(_play_api(fresh=True))


def cmd_login(args) -> int:
    email, oauth_token = args.email, args.oauth_token
    if not oauth_token:
        print("Sign in to your Google account in the browser window (including 2-step verification). "
              "The window closes by itself when you are done.")
        scraped, oauth_token = browser.capture_oauth_token(store.chrome_profile_dir(),
                                                           timeout=args.timeout)
        print(f"Got oauth_token: {_mask(oauth_token)}")
        email = email or scraped
    if not email:
        # The oauth_token expires within minutes, so this must be entered right away
        email = input("Could not detect the email address. Account email: ").strip()

    aas = auth.exchange_oauth_token(email, oauth_token)
    path = store.save_account(email, aas)
    print(f"Got AAS token: {_mask(aas)} — {email}")
    print(f"Saved: {path}")
    return 0


def cmd_checkin(args) -> int:
    account = _account()
    existing = store.load_device()
    if existing and not args.force:
        # Every checkin registers a new device on the account, so reuse the existing one
        print(f"A device is already registered: gsfId={existing['gsfId']} ({existing['profile']})")
        print("Use --force to register a new one")
        return 0

    profile = Profile.load(args.profile)
    print(f"Exchanging for an ac2dm token... ({account['email']})")
    ac2dm = auth.exchange_aas_token(account["email"], account["aasToken"], "ac2dm")
    print(f"Checking in... ({profile.props['UserReadableName']}, {args.locale}, {args.timezone})")
    result = checkin.checkin(profile, account["email"], ac2dm,
                             locale=args.locale, timezone=args.timezone)
    path = store.save_device({"profile": profile.name, "locale": args.locale,
                              "timezone": args.timezone, **result})
    print(f"gsfId: {result['gsfId']}")
    print(f"Saved: {path}")
    return 0


def cmd_session(args) -> int:
    _new_session(_account(), _device())
    print(f"Saved: {store.session_path()}")
    return 0


def cmd_details(args) -> int:
    doc = _with_api(lambda api: api.details(args.package))
    if not doc.docid:
        print(f"No such app: {args.package}", file=sys.stderr)
        return 1
    a = doc.details.appDetails
    print(f"Title        {doc.title}")
    print(f"Package      {a.packageName or doc.docid}")
    print(f"Version      {a.versionString} ({a.versionCode})")
    print(f"Developer    {a.developerName or doc.creator}")
    print(f"Updated      {a.uploadDate}")
    print(f"Installs     {a.numDownloads}")  # the value already includes localized wording such as "… downloads"
    print(f"Size         {a.installationSize / 1024 / 1024:.1f} MB")
    print(f"Permissions  {len(a.permission)}")
    for perm in a.permission:
        print(f"  {perm}")
    return 0


def _price(doc) -> str:
    return next((o.formattedAmount for o in doc.offer if o.offerType == 1 and o.micros), "Free")


def _print_apps(apps) -> None:
    for i, doc in enumerate(apps, 1):
        a = doc.details.appDetails
        print(f"{i:>3}. {doc.title} — {doc.creator}")
        # top chart entries carry no install count
        print("     " + " · ".join(x for x in [doc.docid, a.versionString, a.numDownloads, _price(doc)] if x))


def cmd_search(args) -> int:
    apps = _with_api(lambda api: api.search(args.query, args.limit))
    if not apps:
        print(f"No search results: {args.query}", file=sys.stderr)
        return 1
    _print_apps(apps)
    return 0


# Play app labels for the TOP_CHARTS keys
_CHART_LABELS = {"free": "Top free", "paid": "Top paid", "grossing": "Top grossing", "trending": "Trending"}
# Top-level categories; categoriesList only returns their subcategories
_TOP_CATEGORIES = {"APPLICATION": "Apps", "GAME": "Games"}


def _category_id(api: PlayApi, name: str) -> str:
    """Category ID from an ID (FINANCE, game_action) or a name as `categories` prints it, in any case.

    Upper case or an underscore means an ID. Anything else is looked up by name first, because names in
    an English locale look like IDs ("Puzzle" is GAME_PUZZLE); a single word that matches no name is
    still taken as an ID ("finance" with a non-English locale).
    """
    if re.fullmatch(r"[A-Z_]+", name) or "_" in name:
        return name.upper()
    named = [*_TOP_CATEGORIES.items(), *(c for top in _TOP_CATEGORIES for c in api.categories(top))]
    ids = [cid for cid, title in named if title.casefold() == name.casefold()]
    if not ids and re.fullmatch(r"[A-Za-z]+", name):
        return name.upper()
    if not ids:
        raise ValueError(f"Category not found: {name} (see the list with categories)")
    if len(ids) > 1:
        raise ValueError(f"Several categories are named {name}: {', '.join(ids)}. Pass the ID instead")
    return ids[0]


def cmd_top(args) -> int:
    def fetch(api):
        category = _category_id(api, args.category)
        return category, api.top_chart(category, args.chart, args.limit)

    category, apps = _with_api(fetch)
    title = f"{category} {_CHART_LABELS[args.chart]}"
    if not apps:
        print(f"No chart: {title} (check the category ID with categories)", file=sys.stderr)
        return 1
    print(title)
    _print_apps(apps)
    return 0


def cmd_categories(args) -> int:
    groups = _with_api(lambda api: [(top, api.categories(top)) for top in _TOP_CATEGORIES])
    for top, subs in groups:
        print(f"{top:<24} {_TOP_CATEGORIES[top]}")
        for cid, name in subs:
            print(f"  {cid:<22} {name}")
    return 0


def _date(msec: int) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(msec / 1000))


def _indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def cmd_reviews(args) -> int:
    # rev returns an empty list even for an unknown app, so check the app exists with details,
    # which also gives the rating summary
    doc, reviews = _with_api(lambda api: (
        api.details(args.package),
        api.reviews(args.package, args.limit, sort=args.sort, rating=args.rating)))
    if not doc.docid:
        print(f"No such app: {args.package}", file=sys.stderr)
        return 1
    r = doc.aggregateRating
    if r.ratingsCount:
        print(f"{doc.title} — rated {r.starRating:.1f} ({r.ratingsCount:,} ratings)")
        for star, count in [(5, r.fiveStarRatings), (4, r.fourStarRatings), (3, r.threeStarRatings),
                            (2, r.twoStarRatings), (1, r.oneStarRatings)]:
            print(f"  ★{star} {count * 100 / r.ratingsCount:3.0f}%  {count:,}")
    else:
        print(f"{doc.title} — no ratings")

    label = {"relevant": "most relevant", "newest": "newest"}[args.sort]
    if args.rating:
        label += f" · {args.rating} stars"
    if not reviews:
        print(f"No reviews ({label})", file=sys.stderr)
        return 1
    print(f"\n{len(reviews)} review{'s' if len(reviews) != 1 else ''} ({label})")
    for i, rv in enumerate(reviews, 1):
        meta = [rv.userProfile.name, _date(rv.timestampMsec), rv.documentVersion]
        if rv.thumbsUpCount:
            meta.append(f"{rv.thumbsUpCount:,} found helpful")
        print(f"\n{i:>3}. {'★' * rv.starRating}{'☆' * (5 - rv.starRating)}  {' · '.join(m for m in meta if m)}")
        if rv.title:
            print(f"     [{rv.title}]")
        print(_indent(rv.comment, "     "))
        if rv.replyText:
            print(f"     └ Developer reply ({_date(rv.replyTimestampMsec)})")
            print(_indent(rv.replyText, "       "))
    return 0


def cmd_cert(args) -> int:
    doc = _with_api(lambda api: api.details(args.package))
    if not doc.docid:
        print(f"No such app: {args.package}", file=sys.stderr)
        return 1
    a = doc.details.appDetails
    print(f"{doc.title} {a.versionString} ({a.versionCode}) — {_price(doc)}")

    hashes = certificate_hashes(doc)
    print("Signing certificates from Play details:" if hashes
          else "The details response has no signing information (certificateSet).")
    for sha1, sha256 in hashes:
        print(f"  SHA-1   {sha1}")
        print(f"  SHA-256 {sha256}")
    if not args.apk:
        return 0 if hashes else 1

    _, data = _with_api(lambda api: api.app_delivery(args.package))
    reader = apksig.HttpRangeReader(data.downloadUrl,
                                    {c.name: c.value for c in data.downloadAuthCookie},
                                    size=data.downloadSize)
    signers = apksig.read_signers(reader)
    print(f"APK signing block ({reader.fetched / 1024:.1f} KB of the {data.downloadSize / 1024 / 1024:.1f} MB "
          f"base.apk in {reader.requests} Range requests):")
    for s in signers:
        print(f"  {s.scheme:<4} SHA-1   {s.sha1}")
        print(f"       SHA-256 {s.sha256}")
    same = {s.sha1 for s in signers} & {sha1 for sha1, _ in hashes}
    print("→ matches details" if same else "→ does not match details!")
    return 0 if same else 1


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB"


def _progress_line(prefix: str):
    """On a terminal, overwrite progress on the same line (in 1% steps)."""
    if not sys.stdout.isatty():
        return None
    last = -1

    def show(done: int, total: int):
        nonlocal last
        pct = done * 100 // total if total else 0
        if pct != last:
            last = pct
            print(f"\r{prefix}  {pct:3d}%", end="", flush=True)
    return show


def cmd_download(args) -> int:
    doc, data = _with_api(lambda api: api.app_delivery(args.package, args.version_code))
    a = doc.details.appDetails
    vc = args.version_code or a.versionCode
    # versionString is only in details (the latest version)
    version = (f"{a.versionString} ({vc})" if vc == a.versionCode
               else f"versionCode {vc} (latest {a.versionString} / {a.versionCode})")
    out = Path(args.out) / f"{args.package}-{vc}"
    files = download.play_files(args.package, data)
    print(f"{doc.title} {version} — {len(files)} files, {_mb(sum(f.size for f in files))} → {out}")

    downloader = download.Downloader(data)
    for i, f in enumerate(files, 1):
        prefix = f"  [{i}/{len(files)}] {f.name} ({_mb(f.size)})"
        fetched = downloader.fetch(f, out, progress=_progress_line(prefix))
        print(f"\r{prefix}  {'OK' if fetched else 'already there'}  ")

    apks = [f.name for f in files if f.name.endswith(".apk")]
    print(f"Install: adb install-multiple {out}/*.apk" if len(apks) > 1 else f"Install: adb install {out}/base.apk")
    if len(apks) < len(files):
        print(f"Put the OBB files in /sdcard/Android/obb/{args.package}/ on the device.")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="playstore")
    sub = p.add_subparsers(dest="command", required=True)

    login = sub.add_parser("login", help="sign in to a Google account → save an AAS token")
    login.add_argument("--email", help="account email (detected automatically with the browser login)")
    login.add_argument("--oauth-token",
                       help="oauth_token cookie from EmbeddedSetup (oauth2_4/...); skips the browser")
    login.add_argument("--timeout", type=float, default=600, help="seconds to wait for the browser login")
    login.set_defaults(func=cmd_login)

    ci = sub.add_parser("checkin", help="register a virtual device → save its gsfId")
    ci.add_argument("--profile", default=DEFAULT_PROFILE, help=f"device profile (default {DEFAULT_PROFILE})")
    ci.add_argument("--locale", default="en_US", help="device locale; Play answers in its language (default en_US)")
    ci.add_argument("--timezone", default="UTC", help="device time zone (default UTC)")
    ci.add_argument("--force", action="store_true", help="register a new device even if one is already registered")
    ci.set_defaults(func=cmd_checkin)

    se = sub.add_parser("session", help="create a Play session (Play token, device config upload, terms of service)")
    se.set_defaults(func=cmd_session)

    de = sub.add_parser("details", help="app details and permissions")
    de.add_argument("package", help="package name (e.g. com.google.android.youtube)")
    de.set_defaults(func=cmd_details)

    sr = sub.add_parser("search", help="search apps → find package names")
    sr.add_argument("query", help="search query (e.g. youtube)")
    sr.add_argument("-n", "--limit", type=int, default=20, help="maximum number of results (default 20)")
    sr.set_defaults(func=cmd_search)

    rv = sub.add_parser("reviews", help="app reviews (rating summary, reviews and developer replies)")
    rv.add_argument("package", help="package name (e.g. com.google.android.youtube)")
    rv.add_argument("-n", "--limit", type=int, default=20, help="maximum number of reviews (default 20)")
    rv.add_argument("-s", "--sort", choices=list(REVIEW_SORT), default="relevant",
                    help="relevant: most relevant (default, Play's own order), newest: newest first")
    rv.add_argument("-r", "--rating", type=int, choices=range(1, 6), metavar="1-5",
                    help="only reviews with this many stars")
    rv.set_defaults(func=cmd_reviews)

    tp = sub.add_parser("top", help="top charts by category (top free/paid, top grossing, trending)")
    tp.add_argument("category", nargs="?", default="APPLICATION",
                    help="category ID or name (e.g. FINANCE, Finance, GAME, Games); all apps if omitted")
    tp.add_argument("-c", "--chart", choices=list(TOP_CHARTS), default="free",
                    help=", ".join(f"{k}={v}" for k, v in _CHART_LABELS.items()) + " (default free)")
    tp.add_argument("-n", "--limit", type=int, default=20, help="maximum number of ranks (default 20; Play goes to about 200)")
    tp.set_defaults(func=cmd_top)

    ca = sub.add_parser("categories", help="category IDs and names (values for top)")
    ca.set_defaults(func=cmd_categories)

    ce = sub.add_parser("cert", help="official signing certificate hashes (certificateSet from details)")
    ce.add_argument("package", help="package name (e.g. com.google.android.youtube)")
    ce.add_argument("--apk", action="store_true",
                    help="fetch the end of the APK and compare with its signing block (free apps only; acquires the app if it is not in the library)")
    ce.set_defaults(func=cmd_cert)

    dl = sub.add_parser("download", help="download APKs (base + splits + OBB, free apps only)")
    dl.add_argument("package", help="package name (e.g. com.google.android.youtube)")
    dl.add_argument("-v", "--version-code", type=int,
                    help="versionCode to download (an older version still on Play); latest if omitted")
    dl.add_argument("-o", "--out", default="downloads",
                    help="output directory; files go to <package>-<versionCode>/ under it (default downloads)")
    dl.set_defaults(func=cmd_download)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except (_Missing, browser.LoginError, auth.AuthError, checkin.CheckinError, PlayApiError,
            NotDownloadable, apksig.SignatureError, download.DownloadError,
            requests.RequestException, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
