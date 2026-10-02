#!/usr/bin/env python3
"""
findfont.py - find every font on this PC that can actually render Bangla.

Windows Explorer shows font FAMILY names ("Nirmala UI"), not filenames,
so you cannot tell from the Fonts window what file to point at. This
script opens each font file and tests it for real:

  1. does it have a glyph for  ক  (U+0995) ?
  2. does it form the conjunct ক্ষ  (shaping test) ?

Run:   python findfont.py
"""
import os, glob, sys

try:
    from PIL import Image, ImageDraw, ImageFont, features
except ImportError:
    sys.exit("Pillow missing.  Run:  pip install pillow")

SEARCH_DIRS = [
    r"C:\Windows\Fonts",
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Windows\Fonts"),
    "/System/Library/Fonts", "/System/Library/Fonts/Supplemental", "/Library/Fonts",
    os.path.expanduser("~/Library/Fonts"),
    "/usr/share/fonts", os.path.expanduser("~/.fonts"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts"),
]

def render(font, text):
    img = Image.new("L", (300, 120), 0)
    ImageDraw.Draw(img).text((20, 20), text, font=font, fill=255, language="bn")
    bb = img.getbbox()
    return img.crop(bb) if bb else None

def test(path):
    """-> (has_bengali, shapes_conjunct, note)"""
    try:
        f = ImageFont.truetype(path, 40)
    except Exception as e:
        return None
    try:
        missing = render(f, "\ue000")          # private use - almost never mapped
        ka      = render(f, "ক")
        if ka is None:
            return (False, False, "no glyph")
        if missing is not None and ka.size == missing.size and ka.tobytes() == missing.tobytes():
            return (False, False, "renders as .notdef box")
        kha = render(f, "ষ")
        conj = render(f, "ক্ষ")
        shaped = False
        if conj and kha:
            # a real conjunct is narrower than the two letters side by side
            shaped = conj.width < (ka.width + kha.width) * 0.92
        return (True, shaped, "")
    except Exception as e:
        return None

print("HarfBuzz shaping in Pillow:", "YES" if features.check("raqm") else "NO  <-- fix this first")
print()

seen, good = set(), []
for d in SEARCH_DIRS:
    if not d or not os.path.isdir(d):
        continue
    for ext in ("ttf", "TTF", "ttc", "TTC", "otf", "OTF"):
        for p in glob.glob(os.path.join(d, "**", f"*.{ext}"), recursive=True):
            rp = os.path.realpath(p)
            if rp in seen:
                continue
            seen.add(rp)
            r = test(p)
            if r and r[0]:
                good.append((p, r[1]))

print(f"scanned {len(seen)} font files\n")
if not good:
    print("NO Bangla-capable font found on this PC.")
    print("Fix: put Lohit-Bengali.ttf into a 'fonts' folder next to this script.")
    sys.exit(1)

good.sort(key=lambda t: (not t[1], t[0]))
print("Bangla-capable fonts (conjunct-forming ones first):\n")
for p, shaped in good:
    print(f"  [{'CONJUNCTS OK' if shaped else 'no conjuncts'}]  {p}")

best = good[0][0]
print("\nUse this one:")
print(f'  python txt2bin.py --font "{best}"')
print("\nOr just drop Lohit-Bengali.ttf into a 'fonts' folder next to the script")
print("and txt2bin.py will pick it up automatically with no --font needed.")
