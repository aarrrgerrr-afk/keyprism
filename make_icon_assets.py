"""Regenerate KeyPrism icon assets from the original logo artwork.

The crystal logo's violet (left) half is much darker than its cyan (right)
half, so at small sizes (taskbar/title-bar 16-24px) the violet collapses
into the background and the icon reads as a lone cyan "C".  This script
lifts the luminance of violet/magenta pixels so both halves survive, then
rewrites every asset: transparent logo (header), and a tile-style multi-size
.ico for the exe / taskbar / Explorer.
"""
from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageFilter

SRC = Path(r"C:\Users\lol\Downloads\IMG_9403.png")
OUT = Path(".")


def brighten_violet(img: Image.Image, floor: int = 246) -> Image.Image:
    """Lift the value of violet/magenta pixels so both halves of the
    crystal survive small (16px) rendering.  The violet body of the logo
    sits at ~(64,0,128)-(128,32,192) — far darker than the ~255 cyan side —
    so at taskbar size it collapses into the background.  Push it toward
    `floor` while keeping the glow gradient."""
    img = img.convert("RGB")
    hsv = img.convert("HSV")
    h, s, v = hsv.split()
    hp, sp, vp = h.load(), s.load(), v.load()

    px = img.load()
    W, H = img.size
    for y in range(H):
        for x in range(W):
            hue, sat, val = hp[x, y], sp[x, y], vp[x, y]
            # violet/magenta band on PIL's 0-255 hue scale (~265-325 deg)
            if 176 <= hue <= 232:
                if val > 28 and val < 248:
                    new_val = min(252, int(val + (floor - val) * 0.85))
                    scale = new_val / max(1, val)
                    r, g, b = px[x, y]
                    # nudge toward magenta so the left half reads violet
                    # (not blue) at tiny sizes
                    px[x, y] = (
                        min(255, int(r * scale * 1.30)),
                        min(255, int(g * scale * 0.92)),
                        min(255, int(b * scale * 1.02)),
                    )
    return img


def chroma_key(img: Image.Image) -> Image.Image:
    """Remove the near-black background with a feathered edge (no halo)."""
    gray = img.convert("L")
    # threshold mask: keep bright pixels, feather the boundary
    mask = gray.point(lambda p: 255 if p > 26 else 0)
    mask = mask.filter(ImageFilter.GaussianBlur(1.6))
    rgba = img.convert("RGBA")
    rgba.putalpha(mask)
    # knock out any stray dark core pixels that survived (background grid)
    return rgba


def main() -> None:
    orig = Image.open(SRC).convert("RGB")
    print("original:", orig.size)

    vivid = brighten_violet(orig)
    vivid.save(OUT / "logo_vivid.png")

    # header logo: transparent, 40px (window icon + in-app branding)
    header = chroma_key(vivid)
    header = header.resize((40, 40), Image.Resampling.LANCZOS)
    header.save(OUT / "logo_header.png")
    print("logo_header.png 40px")

    # full transparent logo for other uses
    logo = chroma_key(vivid).resize((256, 256), Image.Resampling.LANCZOS)
    logo.save(OUT / "logo.png")
    print("logo.png 256px")

    # tile-style .ico: the original composition (dark synthwave backdrop
    # included) so the icon reads as the real artwork at every size
    tile = vivid.resize((256, 256), Image.Resampling.LANCZOS)
    tile.save(
        OUT / "app.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print("app.ico multi-size")

    # preview how the icon renders at taskbar size
    for s in (16, 24, 32):
        small = tile.resize((s, s), Image.Resampling.LANCZOS)
        small.save(OUT / f"icon_preview_{s}.png")


if __name__ == "__main__":
    main()
