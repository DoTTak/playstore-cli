"""Background for playstore-cli.tape (VHS MarginFill): the banner's dark teal, a dot grid, and a glowing
border around the terminal window. Pure Python, no dependencies.

    python3 .github/vhs/background.py .github/vhs/background.png
"""
import math
import struct
import sys
import zlib

W, H = 1920, 1080
# Terminal window as VHS draws it with Margin 56 (measured): x 56..1863, y 56..1023.
X0, Y0, X1, Y1, RADIUS = 56, 56, 1864, 1024, 9

STOPS = [(0.0, (7, 62, 56)), (0.45, (2, 21, 28)), (1.0, (3, 47, 54))]  # top-left → bottom-right
MINT = (21, 255, 208)
BORDER = (46, 128, 118)


def lerp(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def gradient(t):
    for (t0, c0), (t1, c1) in zip(STOPS, STOPS[1:]):
        if t <= t1:
            return lerp(c0, c1, (t - t0) / (t1 - t0))
    return STOPS[-1][1]


def window_distance(x, y):
    """Signed distance to the rounded window rectangle (negative inside)."""
    cx, cy = (X0 + X1) / 2, (Y0 + Y1) / 2
    hx, hy = (X1 - X0) / 2 - RADIUS, (Y1 - Y0) / 2 - RADIUS
    dx, dy = abs(x - cx) - hx, abs(y - cy) - hy
    return math.hypot(max(dx, 0), max(dy, 0)) + min(max(dx, dy), 0) - RADIUS


def pixel(x, y):
    c = gradient((x / W + y / H) / 2)
    # soft light from the top-left and bottom-right corners, like the banner
    for gx, gy, color, reach, strength in ((0, 0, (14, 110, 96), 520, 0.55), (W, H, (8, 96, 104), 520, 0.45)):
        k = strength * math.exp(-((x - gx) ** 2 + (y - gy) ** 2) / reach ** 2)
        c = lerp(c, color, k)
    # dot grid, fading toward the middle
    if x % 24 < 2 and y % 24 < 2:
        fade = max(math.exp(-(x ** 2 + y ** 2) / 900 ** 2), math.exp(-((W - x) ** 2 + (H - y) ** 2) / 900 ** 2))
        c = lerp(c, (60, 160, 150), 0.35 * fade + 0.06)
    d = window_distance(x + 0.5, y + 0.5)
    if d > 0:
        c = lerp(c, MINT, 0.22 * math.exp(-d / 14))   # glow
    c = lerp(c, BORDER, max(0.0, 1 - abs(d - 0.5) / 1.2))  # 1px border, antialiased
    return bytes(max(0, min(255, round(v))) for v in c)


def png(path):
    rows = b"".join(b"\0" + b"".join(pixel(x, y) for x in range(W)) for y in range(H))
    chunk = lambda kind, data: struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


png(sys.argv[1])
