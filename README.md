# playstore-cli

An unofficial Google Play Store client, for research and learning.

It calls the internal API (fdfe) that the Play Store app uses, so you can:

- sign in to a Google account and register a virtual device
- search apps, and look up app details and permissions
- read the rating summary and reviews (with developer replies)
- browse top charts by category (top free, top paid, top grossing, trending)
- look up the official signing certificate hashes (SHA-1, SHA-256), and compare them with the APK's signing block
- download APKs (base and split APKs, OBBs, older versions)

## Requirements

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- A Chrome-based browser (Chrome, Chromium, Edge, Brave), used by `login`
- A Google account. A separate account for research is recommended (see [Caveats](#caveats))

```bash
uv sync
```

## Quick start

```bash
uv run python -m playstore login                               # 1. Google sign-in → AAS token
uv run python -m playstore checkin                             # 2. register a virtual device → gsfId
uv run python -m playstore search youtube                      # find package names
uv run python -m playstore top Finance                         # top chart of a category (top free)
uv run python -m playstore details com.google.android.youtube  # details and permissions
uv run python -m playstore reviews com.google.android.youtube  # ratings and reviews
uv run python -m playstore cert com.google.android.youtube     # official signing certificate hashes
uv run python -m playstore download com.google.android.youtube # download the APKs
```

`login` and `checkin` are needed only once. A Play session is created whenever needed, and when the Play token expires (401) it is rebuilt from the saved AAS token and the request is retried.

## Commands

| Command | What it does |
|---|---|
| `login` | Sign in to a Google account → save an AAS token |
| `checkin` | Register a virtual device → save its gsfId |
| `session` | Create a Play session (normally created automatically) |
| `search <query>` | Search apps |
| `top [category]` | Top charts by category (top free/paid, top grossing, trending) |
| `categories` | Category IDs and names |
| `details <package>` | App details and permissions |
| `reviews <package>` | Rating summary and reviews |
| `cert <package>` | SHA-1 and SHA-256 of the official signing certificates |
| `download <package>` | Download the base and split APKs and OBBs |

### login

```bash
uv run python -m playstore login [--email EMAIL] [--oauth-token TOKEN] [--timeout 600]
```

- Opens the Google "Add account" page (`accounts.google.com/EmbeddedSetup`) in a browser window. Once you have signed in (including 2-step verification), it reads the `oauth_token` cookie and exchanges it for a long-lived AAS token. The window closes by itself.
- The browser profile used for the login is kept, so 2-step verification is usually asked only the first time.
- With `--oauth-token`, no browser is opened. That token expires within minutes, so use it right away.

### checkin

```bash
uv run python -m playstore checkin [--profile pixel_9a] [--locale en_US] [--timezone UTC] [--force]
```

- Registers a virtual device (Pixel 9a, Android 15 / SDK 35 by default) with Google and gets its gsfId. All later Play requests are made as this device.
- Play answers in the device locale (app titles, install counts, category names). Prices are in the currency of the account's country.
- Every checkin adds a new device to the account, so an already registered device is reused. Pass `--force` to register a new one.

### search

```bash
uv run python -m playstore search youtube [-n 20]
```

```
  1. YouTube — Google LLC
     com.google.android.youtube · 21.40.161 · 10,000,000,000+ downloads · Free
  2. Messenger — Meta Platforms, Inc.
     com.facebook.orca · 582.0.0.61.92 · 5,000,000,000+ downloads · Free
```

- Pages through the results and collects up to `-n` apps without duplicates, until Play stops returning more ("youtube" ends at 508).
- Play returns popular apps even for a query with no match.

### top

```bash
uv run python -m playstore top [category] [-c free|paid|grossing|trending] [-n 20]
```

```
$ uv run python -m playstore top GAME -c paid -n 3
GAME Top paid
  1. Dungeon Merge! — Brilbox
     com.brilbox.dungeonmerge · 1.0.41 · ₩70
  2. Minecraft: Dream it, Build it! — Mojang
     com.mojang.minecraftpe · 1.26.52.3 · ₩9,900
  3. Stardew Valley — ConcernedApe
     com.chucklefish.stardewvalley · 1.6.15.3 · ₩5,900
```

- The category is an ID (`FINANCE`, `GAME_PUZZLE`; any case if it has an underscore) or a name as `categories` prints it (`Finance`, `Puzzle`, `Health & Fitness`; any case). Without one, the chart covers all apps (`APPLICATION`). Names shared by several categories (`Sports` = `SPORTS`, `GAME_SPORTS`) need the ID.
- `-c`: `free` top free (default), `paid` top paid, `grossing` top grossing, `trending` trending.
- Ranks come 7 at a time and are collected up to `-n`. Play goes to about rank 200 (around 5 seconds for 200). Charts with few apps are shorter (top paid in Finance has 1).
- Top chart responses carry no install count, so it is left out of the output.

### categories

```bash
uv run python -m playstore categories
```

```
APPLICATION              Apps
  ANDROID_AUTO_PROJECTED Android Auto
  ...
  FINANCE                Finance
  ...
GAME                     Games
  GAME_ACTION            Action
  ...
```

- The category IDs and names to pass to `top`. Names come in the device locale (35 app and 17 game categories).

### details

```bash
uv run python -m playstore details com.google.android.youtube
```

```
Title        YouTube
Package      com.google.android.youtube
Version      21.40.161 (1561301413)
Developer    Google LLC
Updated      Oct 1, 2026
Installs     10,000,000,000+ downloads
Size         139.1 MB
Permissions  51
  android.permission.ACCESS_ADSERVICES_AD_ID
  ...
```

The permission list is not the manifest as-is but **a list Play adapts to this device**: permissions whose `maxSdkVersion` is below the device SDK are dropped, and implied permissions are added. YouTube, for example, has 50 in its manifest, plus `READ_EXTERNAL_STORAGE`, implied by `WRITE_EXTERNAL_STORAGE`, for 51.

### reviews

```bash
uv run python -m playstore reviews com.google.android.youtube [-n 20] [-s relevant|newest] [-r 1-5]
```

```
YouTube — rated 2.9 (160,324,552 ratings)
  ★5  37%  59,074,443
  ★4   7%  11,500,112
  ★3   7%  11,071,837
  ★2   6%  9,385,479
  ★1  43%  69,292,286

20 reviews (most relevant)

  1. ★★★★☆  <author> · 2026-07-07 · 21.25.529 · 1,102 found helpful
     <review text>
     └ Developer reply (<date>)
       <reply text>
```

- The rating summary (average and per-star distribution) is followed by the reviews. Each review shows the author, date, app version at the time, "found helpful" count and the developer reply.
- `-s`: `relevant` (most relevant, default) is the same order as the reviews on the Play Store web page; `newest` is newest first.
- `-r`: only reviews with that many stars.
- Pages through the reviews up to `-n` (100 per request).

### cert

```bash
uv run python -m playstore cert com.google.android.youtube [--apk]
```

| | `cert` | `cert --apk` |
|---|---|---|
| Source | `certificateSet` in the Play details response | The real APK's signing block (v2/v3/v3.1) |
| Requests | 1 details request | details, delivery (purchase if needed), a few Range requests for the end of the APK (YouTube: 3 requests, 80 KB) |
| Paid apps | Yes (no purchase) | No |
| Side effects | None | A free app not yet in the account library gets added to it |

- `cert` is enough when you only need the signing hashes. `--apk` checks the hashes Play reports against the file it actually distributes.
- The hashes are **hashes of the certificate (DER)**, the same as the certificate digest from Android's `PackageManager` or `apksigner`.
- **Some apps have several certificates, and the details list doesn't necessarily match the APK's signers.** YouTube 21.40.161:

  | Source | Certificate SHA-1 |
  |---|---|
  | details `certificateSet` | `4ba713df…`, `24bb24c0…` |
  | APK v3 signer (Android 7-12, SDK 24-32) | `24bb24c0…` |
  | APK v3.1 signer (Android 13+, SDK 33+) | `f8456b1d…` (key rotation: `24bb24c0…` → `f8456b1d…`) |

  Devices on Android 13 or later use the rotated key (`f8456b1d…`), which is not in the details list. Conversely, `4ba713df…`, which only details lists, is nowhere in the APK's signers or lineage. To compare with what a device reports, check the `--apk` result as well.
- `--apk` does not support APKs signed with v1 (JAR) only.

### download

```bash
uv run python -m playstore download com.google.android.youtube [-v VERSION_CODE] [-o downloads]
```

```
YouTube 21.40.161 (1561301413) — 4 files, 139.1 MB → downloads/com.google.android.youtube-1561301413
  [1/4] base.apk (62.1 MB)  OK
  [2/4] split_config.arm64_v8a.apk (74.0 MB)  OK
  [3/4] split_config.en.apk (0.3 MB)  OK
  [4/4] split_config.xxhdpi.apk (2.7 MB)  OK
Install: adb install-multiple downloads/com.google.android.youtube-1561301413/*.apk
```

- Downloads the base APK, every split APK and the OBBs into `downloads/<package>-<versionCode>/`.
- File names follow the on-device `/data/app` layout (`base.apk`, `split_<name>.apk`), so the printed `adb install-multiple` command installs them directly. OBBs (`[main|patch].<versionCode>.<package>.obb`) go in `/sdcard/Android/obb/<package>/` on the device.
- Each file is checked against the size and SHA-256 (SHA-1 for OBBs) that Play reports. Incomplete or mismatching files are not kept, and files already downloaded and verified are not downloaded again.
- `-v` downloads an older version, if Play still has it. You need to know the version code. For a version Play doesn't have, it fails with `no download URL for versionCode N`.
- Free apps only. An app not yet in the account library is added to it (purchase).

## Storage

| Path | Contents |
|---|---|
| `~/.config/playstore-cli/account.json` | Email and AAS token (mode 600) |
| `~/.config/playstore-cli/device.json` | Device profile, gsfId, checkin tokens |
| `~/.config/playstore-cli/session.json` | Play token, deviceConfigToken, dfeCookie |
| `~/.config/playstore-cli/chrome-profile/` | Browser profile for the login |
| `downloads/` | Downloaded APKs (git-ignored) |

Set the `PLAYSTORE_HOME` environment variable to use another directory than `~/.config/playstore-cli`.

## Layout

```
playstore/
  __main__.py        CLI
  browser.py         login step 1: get the oauth_token cookie through Chrome (DevTools protocol)
  auth.py            android.clients.google.com/auth: oauth_token → AAS → ac2dm / Play tokens
  device.py          device profile (.properties) → User-Agent, checkin request, device configuration
  checkin.py         virtual device registration → gsfId
  session.py         Play token → device config upload → terms of service (toc)
  api.py             fdfe API: details, search, top_chart, categories, reviews, purchase, delivery
  download.py        delivery response → file list, download and verification
  apksig.py          APK signing block (v2/v3/v3.1) parser (local file / HTTP Range)
  store.py           account, device and session storage
  googleplay.proto   Play protocol definitions, only the messages needed (googleplay_pb2.py is generated)
  profiles/          device profiles
tests/               tests against local HTTP stand-ins (no requests to Google)
```

## Development

```bash
uv run pytest
```

After changing the proto, regenerate it from the repository root (protoc 28.x):

```bash
protoc -I. --python_out=. playstore/googleplay.proto
```

## Play API behavior confirmed against real responses

Things that are missing from, or differ from, the reference implementations (gpapi, Aurora):

- **Signing hashes:** field 22 of `AppDetails` in the details response (`certificateSet`) holds each certificate's SHA-1 and SHA-256, base64url-encoded without padding. Paid apps and apps incompatible with the device have it too. It doesn't necessarily match the APK's signers, though (see [cert](#cert)).
- **delivery hashes:** the file `sha1`/`sha256` in a delivery response are base64url without padding too.
- **Missing versions:** asking delivery for a versionCode Play doesn't have returns no error, only an empty `downloadUrl`.
- **Search User-Agent:** sending `search` with the current Play User-Agent returns empty result clusters, so search uses a legacy User-Agent. Results continue along two paths: a cluster's next results (`getCluster?…`) and the next clusters (`searchList?…`).
- **Reviews:** `rev` takes `sort` `2` for most relevant and `0` for newest. An unknown app returns an empty list (HTTP 200) instead of an error, and the next-page path (`ptkn`) carries the sort and star filter over. The "found helpful" count of a review is field 38, which gpapi doesn't define.
- **Top charts:** `listTopChartItems?c=3&scat=<category ID>&stcid=<chart>`, where `stcid` is `apps_topselling_free`, `apps_topselling_paid`, `apps_topgrossing` or `apps_movers_shakers`. A `scat` that is not a category ID (such as `apps`) returns an empty list. The next 7 ranks continue through the cluster's `getCluster?…`. With the legacy User-Agent the same apps come back but some ranks are reordered (ranks 23-37 of top free in Finance, for example), so top charts use the device profile's User-Agent.
- **Category list:** sending `categoriesList` with the current Play User-Agent returns a newer stream format without `listResponse`, so it uses the legacy User-Agent like search. The category ID is in each document's category home path (`homeV2?cat=FINANCE&c=3`).
- **Permissions:** the list is adapted to the device SDK rather than copied from the manifest: some permissions are dropped because of `maxSdkVersion`, and implied permissions are added.
- **Download CDN:** Google's CDN (`*.gvt1.com`) rejects suffix ranges (`bytes=-N`) with 416. Both start and end have to be given.
- **Auth TLS:** `/auth` returns 403 `BadAuthentication` if TLS ALPN is sent or session tickets are disabled (the same tweaks as gpsoauth are needed).

## Caveats

- This project is for research and learning. It uses an unofficial Google Play API, which may go against the terms of service, and **the account may get restricted.** Use a separate account for research.
- The AAS token is a long-lived token roughly as sensitive as the account password. Keep `account.json` private.
- Downloaded APKs are their developers' work. Don't redistribute them.

## References

- [Aurora Store / gplayapi](https://gitlab.com/AuroraOSS/gplayapi): request headers, search, device profiles
- [googleplay-api (gpapi)](https://github.com/NoMore201/googleplay-api): proto field numbers, checkin, delivery
- [gpsoauth](https://github.com/simon-weber/gpsoauth): `/auth` TLS settings
- [APK Signature Scheme v2](https://source.android.com/docs/security/features/apksigning/v2)

`playstore/profiles/pixel_9a.properties` is a device profile from The Calyx Institute, licensed GPL-3.0-or-later.
