from __future__ import annotations

from functools import lru_cache
from io import BytesIO
import base64
import hashlib

from PIL import Image, ImageDraw, ImageFilter


THEMES = {
    "kimono": ((215, 64, 70), (247, 205, 153), (61, 88, 128)),
    "cat": ((238, 180, 83), (80, 143, 132), (245, 226, 183)),
    "lavender": ((119, 92, 172), (226, 210, 240), (74, 137, 106)),
    "bread": ((196, 128, 62), (244, 218, 171), (86, 93, 118)),
    "sakura": ((234, 137, 168), (245, 220, 226), (74, 115, 132)),
    "street": ((115, 139, 152), (218, 207, 183), (108, 81, 63)),
}


def image_data_uri(seed: str, subject: str = "") -> str:
    return "data:image/png;base64," + base64.b64encode(image_bytes(seed, subject)).decode("ascii")


@lru_cache(maxsize=128)
def image_bytes(seed: str, subject: str = "") -> bytes:
    key = (seed or subject or "puzzle").lower()
    colors = _theme_colors(key)
    width, height = 360, 240
    image = Image.new("RGB", (width, height), colors[1])
    draw = ImageDraw.Draw(image)
    for y in range(height):
        ratio = y / max(height - 1, 1)
        fill = tuple(int(colors[1][i] * (1 - ratio) + colors[0][i] * ratio) for i in range(3))
        draw.line((0, y, width, y), fill=fill)

    draw.ellipse((-70, 120, 150, 310), fill=_mix(colors[2], (255, 255, 255), 0.16))
    draw.rectangle((205, 54, 350, 188), fill=_mix(colors[2], colors[1], 0.22))
    draw.polygon(((205, 54), (276, 18), (350, 54)), fill=_mix(colors[0], (255, 255, 255), 0.1))
    draw.ellipse((92, 52, 232, 196), fill=_mix(colors[0], (255, 255, 255), 0.2))
    draw.ellipse((126, 76, 198, 148), fill=_mix(colors[1], (255, 255, 255), 0.28))
    draw.rectangle((0, 184, width, height), fill=_mix(colors[2], (255, 255, 255), 0.08))

    for x in range(0, width, 45):
        draw.line((x, 0, x, height), fill=(255, 255, 255), width=1)
    for y in range(0, height, 40):
        draw.line((0, y, width, y), fill=(255, 255, 255), width=1)
    for x, y in _motif_points(key):
        draw.ellipse((x - 10, y - 10, x + 10, y + 10), fill=_mix(colors[0], (255, 255, 255), 0.35))

    image = image.filter(ImageFilter.UnsharpMask(radius=1, percent=120, threshold=3))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _theme_colors(key: str) -> tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]:
    for name, colors in THEMES.items():
        if name in key:
            return colors
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return (
        (120 + digest[0] % 100, 70 + digest[1] % 120, 70 + digest[2] % 120),
        (190 + digest[3] % 50, 180 + digest[4] % 55, 150 + digest[5] % 70),
        (55 + digest[6] % 120, 75 + digest[7] % 110, 85 + digest[8] % 100),
    )


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], ratio: float) -> tuple[int, int, int]:
    return tuple(int(a[index] * (1 - ratio) + b[index] * ratio) for index in range(3))


def _motif_points(key: str) -> tuple[tuple[int, int], ...]:
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return tuple((28 + digest[index] % 304, 32 + digest[index + 1] % 168) for index in range(0, 10, 2))
