#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["pillow>=10.1"]
# ///
"""Build a side-by-side comparison image of candidate frames from one group.

Frames are given by their contact-sheet number (#) or file name. Each tile
shows the frame number, file name and a focus score. With --zoom the score
is computed on the crop itself and the sharpest crop is starred; without
--zoom the whole-frame score is shown unstarred, because it mostly measures
how much in-focus background a frame contains.

    uv run compare.py --work-dir DIR 27 28 29
    uv run compare.py --work-dir DIR 27 28 29 --zoom 0.35,0.15,0.3,0.3

--zoom x,y,w,h crops the same region (fractions of the upright frame, e.g.
around the subject's eyes) out of the FULL-resolution embedded JPEG of each
frame, which is what you want for judging focus and blinks between frames
that look identical at preview size.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image, ImageDraw  # noqa: E402

import cr3  # noqa: E402
from prepare import font, sharpness, upright  # noqa: E402

MAX_W = 1568
PER_IMAGE = 9


def resolve(manifest: dict, refs: list) -> list:
    by_index = {r["index"]: r for r in manifest["images"]}
    by_file = {}
    for r in manifest["images"]:
        by_file[r["file"]] = r
        by_file[os.path.basename(r["file"])] = r
        by_file[os.path.splitext(os.path.basename(r["file"]))[0]] = r
    out = []
    for ref in refs:
        key = ref.lstrip("#")
        r = by_index.get(int(key)) if key.isdigit() and int(key) in by_index else by_file.get(ref)
        if r is None:
            raise SystemExit(f"unknown frame: {ref}")
        if "preview" not in r:
            raise SystemExit(f"frame {ref} has no preview ({r.get('error')})")
        out.append(r)
    return out


def tile_image(rec: dict, zoom):
    if not zoom:
        return Image.open(rec["preview"])
    full = Image.open(io.BytesIO(cr3.read_full_jpeg(rec["path"])))
    full = upright(full, rec.get("orientation", 1))
    x, y, w, h = zoom
    W, H = full.size
    box = (int(x * W), int(y * H), int(min(1, x + w) * W), int(min(1, y + h) * H))
    return full.crop(box)


def layout(n: int):
    if n <= 3:
        return n, 1
    if n <= 4:
        return 2, 2
    if n <= 6:
        return 3, 2
    return 3, 3


def build(recs: list, zoom, out_path: str) -> None:
    cols, rows = layout(len(recs))
    pad, label_h = 6, 30
    cell_w = (MAX_W - pad * (cols + 1)) // cols
    tiles = [tile_image(r, zoom) for r in recs]
    aspect = max(t.height / t.width for t in tiles)
    cell_h = int(cell_w * min(aspect, 1.5))
    rows = (len(recs) + cols - 1) // cols
    W = cols * (cell_w + pad) + pad
    H = rows * (cell_h + label_h + pad) + pad
    sheet = Image.new("RGB", (W, H), (24, 24, 24))
    d = ImageDraw.Draw(sheet)
    f_big, f_small = font(20), font(15)
    # Whole-frame scores swing with how much in-focus background a frame has,
    # so they only rank frames fairly in zoom mode, where every tile is the
    # same region. Re-score the crops there and only star in that case.
    if zoom:
        scores = [sharpness(t) for t in tiles]
    else:
        scores = [r.get("sharpness", 0) for r in recs]
    top = max(scores) if zoom and len(recs) > 1 else None
    for k, (r, t, score) in enumerate(zip(recs, tiles, scores)):
        x = pad + (k % cols) * (cell_w + pad)
        y = pad + (k // cols) * (cell_h + label_h + pad)
        t = t.copy()
        t.thumbnail((cell_w, cell_h))
        sheet.paste(t, (x + (cell_w - t.width) // 2, y + (cell_h - t.height) // 2))
        d.text((x + 8, y + cell_h + 4), f"#{r['index']}", fill=(255, 255, 255), font=f_big)
        star = " *" if top is not None and score == top else ""
        label = "crop focus" if zoom else "frame focus"
        d.text(
            (x + 70, y + cell_h + 7),
            f"{os.path.basename(r['file'])}   {label} {score:.0f}{star}",
            fill=(255, 210, 90) if star else (150, 150, 150),
            font=f_small,
        )
    sheet.save(out_path, quality=88)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("frames", nargs="+", help="frame numbers (#) or file names")
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--zoom", help="x,y,w,h as fractions of the frame, e.g. 0.4,0.2,0.2,0.2")
    ap.add_argument("--out", help="output path (default: <work-dir>/compare/<frames>.jpg)")
    args = ap.parse_args()

    work = os.path.abspath(os.path.expanduser(args.work_dir))
    with open(os.path.join(work, "manifest.json")) as f:
        manifest = json.load(f)
    recs = resolve(manifest, args.frames)
    zoom = None
    if args.zoom:
        zoom = tuple(float(v) for v in args.zoom.split(","))
        if len(zoom) != 4:
            raise SystemExit("--zoom needs four comma-separated fractions: x,y,w,h")

    os.makedirs(os.path.join(work, "compare"), exist_ok=True)
    outs = []
    for page, s in enumerate(range(0, len(recs), PER_IMAGE)):
        chunk = recs[s:s + PER_IMAGE]
        if args.out and len(recs) <= PER_IMAGE:
            out = args.out
        else:
            tag = f"{chunk[0]['index']}-{chunk[-1]['index']}" + ("_zoom" if zoom else "")
            out = os.path.join(work, "compare", f"compare_{tag}_{page + 1}.jpg")
        build(chunk, zoom, out)
        outs.append(out)
    for o in outs:
        print(o)
    return 0


if __name__ == "__main__":
    sys.exit(main())
