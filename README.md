# DoTTakStore

Google Play 스토어 비공식 클라이언트입니다 (연구·학습용).

Play 스토어 앱이 쓰는 내부 API(fdfe)를 직접 호출해서 다음을 할 수 있습니다.

- 구글 계정 로그인, 가상 기기 등록
- 앱 검색, 상세 정보와 권한 목록 조회
- 정식 서명 인증서 해시(SHA-1, SHA-256) 조회. 실제 APK의 서명 블록과 대조도 가능
- APK 다운로드 (base·split APK, OBB, 과거 버전)

## 요구 사항

- Python 3.12 이상, [uv](https://docs.astral.sh/uv/)
- Chrome 계열 브라우저 (Chrome, Chromium, Edge, Brave): `login`에서 사용
- 구글 계정: 연구용 별도 계정을 권장합니다 (아래 [주의](#주의) 참고)

```bash
uv sync
```

## 빠른 시작

```bash
uv run python -m playstore login                               # 1. 구글 로그인 → AAS 토큰
uv run python -m playstore checkin                             # 2. 가상 기기 등록 → gsfId
uv run python -m playstore search youtube                      # 패키지명 찾기
uv run python -m playstore details com.google.android.youtube  # 상세 정보·권한
uv run python -m playstore cert com.google.android.youtube     # 정식 서명 해시
uv run python -m playstore download com.google.android.youtube # APK 다운로드
```

`login`과 `checkin`은 처음 한 번만 하면 됩니다. Play 세션은 필요할 때 자동으로 만들어지고, Play 토큰이 만료되면(401) 저장된 AAS 토큰으로 다시 만든 뒤 재시도합니다.

## 명령

| 명령 | 하는 일 |
|---|---|
| `login` | 구글 계정 로그인 → AAS 토큰 저장 |
| `checkin` | 가상 기기 등록 → gsfId 저장 |
| `session` | Play 세션 생성 (보통은 자동으로 만들어져서 직접 쓸 일이 없음) |
| `search <검색어>` | 앱 검색 |
| `details <패키지>` | 앱 상세 정보와 권한 목록 |
| `cert <패키지>` | 정식 서명 인증서의 SHA-1, SHA-256 |
| `download <패키지>` | base·split APK와 OBB 다운로드 |

### login

```bash
uv run python -m playstore login [--email EMAIL] [--oauth-token TOKEN] [--timeout 600]
```

- 브라우저 창에 구글 계정 추가 화면(`accounts.google.com/EmbeddedSetup`)을 띄웁니다. 로그인(2단계 인증 포함)이 끝나면 `oauth_token` 쿠키를 읽고, 이를 장기 토큰인 AAS 토큰으로 교환합니다. 창은 자동으로 닫힙니다.
- 로그인용 Chrome 프로필이 따로 보관되므로 2단계 인증은 보통 처음 한 번만 묻습니다.
- `--oauth-token`을 주면 브라우저를 띄우지 않습니다. 이 토큰은 몇 분 안에 만료되니 바로 써야 합니다.

### checkin

```bash
uv run python -m playstore checkin [--profile pixel_9a] [--locale ko_KR] [--timezone Asia/Seoul] [--force]
```

- 가상 기기(기본 Pixel 9a, Android 15 / SDK 35)를 구글에 등록하고 gsfId를 받습니다. 이후 Play 요청은 모두 이 기기 이름으로 나갑니다.
- checkin을 할 때마다 계정에 새 기기가 추가되므로, 이미 등록한 기기가 있으면 그것을 다시 씁니다. 새로 등록하려면 `--force`를 줍니다.

### search

```bash
uv run python -m playstore search youtube [-n 20]
```

```
  1. YouTube — Google LLC
     com.google.android.youtube · 21.40.161 · 다운로드 10,000,000,000회 이상 · 무료
  2. Microsoft Excel: Spreadsheets — Microsoft Corporation
     com.microsoft.office.excel · 16.0.20527.20034 · 다운로드 1,000,000,000회 이상 · 무료
```

- 페이지를 넘기며 중복 없이 `-n`개까지 모읍니다. Play가 더 주지 않으면 거기서 끝납니다 ("youtube"는 463개).
- 결과가 없는 검색어에도 Play는 인기 앱을 결과로 돌려줍니다.

### details

```bash
uv run python -m playstore details com.google.android.youtube
```

```
제목     YouTube
패키지   com.google.android.youtube
버전     21.40.161 (1561301413)
개발자   Google LLC
업데이트 2026. 10. 1.
설치수   다운로드 10,000,000,000회 이상
크기     139.0 MB
권한     51개
  android.permission.ACCESS_ADSERVICES_AD_ID
  ...
```

권한 목록은 매니페스트를 그대로 옮긴 것이 아니라 **Play가 이 기기에 맞게 가공한 목록**입니다. 매니페스트에서 `maxSdkVersion`이 기기 SDK보다 낮은 권한은 빠지고, 암묵적 권한은 더해집니다. 예를 들어 YouTube는 매니페스트에 50개가 있고, `WRITE_EXTERNAL_STORAGE` 때문에 자동으로 붙는 `READ_EXTERNAL_STORAGE`가 더해져 51개입니다.

### cert

```bash
uv run python -m playstore cert com.google.android.youtube [--apk]
```

| | `cert` | `cert --apk` |
|---|---|---|
| 출처 | Play details 응답의 `certificateSet` | 실제 APK의 서명 블록 (v2/v3/v3.1) |
| 요청 | details 1회 | details, delivery(필요하면 purchase), APK 끝부분 Range 요청 몇 번 (YouTube: 3회, 80KB) |
| 유료 앱 | 됨 (구매 없이) | 안 됨 |
| 부작용 | 없음 | 계정 보관함에 없는 무료 앱이면 보관함에 추가됨 |

- 서명 해시만 필요하면 `cert`로 충분합니다. `--apk`는 Play가 알려 준 해시를 실제 배포 파일과 대조할 때 씁니다.
- 해시는 **인증서(DER)의 해시**입니다. 안드로이드 `PackageManager`, `apksigner`의 certificate digest와 같은 기준입니다.
- **인증서가 여러 개인 앱이 있고, details 목록과 APK 서명자가 꼭 같지는 않습니다.** YouTube 21.40.161의 경우:

  | 출처 | 인증서 SHA-1 |
  |---|---|
  | details `certificateSet` | `4ba713df…`, `24bb24c0…` |
  | APK v3 서명 (Android 7~12, SDK 24~32) | `24bb24c0…` |
  | APK v3.1 서명 (Android 13 이상, SDK 33~) | `f8456b1d…` (서명 키 교체: `24bb24c0…` → `f8456b1d…`) |

  안드로이드 13 이상 기기는 교체된 키(`f8456b1d…`)를 쓰는데, 이 값은 details 목록에 없습니다. 반대로 details에만 있는 `4ba713df…`는 APK 서명과 lineage 어디에서도 찾을 수 없었습니다. 그래서 기기에서 보이는 서명과 비교하려면 `--apk` 결과까지 함께 봐야 합니다.
- `--apk`는 v1(JAR) 서명만 있는 APK를 지원하지 않습니다.

### download

```bash
uv run python -m playstore download com.google.android.youtube [-v VERSION_CODE] [-o downloads]
```

```
YouTube 21.40.161 (1561301413) — 파일 4개, 139.0 MB → downloads/com.google.android.youtube-1561301413
  [1/4] base.apk (62.1 MB)  OK
  [2/4] split_config.arm64_v8a.apk (74.0 MB)  OK
  [3/4] split_config.ko.apk (0.3 MB)  OK
  [4/4] split_config.xxhdpi.apk (2.7 MB)  OK
설치: adb install-multiple downloads/com.google.android.youtube-1561301413/*.apk
```

- `downloads/<패키지>-<versionCode>/` 아래에 base APK, split APK 전부, OBB를 받습니다.
- 파일 이름은 기기의 `/data/app` 배치를 따릅니다 (`base.apk`, `split_<이름>.apk`). 그래서 출력에 나온 `adb install-multiple` 명령으로 바로 설치할 수 있습니다. OBB(`[main|patch].<versionCode>.<패키지>.obb`)는 기기의 `/sdcard/Android/obb/<패키지>/`에 넣습니다.
- 파일마다 Play가 알려 준 크기와 SHA-256(OBB는 SHA-1)을 대조합니다. 다 받지 못했거나 맞지 않는 파일은 남기지 않고, 이미 받아서 검증된 파일은 다시 받지 않습니다.
- `-v`로 과거 버전을 받을 수 있습니다 (Play에 남아 있는 버전만). 버전 코드는 직접 알아야 합니다. Play에 없는 버전이면 `versionCode N 의 다운로드 URL 이 없음`으로 실패합니다.
- 무료 앱만 받을 수 있습니다. 계정 보관함에 없으면 자동으로 추가(purchase)합니다.

## 저장 위치

| 경로 | 내용 |
|---|---|
| `~/.config/dottakstore/account.json` | 이메일, AAS 토큰 (권한 600) |
| `~/.config/dottakstore/device.json` | 기기 프로필, gsfId, checkin 토큰 |
| `~/.config/dottakstore/session.json` | Play 토큰, deviceConfigToken, dfeCookie |
| `~/.config/dottakstore/chrome-profile/` | 로그인용 Chrome 프로필 |
| `downloads/` | 받은 APK (git 제외) |

`PLAYSTORE_HOME` 환경 변수로 `~/.config/dottakstore` 위치를 바꿀 수 있습니다.

## 구조

```
playstore/
  __main__.py        CLI
  browser.py         로그인 1단계: Chrome(DevTools 프로토콜)으로 oauth_token 쿠키 획득
  auth.py            android.clients.google.com/auth: oauth_token → AAS → ac2dm / Play 토큰
  device.py          기기 프로필(.properties) → User-Agent, checkin 요청, 기기 구성
  checkin.py         가상 기기 등록 → gsfId
  session.py         Play 토큰 → 기기 구성 업로드 → 약관(toc)
  api.py             fdfe API: details, search, purchase, delivery
  download.py        delivery 응답 → 파일 목록, 다운로드와 검증
  apksig.py          APK 서명 블록(v2/v3/v3.1) 파서 (로컬 파일 / HTTP Range)
  store.py           계정·기기·세션 저장
  googleplay.proto   필요한 메시지만 둔 Play 프로토콜 정의 (googleplay_pb2.py 는 생성물)
  profiles/          기기 프로필
tests/               로컬 HTTP 대역으로 검증 (실제 구글 서버와 통신하지 않음)
```

## 개발

```bash
uv run pytest
```

proto를 고쳤으면 저장소 루트에서 다시 생성합니다 (protoc 28.x).

```bash
protoc -I. --python_out=. playstore/googleplay.proto
```

## 실제 응답으로 확인한 Play API 동작

참고 구현(gpapi, Aurora)에 없거나 다른 부분입니다.

- **서명 해시:** details 응답의 `AppDetails` 필드 22(`certificateSet`)에 인증서별 SHA-1·SHA-256이 base64url(패딩 없음)로 옵니다. 유료 앱이나 기기 비호환 앱도 마찬가지입니다. 다만 APK의 서명자 목록과 꼭 같지는 않습니다 ([cert](#cert) 참고).
- **delivery 해시:** delivery 응답의 파일 `sha1`/`sha256`도 base64url(패딩 없음)입니다.
- **없는 버전:** Play에 없는 versionCode로 delivery를 요청하면 오류 없이 `downloadUrl`만 빈 값으로 옵니다.
- **검색 User-Agent:** 최신 Play User-Agent로 `search`를 보내면 결과 묶음이 빈 채로 와서, 검색만 구버전 User-Agent로 보냅니다. 결과는 묶음의 다음 결과(`getCluster?…`)와 다음 묶음(`searchList?…`) 두 경로로 이어집니다.
- **권한 목록:** 매니페스트가 아니라 기기 SDK에 맞게 가공된 목록이 옵니다. `maxSdkVersion`으로 빠지는 권한이 있고, 암묵적 권한은 더해집니다.
- **다운로드 CDN:** 구글 CDN(`*.gvt1.com`)은 끝 기준 Range(`bytes=-N`)를 416으로 거절합니다. 시작과 끝을 모두 지정해야 합니다.
- **인증 요청 TLS:** `/auth`는 TLS ALPN을 보내거나 세션 티켓을 끄면 403 `BadAuthentication`을 줍니다 (gpsoauth와 같은 조정이 필요).

## 주의

- 연구·학습 목적의 프로젝트입니다. Google Play 비공식 API를 쓰므로 서비스 약관에 어긋날 수 있고, **계정이 제한될 수 있습니다.** 연구용 별도 계정을 쓰세요.
- AAS 토큰은 계정 비밀번호에 준하는 장기 토큰입니다. `account.json`이 유출되지 않게 하세요.
- 받은 APK는 각 개발자의 저작물입니다. 재배포하지 마세요.

## 참고

- [Aurora Store / gplayapi](https://gitlab.com/AuroraOSS/gplayapi): 요청 헤더, 검색, 기기 프로필
- [googleplay-api (gpapi)](https://github.com/NoMore201/googleplay-api): proto 필드 번호, checkin, delivery
- [gpsoauth](https://github.com/simon-weber/gpsoauth): `/auth` TLS 설정
- [APK Signature Scheme v2](https://source.android.com/docs/security/features/apksigning/v2)

`playstore/profiles/pixel_9a.properties`는 The Calyx Institute의 기기 프로필이며 GPL-3.0-or-later입니다.
