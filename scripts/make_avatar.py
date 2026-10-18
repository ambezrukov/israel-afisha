"""Аватарка бота: билет с нотой и звездой на тёплом фоне, внизу — волны (Хайфа у моря).

python scripts/make_avatar.py  →  assets/bot-avatar.png (1024×1024)
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

S = 2048  # рисуем крупно и уменьшаем — так края получаются гладкими
OUT = Path(__file__).resolve().parent.parent / "assets" / "bot-avatar.png"

CORAL = (232, 88, 52)
DEEP = (150, 38, 40)
CREAM = (255, 244, 228)
INK = (60, 26, 30)
GOLD = (255, 196, 92)


def radial_background() -> Image.Image:
    bg = Image.new("RGB", (S, S))
    px = bg.load()
    cx, cy = S * 0.42, S * 0.36
    rmax = math.hypot(S, S) * 0.62
    for y in range(S):
        for x in range(S):
            t = min(1.0, math.hypot(x - cx, y - cy) / rmax)
            t = t ** 1.15
            px[x, y] = tuple(int(CORAL[i] * (1 - t) + DEEP[i] * t) for i in range(3))
    return bg


def waves(img: Image.Image) -> None:
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for k, (alpha, base) in enumerate([(70, 0.80), (110, 0.87), (160, 0.94)]):
        pts = []
        for x in range(-20, S + 21, 8):
            y = S * base + math.sin(x / S * math.pi * 4 + k * 1.3) * S * 0.018
            pts.append((x, y))
        pts += [(S + 20, S + 20), (-20, S + 20)]
        d.polygon(pts, fill=(255, 255, 255, alpha // 3) if k < 2 else (110, 20, 30, alpha))
    img.alpha_composite(layer)


def ticket(img: Image.Image) -> None:
    w, h = int(S * 0.70), int(S * 0.40)
    t = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(t)
    r = int(h * 0.12)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=CREAM)
    # вырезы-полукруги по линии отрыва
    cut_x = int(w * 0.70)
    notch = int(h * 0.11)
    for cy in (0, h):
        d.ellipse([cut_x - notch, cy - notch, cut_x + notch, cy + notch], fill=(0, 0, 0, 0))
    # перфорация
    for y in range(int(h * 0.16), int(h * 0.86), int(h * 0.075)):
        d.ellipse([cut_x - 9, y - 9, cut_x + 9, y + 9], fill=(225, 205, 185, 255))
    # нота (две восьмые) на основной части
    nx, ny = int(w * 0.22), int(h * 0.66)
    rx, ry = int(h * 0.13), int(h * 0.095)
    gap = int(w * 0.20)
    stem = int(h * 0.42)
    lw = int(h * 0.05)
    for dx in (0, gap):
        d.ellipse([nx + dx - rx, ny - ry, nx + dx + rx, ny + ry], fill=INK)
        d.rectangle([nx + dx + rx - lw, ny - stem, nx + dx + rx, ny], fill=INK)
    d.polygon([
        (nx + rx - lw, ny - stem), (nx + gap + rx, ny - stem - int(h * 0.06)),
        (nx + gap + rx, ny - stem + int(h * 0.07)), (nx + rx - lw, ny - stem + int(h * 0.13)),
    ], fill=INK)
    # звезда на корешке
    sx, sy, R = int(w * 0.85), int(h * 0.5), int(h * 0.19)
    star = []
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        rr = R if i % 2 == 0 else R * 0.45
        star.append((sx + rr * math.cos(a), sy + rr * math.sin(a)))
    d.polygon(star, fill=GOLD)

    t = t.rotate(-11, resample=Image.BICUBIC, expand=True)
    shadow = Image.new("RGBA", t.size, (0, 0, 0, 0))
    shadow.paste((40, 0, 10, 110), mask=t.split()[3])
    shadow = shadow.filter(ImageFilter.GaussianBlur(S * 0.02))
    ox, oy = (S - t.width) // 2, int(S * 0.47 - t.height / 2)
    img.alpha_composite(shadow, (ox + int(S * 0.012), oy + int(S * 0.03)))
    img.alpha_composite(t, (ox, oy))


def sparkles(img: Image.Image) -> None:
    d = ImageDraw.Draw(img)
    for cx, cy, r in [(0.20, 0.20, 0.035), (0.80, 0.17, 0.025), (0.86, 0.66, 0.02), (0.13, 0.62, 0.018)]:
        x, y, rr = S * cx, S * cy, S * r
        d.polygon([(x, y - rr), (x + rr * 0.25, y - rr * 0.25), (x + rr, y), (x + rr * 0.25, y + rr * 0.25),
                   (x, y + rr), (x - rr * 0.25, y + rr * 0.25), (x - rr, y), (x - rr * 0.25, y - rr * 0.25)],
                  fill=(255, 236, 200, 230))


def main() -> None:
    img = radial_background().convert("RGBA")
    waves(img)
    sparkles(img)
    ticket(img)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.resize((1024, 1024), Image.LANCZOS).convert("RGB").save(OUT, optimize=True)
    print(OUT)


if __name__ == "__main__":
    main()
