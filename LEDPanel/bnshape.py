"""
bnshape.py - Bangla text -> grayscale bitmap, WITHOUT Pillow's Raqm.

Pillow's Windows wheels do not bundle Raqm, so PIL.ImageDraw.text() cannot
shape Bangla there (conjuncts break). This module does the job directly:

    uharfbuzz   - OpenType shaping: ক + ্ + ষ  ->  the ক্ষ ligature glyph,
                  plus correct positioning of matras
    freetype-py - rasterises each shaped glyph by glyph-ID

Both ship self-contained wheels for Windows/macOS/Linux with no external
DLLs, so this path works everywhere:

    pip install uharfbuzz freetype-py
"""
import uharfbuzz as hb
import freetype


def available():
    return True


def render(text, font_path, px):
    """Shape + rasterise `text`. Returns (pixels, width, height) as a
    row-major bytearray of 8-bit intensity, tightly cropped."""

    # ---- 1. shape with HarfBuzz ----
    with open(font_path, "rb") as fh:
        data = fh.read()
    face = hb.Face(data)
    hbfont = hb.Font(face)
    hbfont.scale = (px * 64, px * 64)          # 26.6 fixed point
    hb.ot_font_set_funcs(hbfont)

    buf = hb.Buffer()
    buf.add_str(text)
    buf.direction = "ltr"
    buf.script = "Beng"
    buf.language = "bn"
    hb.shape(hbfont, buf)

    infos = buf.glyph_infos
    poss = buf.glyph_positions

    # ---- 2. rasterise each glyph with FreeType ----
    ft = freetype.Face(font_path)
    ft.set_pixel_sizes(0, px)

    margin = px * 2
    total_adv = sum(p.x_advance for p in poss) / 64.0
    W = int(total_adv) + margin * 2
    H = px * 4
    baseline = int(H * 0.68)
    canvas = bytearray(W * H)

    penx = float(margin)
    peny = float(baseline)
    for info, pos in zip(infos, poss):
        ft.load_glyph(info.codepoint, freetype.FT_LOAD_RENDER)
        bm = ft.glyph.bitmap
        gx = int(round(penx + pos.x_offset / 64.0)) + ft.glyph.bitmap_left
        gy = int(round(peny - pos.y_offset / 64.0)) - ft.glyph.bitmap_top

        for r in range(bm.rows):
            ty = gy + r
            if ty < 0 or ty >= H:
                continue
            base = r * bm.pitch
            for c in range(bm.width):
                tx = gx + c
                if tx < 0 or tx >= W:
                    continue
                v = bm.buffer[base + c]
                if v:
                    i = ty * W + tx
                    if v > canvas[i]:
                        canvas[i] = v          # max-blend overlapping marks

        penx += pos.x_advance / 64.0
        peny -= pos.y_advance / 64.0

    # ---- 3. tight crop ----
    minx, miny, maxx, maxy = W, H, -1, -1
    for y in range(H):
        row = canvas[y * W:(y + 1) * W]
        if not any(row):
            continue
        if y < miny: miny = y
        if y > maxy: maxy = y
        for x in range(W):
            if row[x]:
                if x < minx: minx = x
                if x > maxx: maxx = x
    if maxx < 0:
        return bytearray(), 0, 0

    cw, ch = maxx - minx + 1, maxy - miny + 1
    out = bytearray(cw * ch)
    for y in range(ch):
        src = (miny + y) * W + minx
        out[y * cw:(y + 1) * cw] = canvas[src:src + cw]
    return out, cw, ch
