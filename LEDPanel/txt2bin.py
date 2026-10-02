#!/usr/bin/env python3
"""
txt2bin.py  --  notice.txt (Bangla, UTF-8)  ->  data/notice.bin

Runs on YOUR PC, not on the ESP32.

TEXT ENGINES, in order of preference:
  1. harfbuzz : uharfbuzz + freetype-py  (pip install uharfbuzz freetype-py)
                Works on every OS. Pillow's Windows wheels do NOT bundle
                Raqm, so on Windows this is normally the only working path.
  2. pillow   : PIL's own text layout, only if it reports Raqm support.

    python txt2bin.py --check       verify the setup
    python txt2bin.py               convert notice.txt
    python txt2bin.py mymsg.txt     convert some other file

notice.bin format (little-endian):
    0 : 'B','N','1',0
    4 : uint16 width
    6 : uint16 height
    8 : uint8 pixels[width*height]   row-major, 8-bit intensity
"""
import argparse, struct, shutil, os, sys, glob

try:
    from PIL import Image, features
except ImportError:
    sys.exit("Pillow is missing.  Run:  pip install pillow")

# ------------------------------------------------------------ engines
ENGINE = None
try:
    import bnshape                       # uharfbuzz + freetype-py
    ENGINE = "harfbuzz"
except Exception:
    if features.check("raqm"):
        ENGINE = "pillow"

def render_text(msg, font, px):
    """-> PIL 'L' image, tightly cropped, or None."""
    if ENGINE == "harfbuzz":
        data, w, h = bnshape.render(msg, font, px)
        if not w:
            return None
        return Image.frombytes("L", (w, h), bytes(data))
    if ENGINE == "pillow":
        from PIL import ImageDraw, ImageFont
        f = ImageFont.truetype(font, px)
        img = Image.new("L", (8000, 300), 0)
        ImageDraw.Draw(img).text((50, 100), msg, font=f, fill=255, language="bn")
        bb = img.getbbox()
        return img.crop(bb) if bb else None
    return None

# ------------------------------------------------------------ fonts
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\Nirmala.ttf",
    r"C:\Windows\Fonts\NirmalaB.ttf",
    r"C:\Windows\Fonts\NirmalaS.ttf",
    r"C:\Windows\Fonts\Shonar.ttf",
    r"C:\Windows\Fonts\vrinda.ttf",
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Windows\Fonts\Nirmala.ttf"),
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Windows\Fonts\Lohit-Bengali.ttf"),
    "/System/Library/Fonts/Supplemental/Bangla Sangam MN.ttc",
    "/System/Library/Fonts/Supplemental/Bangla MN.ttc",
    "/usr/share/fonts/truetype/lohit-bengali/Lohit-Bengali.ttf",
    "/usr/share/fonts/truetype/fonts-beng-extra/Muktibold.ttf",
]

def find_font(explicit=None):
    if explicit:
        if not os.path.exists(explicit):
            sys.exit(f"font not found: {explicit}")
        return explicit
    here = os.path.dirname(os.path.abspath(__file__))
    for pat in ("*.ttf", "*.TTF", "*.ttc", "*.otf"):
        hits = sorted(glob.glob(os.path.join(here, "fonts", pat)))
        if hits:
            return hits[0]
    for p in FONT_CANDIDATES:
        if p and os.path.exists(p):
            return p
    sys.exit(
        "No Bangla font found.\n\n"
        "Put Lohit-Bengali.ttf here:\n"
        f"    {os.path.join(here, 'fonts', 'Lohit-Bengali.ttf')}\n"
        "or run  python findfont.py  and pass one with  --font \"<path>\"."
    )

# ------------------------------------------------------------ main
ap = argparse.ArgumentParser()
ap.add_argument("txt", nargs="?", default="notice.txt",
                help="notice.txt (default) or timeup.txt")
ap.add_argument("--outdir", default="data")
ap.add_argument("--font", default=None)
ap.add_argument("--band", type=int, default=18)
ap.add_argument("--name", default=None,
                help="output .bin name; default follows the input .txt")
ap.add_argument("--check", action="store_true")
a = ap.parse_args()

FONT = find_font(a.font)

if a.check:
    import PIL
    print("Pillow      :", PIL.__version__)
    print("Bangla font :", FONT)
    print("Text engine :", ENGINE or "NONE  <-- PROBLEM")
    if ENGINE == "harfbuzz":
        print("              uharfbuzz + freetype-py  (recommended)")
    elif ENGINE == "pillow":
        print("              Pillow/Raqm")
    else:
        print("\nNo working text engine. Bangla cannot be shaped.")
        print("Fix (works on Windows, macOS and Linux):")
        print("    pip install uharfbuzz freetype-py")
        sys.exit(1)
    # prove it really shapes a conjunct
    im = render_text("ক্ষ", FONT, 40)
    print("Conjunct test ক্ষ :", f"{im.width}x{im.height} px OK" if im else "FAILED")
    sys.exit(0)

if ENGINE is None:
    sys.exit("No text engine.  Run:  pip install uharfbuzz freetype-py")
if not os.path.exists(a.txt):
    sys.exit(f"{a.txt} not found. Create it (UTF-8) with your Bangla text.")

raw = open(a.txt, encoding="utf-8").read()
lines = [l.strip() for l in raw.splitlines() if l.strip() and not l.startswith("#")]
msg = "   -   ".join(lines)
if not msg:
    sys.exit(f"{a.txt} has no usable text (blank, or only # comment lines)")

best = None
for px in range(8, 60):
    im = render_text(msg, FONT, px)
    if im and im.height <= a.band:
        best = (px, im)
    else:
        break
if not best:
    sys.exit("could not fit the text into the band height")
px, im = best

strip = Image.new("L", (im.width, a.band), 0)
strip.paste(im, (0, (a.band - im.height) // 2))

os.makedirs(a.outdir, exist_ok=True)
outname = a.name or (os.path.splitext(os.path.basename(a.txt))[0] + ".bin")
path = os.path.join(a.outdir, outname)
with open(path, "wb") as fh:
    fh.write(b"BN1\0")
    fh.write(struct.pack("<HH", strip.width, strip.height))
    fh.write(strip.tobytes())
shutil.copy(a.txt, os.path.join(a.outdir, os.path.basename(a.txt)))
preview = os.path.splitext(outname)[0] + "_preview.png"
strip.resize((strip.width * 3, strip.height * 3), Image.NEAREST).save(preview)

print(f'text    : "{msg}"')
print(f"engine  : {ENGINE}")
print(f"font    : {os.path.basename(FONT)} @ {px}px")
print(f"render  : {strip.width} x {strip.height}")
print(f"written : {path}  ({8 + strip.width*strip.height} bytes)")
print(f"preview : {preview}   <-- OPEN THIS and check the Bangla")
print("\nnext:  python send_notice.py COM3      (sends both bitmaps)")
