#!/usr/bin/env python3
"""Generate the site's Open Graph / Twitter card images.

The site ships no bitmaps at all, so every share on X, WhatsApp, Slack, Discord,
Facebook and LinkedIn rendered as a bare blue link, and Google Discover had no
large image to promote. These cards give every page a branded 1200x630 preview.

Design follows the visual system in style.css: ink background, the fold seam
running down the card, amber on the warm side and violet on the cool side.

Run:  python scripts/build_og.py         # writes og/*.png
      python scripts/build_og.py --check # verify every page's card exists
"""

import argparse
import io
import os
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_DIR = os.path.join(ROOT, "fonts")
OUT_DIR = os.path.join(ROOT, "og")

W, H = 1200, 630

INK = (10, 11, 18)
INK_2 = (18, 20, 31)
WHITE = (255, 255, 255)
MUTED = (154, 162, 182)
AMBER = (255, 122, 26)
VIOLET = (139, 123, 255)

# slug -> (title, kicker). Title is what a human should read in the feed; it is
# deliberately shorter and punchier than the <title> tag, which has to carry
# keywords for the SERP.
CARDS = {
    "default": (
        "Foldable phones,\ntracked daily",
        "iPhone Fold · Galaxy Z Fold 7 · Pixel 10 Pro Fold",
    ),
    "iphone-fold": (
        "iPhone Fold:\neverything we know",
        "Release, price, specs — every rumor attributed",
    ),
    "iphone-fold-release-date": (
        "When does the\niPhone Fold land?",
        "Expected September 2026, with the iPhone 18",
    ),
    "iphone-fold-price": (
        "How much will the\niPhone Fold cost?",
        "~$1,999 expected — plus UK and Germany estimates",
    ),
    "iphone-fold-vs-galaxy-z-fold": (
        "iPhone Fold vs\nGalaxy Z Fold 7",
        "Wait for Apple, or buy Samsung today?",
    ),
    "best-foldable-phones": (
        "The best foldables\nyou can buy now",
        "Z Fold 7, Pixel 10 Pro Fold, Honor Magic V5",
    ),
    "which-foldable": (
        "Which foldable\nfits you?",
        "Five questions, thirty seconds, one answer",
    ),
    "news": (
        "Foldable news,\nrebuilt every day",
        "The day's stories, written up and credited",
    ),
    "about": (
        "Independent.\nRumors labeled.",
        "How FoldRadar reports on unreleased hardware",
    ),
    "de": (
        "Faltbare Handys,\ntäglich verfolgt",
        "iPhone Fold · Galaxy Z Fold 7 · Pixel 10 Pro Fold",
    ),
    "de-iphone-fold": (
        "iPhone Fold:\nalle Infos",
        "Release, Preis in Deutschland, Specs",
    ),
}


_FONT_CACHE = {}


def load_font(woff2_name, size, weight):
    """Pillow cannot read woff2, so decompress to an in-memory TTF first.

    These are variable fonts whose default instance is Light; the browser picks
    the weight off the wght axis, but Pillow renders the default. Pin the axis
    explicitly or every card comes out thin.
    """
    key = (woff2_name, size, weight)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]

    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer

    tt = TTFont(os.path.join(FONT_DIR, woff2_name))
    axes = {a.axisTag: (a.minValue, a.maxValue) for a in tt["fvar"].axes}
    lo, hi = axes["wght"]
    tt = instancer.instantiateVariableFont(tt, {"wght": max(lo, min(hi, weight))})
    tt.flavor = None
    buf = io.BytesIO()
    tt.save(buf)
    buf.seek(0)
    _FONT_CACHE[key] = ImageFont.truetype(buf, size)
    return _FONT_CACHE[key]


def text_tracked(draw, xy, text, font, fill, tracking=0):
    """Draw text with letter-spacing; Pillow has no tracking of its own."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking
    return x


def backdrop():
    img = Image.new("RGB", (W, H), INK)
    d = ImageDraw.Draw(img)

    # vertical wash so the card is not a flat rectangle
    for y in range(H):
        t = y / H
        d.line(
            [(0, y), (W, y)],
            fill=tuple(int(INK_2[i] + (INK[i] - INK_2[i]) * t) for i in range(3)),
        )

    # duotone glows: amber low-left, violet high-right, blurred into the ink
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    g = ImageDraw.Draw(glow)
    g.ellipse([-260, 300, 560, 900], fill=(90, 42, 8))
    g.ellipse([720, -240, 1420, 380], fill=(48, 42, 104))
    glow = glow.filter(ImageFilter.GaussianBlur(150))
    img = Image.blend(img, Image.blend(img, glow, 0.9), 0.55)

    d = ImageDraw.Draw(img)

    # the signature: two panels meeting at a hinge, seam lit amber -> violet
    seam_x = 812
    for y in range(0, H):
        t = y / H
        col = tuple(int(AMBER[i] + (VIOLET[i] - AMBER[i]) * t) for i in range(3))
        d.line([(seam_x, y), (seam_x + 2, y)], fill=col)

    # soft bloom either side of the seam
    bloom = Image.new("RGB", (W, H), (0, 0, 0))
    b = ImageDraw.Draw(bloom)
    for y in range(0, H):
        t = y / H
        col = tuple(int((AMBER[i] + (VIOLET[i] - AMBER[i]) * t) * 0.5) for i in range(3))
        b.line([(seam_x - 5, y), (seam_x + 7, y)], fill=col)
    bloom = bloom.filter(ImageFilter.GaussianBlur(22))
    img = Image.blend(img, Image.blend(img, bloom, 0.85), 0.5)

    d = ImageDraw.Draw(img)
    # hinge notches, so the seam reads as a fold and not a divider rule
    for cy in (H // 2 - 58, H // 2 + 58):
        d.rounded_rectangle([seam_x - 4, cy - 20, seam_x + 6, cy + 20], 5, fill=INK)
    return img


def wrap(draw, text, font, max_w):
    lines = []
    for para in text.split("\n"):
        words, cur = para.split(), ""
        for word in words:
            trial = f"{cur} {word}".strip()
            if draw.textlength(trial, font=font) <= max_w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return lines


def render(slug, title, kicker):
    img = backdrop()
    d = ImageDraw.Draw(img)

    left, max_w = 76, 700

    # wordmark, with the fold glyph
    mark = load_font("SpaceGrotesk-var.woff2", 27, 700)
    d.polygon([(left, 74), (left + 17, 62), (left + 17, 94), (left, 106)], fill=AMBER)
    d.polygon([(left + 21, 62), (left + 38, 74), (left + 38, 106), (left + 21, 94)],
              fill=VIOLET)
    text_tracked(d, (left + 54, 70), "FOLDRADAR", mark, WHITE, tracking=3.2)

    # title, shrunk until it fits in three lines
    for size in (68, 62, 56, 50, 45):
        tf = load_font("SpaceGrotesk-var.woff2", size, 700)
        lines = wrap(d, title, tf, max_w)
        if len(lines) <= 3:
            break
    lh = int(size * 1.14)
    y = 232 - (len(lines) - 2) * lh // 2
    for line in lines:
        d.text((left, y), line, font=tf, fill=WHITE)
        y += lh

    # kicker
    kf = load_font("Inter-var.woff2", 26, 400)
    y += 18
    for line in wrap(d, kicker, kf, max_w)[:2]:
        d.text((left, y), line, font=kf, fill=MUTED)
        y += 36

    # footer domain, on the amber side of the duotone
    df = load_font("Inter-var.woff2", 24, 500)
    text_tracked(d, (left, H - 78), "foldradar.com", df, AMBER, tracking=0.6)

    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{slug}.png")
    img.save(path, "PNG", optimize=True)
    return path


def render_logo():
    """Square mark for schema.org Organization.logo and apple-touch-icon.

    Google will not show a publisher logo in rich results from an SVG favicon,
    which was the only mark the site had.
    """
    S = 512
    img = Image.new("RGB", (S, S), INK)
    d = ImageDraw.Draw(img)

    glow = Image.new("RGB", (S, S), (0, 0, 0))
    ImageDraw.Draw(glow).ellipse([-120, 120, 300, 620], fill=(95, 45, 9))
    ImageDraw.Draw(glow).ellipse([250, -110, 640, 300], fill=(52, 45, 110))
    img = Image.blend(img, Image.blend(img, glow.filter(ImageFilter.GaussianBlur(90)), 0.9), 0.6)

    d = ImageDraw.Draw(img)
    # the fold glyph, scaled to fill the square
    cx, top, bot, half = S // 2, 128, 384, 104
    d.polygon([(cx - half, top + 34), (cx - 6, top), (cx - 6, bot), (cx - half, bot + 34)],
              fill=AMBER)
    d.polygon([(cx + 6, top), (cx + half, top + 34), (cx + half, bot + 34), (cx + 6, bot)],
              fill=VIOLET)

    path = os.path.join(OUT_DIR, "logo.png")
    os.makedirs(OUT_DIR, exist_ok=True)
    img.save(path, "PNG", optimize=True)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="verify each card exists instead of rebuilding")
    ap.add_argument("--only", help="rebuild a single slug")
    args = ap.parse_args()

    if args.check:
        missing = [f"{s}.png" for s in CARDS
                   if not os.path.exists(os.path.join(OUT_DIR, f"{s}.png"))]
        if not os.path.exists(os.path.join(OUT_DIR, "logo.png")):
            missing.append("logo.png")
        if missing:
            print("missing OG images: " + ", ".join(missing))
            return 1
        print(f"all {len(CARDS)} OG cards + logo present")
        return 0

    slugs = [args.only] if args.only else list(CARDS)
    for slug in slugs:
        title, kicker = CARDS[slug]
        path = render(slug, title, kicker)
        print(f"{os.path.getsize(path):>7,} B  {os.path.relpath(path, ROOT)}")
    if not args.only:
        path = render_logo()
        print(f"{os.path.getsize(path):>7,} B  {os.path.relpath(path, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
