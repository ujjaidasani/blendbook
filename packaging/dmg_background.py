"""Draws the Mac installer window's background: packaging/dmg-background.tiff (1x + 2x in one file).

Run it again after changing the look: .venv/bin/python packaging/dmg_background.py
The icon positions here must match packaging/dmg_settings.py.
"""
import os
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 640, 400                      # window content size, in points
APP, APPS = (170, 196), (470, 196)   # icon centres, shared with dmg_settings.py
BG = (7, 8, 11)
DECK1, DECK2 = (90, 200, 250), (255, 159, 10)
MUTED = (141, 147, 160)


def font(size, points, weight=500):
    for path in ("/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/HelveticaNeue.ttc"):
        if os.path.exists(path):
            f = ImageFont.truetype(path, size)
            try:  # SF's axes: width, optical size (in points), grade, weight
                f.set_variation_by_axes([100, max(17, min(96, points)), 400, weight])
            except Exception:
                pass
            return f
    return ImageFont.load_default(size)


def mix(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def draw(scale):
    s = scale
    img = Image.new("RGB", (W * s, H * s), BG)

    # two soft deck-coloured glows behind the icons, like the landing page
    glow = Image.new("RGB", img.size, BG)
    g = ImageDraw.Draw(glow)
    for (cx, cy), col in ((APP, DECK1), (APPS, DECK2)):
        r = 120 * s
        g.ellipse((cx * s - r, cy * s - r, cx * s + r, cy * s + r), fill=mix(BG, col, 0.16))
    img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(70 * s)), 1.0)
    d = ImageDraw.Draw(img)

    # the arrow: a blue-to-orange line, deck 1 to deck 2
    x0, x1, y = (APP[0] + 82) * s, (APPS[0] - 82) * s, APP[1] * s
    w = max(2, round(3 * s))
    for x in range(x0, x1):
        d.line((x, y - w // 2, x, y + w - w // 2 - 1), fill=mix(DECK1, DECK2, (x - x0) / (x1 - x0)))
    head = 11 * s
    d.line((x1 - head, y - head, x1, y), fill=DECK2, width=w, joint="curve")
    d.line((x1 - head, y + head, x1, y), fill=DECK2, width=w, joint="curve")
    for px, py in ((x1, y), (x1 - head, y - head), (x1 - head, y + head)):
        d.ellipse((px - w / 2, py - w / 2, px + w / 2, py + w / 2), fill=DECK2)

    # one line of instruction under the icons and their names
    text = "Drag Blendbook into Applications"
    f = font(14 * s, 14)
    tw = d.textlength(text, font=f)
    d.text(((W * s - tw) / 2, 318 * s), text, font=f, fill=MUTED)
    return img


def main():
    out = os.path.join(HERE, "dmg-background.tiff")
    with tempfile.TemporaryDirectory() as tmp:
        one, two = os.path.join(tmp, "bg.png"), os.path.join(tmp, "bg@2x.png")
        draw(1).save(one, dpi=(72, 72))
        draw(2).save(two, dpi=(144, 144))
        subprocess.run(["tiffutil", "-cathidpicheck", one, two, "-out", out], check=True)
    print("Wrote", out)


if __name__ == "__main__":
    main()
