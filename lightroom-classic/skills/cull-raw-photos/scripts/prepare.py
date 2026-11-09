#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["pillow>=10.1"]
# ///
"""Prepare a folder of CR3 files for culling.

For every CR3 (in capture order) this:
  * extracts the embedded ~1620px preview JPEG and rotates it upright
  * reads capture time, orientation and any existing sidecar label/rating
  * notes who wrote existing sidecars, to guess whether the shoot is already
    in a Lightroom catalog (manifest "lightroom", printed as "catalog:")
  * computes a rough focus score (see `sharpness` below)
  * builds numbered contact sheets so many frames can be viewed at once

Nothing is written next to the photos. All output goes to a work directory
(default ~/.cache/cull-raw-photos/<folder>-<hash>) because Lightroom's
import would otherwise pick up the extracted JPEGs.

    uv run prepare.py /path/to/raw/folder [--work-dir DIR] [--recursive]
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat  # noqa: E402

import cr3  # noqa: E402
import xmp_sidecar  # noqa: E402

TRANSPOSE = {
    2: [Image.Transpose.FLIP_LEFT_RIGHT],
    3: [Image.Transpose.ROTATE_180],
    4: [Image.Transpose.FLIP_TOP_BOTTOM],
    5: [Image.Transpose.TRANSPOSE],
    6: [Image.Transpose.ROTATE_270],
    7: [Image.Transpose.TRANSVERSE],
    8: [Image.Transpose.ROTATE_90],
}


def upright(img: Image.Image, orientation: int) -> Image.Image:
    for op in TRANSPOSE.get(orientation, []):
        img = img.transpose(op)
    return img


def font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def sharpness(img: Image.Image) -> float:
    """Focus score: Laplacian variance of the sharpest tile in a 6x6 grid.

    Using the best tile rather than the whole frame keeps a shallow
    depth-of-field portrait (sharp subject, creamy background) from scoring
    as "soft". Only meaningful when comparing near-identical frames.
    """
    g = img.convert("L")
    g.thumbnail((960, 960))
    lap = g.filter(ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128))
    w, h = lap.size
    best = 0.0
    n = 6
    for i in range(n):
        for j in range(n):
            box = (i * w // n, j * h // n, (i + 1) * w // n, (j + 1) * h // n)
            var = ImageStat.Stat(lap.crop(box)).var[0]
            best = max(best, var)
    return round(best, 1)


def capture_time(info: cr3.CR3Info, path: str) -> datetime:
    if info.datetime_original:
        try:
            dt = datetime.strptime(info.datetime_original, "%Y:%m:%d %H:%M:%S")
            if info.subsec and info.subsec.isdigit():
                frac = float("0." + info.subsec)
                dt = dt.replace(microsecond=int(frac * 1_000_000))
            return dt
        except ValueError:
            pass
    return datetime.fromtimestamp(os.path.getmtime(path))


def default_work_dir(src: str) -> str:
    src = os.path.abspath(src)
    h = hashlib.sha1(src.encode()).hexdigest()[:8]
    name = os.path.basename(src.rstrip("/")) or "photos"
    parent = os.path.basename(os.path.dirname(src.rstrip("/")))
    slug = f"{parent}_{name}" if parent else name
    return os.path.join(os.path.expanduser("~/.cache/cull-raw-photos"), f"{slug}-{h}")


def find_raws(src: str, recursive: bool) -> list:
    out = []
    if recursive:
        for root, dirs, files in os.walk(src):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            out += [os.path.join(root, f) for f in files if f.lower().endswith(".cr3") and not f.startswith(".")]
    else:
        out = [os.path.join(src, f) for f in os.listdir(src) if f.lower().endswith(".cr3") and not f.startswith(".")]
    return out


def process(path: str, src: str, preview_dir: str) -> dict:
    rel = os.path.relpath(path, src)
    rec = {"file": rel, "path": os.path.abspath(path)}
    try:
        info = cr3.read_info(path)
        jpeg = cr3.read_preview_jpeg(path)
        img = upright(Image.open(io.BytesIO(jpeg)), info.orientation)
        img.load()
        stem = rel.replace(os.sep, "__")
        stem = os.path.splitext(stem)[0]
        out = os.path.join(preview_dir, stem + ".jpg")
        img.convert("RGB").save(out, quality=88)
        rec.update(
            preview=out,
            width=img.width,
            height=img.height,
            orientation=info.orientation,
            model=info.model,
            camera_rating=info.camera_rating,
            sharpness=sharpness(img),
            _dt=capture_time(info, path),
        )
    except Exception as e:  # keep going; report the bad file
        rec["error"] = f"{type(e).__name__}: {e}"
        rec["_dt"] = datetime.fromtimestamp(os.path.getmtime(path))
    sc = xmp_sidecar.read_sidecar(path)
    rec["sidecar"] = {k: sc[k] for k in ("exists", "label", "rating", "writer", "has_develop")}
    return rec


def catalog_status(records: list) -> dict:
    """Guess from the existing sidecars whether the shoot is in a Lightroom catalog.

    Lightroom Classic writes a sidecar for a CR3 only after the photo is in a
    catalog (Save Metadata to File, or auto-write). So Lightroom-written
    sidecars mean the shoot is almost certainly imported. Sidecars from other
    tools, or with develop settings from an unnamed tool, mean it may be.
    Sidecars only from an earlier run of this skill, or none at all, point to
    "not imported", but can't rule it out: a catalog that never wrote
    metadata out leaves no sidecars.

    verdict is 'imported' | 'maybe-imported' | 'probably-not-imported'.
    """
    n = len(records)
    with_sc = [r for r in records if r["sidecar"]["exists"]]
    writers = {}
    for r in with_sc:
        w = r["sidecar"]["writer"]
        writers[w] = writers.get(w, 0) + 1
    develop = sum(1 for r in with_sc if r["sidecar"]["has_develop"])
    if writers.get("lightroom"):
        verdict = "imported"
        reason = f"{writers['lightroom']} of {n} frames have sidecars written by Lightroom"
    elif writers.get("other") or writers.get("unknown") or develop:
        verdict = "maybe-imported"
        k = writers.get("other", 0) + writers.get("unknown", 0)
        reason = f"{k} of {n} frames have sidecars from another tool" + (
            f", {develop} with develop settings" if develop else "")
    elif with_sc:
        verdict = "probably-not-imported"
        reason = f"the only sidecars ({len(with_sc)} of {n}) are from an earlier run of this skill"
    else:
        verdict = "probably-not-imported"
        reason = "no frame has a sidecar"
    return {
        "verdict": verdict,
        "reason": reason,
        "sidecars": len(with_sc),
        "writers": writers,
        "with_develop_settings": develop,
    }


def fmt_gap(s: float) -> str:
    if s < 60:
        return f"+{s:.1f}s"
    if s < 3600:
        return f"+{int(s // 60)}m{int(s % 60):02d}s"
    return f"+{s / 3600:.1f}h"


def build_sheets(records: list, sheet_dir: str, cols: int, rows: int, cell_w: int, gap_mark: float) -> list:
    cell_h = int(cell_w * 2 / 3)
    label_h = 44
    pad = 6
    per = cols * rows
    f_big = font(20)
    f_small = font(14)
    sheets = []
    good = [r for r in records if "preview" in r]
    for s in range(0, len(good), per):
        chunk = good[s:s + per]
        nrows = (len(chunk) + cols - 1) // cols
        W = cols * (cell_w + pad) + pad
        H = nrows * (cell_h + label_h + pad) + pad
        sheet = Image.new("RGB", (W, H), (24, 24, 24))
        d = ImageDraw.Draw(sheet)
        for k, r in enumerate(chunk):
            x = pad + (k % cols) * (cell_w + pad)
            y = pad + (k // cols) * (cell_h + label_h + pad)
            im = Image.open(r["preview"])
            im.thumbnail((cell_w, cell_h))
            ox = x + (cell_w - im.width) // 2
            sheet.paste(im, (ox, y + (cell_h - im.height) // 2))
            big_gap = r["gap_s"] is not None and r["gap_s"] >= gap_mark
            if big_gap:
                # Orange bar on the left edge: a long pause before this frame,
                # a likely (not certain) start of a new moment.
                d.rectangle([x, y, x + 5, y + cell_h + label_h - 1], fill=(255, 140, 0))
            d.text((x + 10, y + cell_h + 2), f"#{r['index']}", fill=(255, 255, 255), font=f_big)
            name = os.path.basename(r["file"])
            d.text((x + 72, y + cell_h + 4), name, fill=(200, 200, 200), font=f_small)
            t = r["time"][11:22]
            gap = "" if r["gap_s"] is None else fmt_gap(r["gap_s"])
            d.text(
                (x + 72, y + cell_h + 22),
                f"{t}  {gap}",
                fill=(255, 170, 60) if big_gap else (160, 160, 160),
                font=f_small,
            )
        out = os.path.join(sheet_dir, f"sheet_{len(sheets) + 1:03d}.jpg")
        sheet.save(out, quality=85)
        sheets.append({"sheet": out, "first": chunk[0]["index"], "last": chunk[-1]["index"]})
    return sheets


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder")
    ap.add_argument("--work-dir")
    ap.add_argument("--recursive", action="store_true", help="include CR3s in subfolders")
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--rows", type=int, default=4)
    ap.add_argument("--cell", type=int, default=390, help="contact sheet cell width in px")
    ap.add_argument("--gap-mark", type=float, default=20.0, help="mark frames that follow a pause of this many seconds")
    args = ap.parse_args()

    src = os.path.abspath(os.path.expanduser(args.folder))
    if not os.path.isdir(src):
        print(f"not a folder: {src}", file=sys.stderr)
        return 2
    raws = find_raws(src, args.recursive)
    if not raws:
        print(f"no .CR3 files in {src}", file=sys.stderr)
        return 1

    work = os.path.abspath(os.path.expanduser(args.work_dir or default_work_dir(src)))
    if os.path.commonpath([work, src]) == src:
        print("work dir must be outside the photo folder (Lightroom would import the JPEGs)", file=sys.stderr)
        return 2
    preview_dir = os.path.join(work, "previews")
    sheet_dir = os.path.join(work, "sheets")
    os.makedirs(preview_dir, exist_ok=True)
    os.makedirs(sheet_dir, exist_ok=True)
    for old in os.listdir(sheet_dir):
        os.unlink(os.path.join(sheet_dir, old))

    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 4)) as ex:
        records = list(ex.map(lambda p: process(p, src, preview_dir), raws))

    records.sort(key=lambda r: (r["_dt"], r["file"]))
    prev = None
    for i, r in enumerate(records, 1):
        r["index"] = i
        r["time"] = r["_dt"].isoformat(timespec="milliseconds")
        r["gap_s"] = None if prev is None else round((r["_dt"] - prev).total_seconds(), 2)
        prev = r.pop("_dt")

    sheets = build_sheets(records, sheet_dir, args.cols, args.rows, args.cell, args.gap_mark)

    labeled = [r for r in records if r["sidecar"]["label"]]
    manifest = {
        "source_dir": src,
        "work_dir": work,
        "created": datetime.now().isoformat(timespec="seconds"),
        "count": len(records),
        "errors": [r["file"] for r in records if "error" in r],
        "already_labeled": len(labeled),
        "lightroom": catalog_status(records),
        "sheets": sheets,
        "images": records,
    }
    with open(os.path.join(work, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)

    print(f"source:   {src}")
    print(f"work dir: {work}")
    print(f"images:   {len(records)} CR3 files, {len(sheets)} contact sheets")
    if manifest["errors"]:
        print(f"ERRORS:   {len(manifest['errors'])} files could not be read: {manifest['errors'][:10]}")
    lr = manifest["lightroom"]
    print(f"catalog:  {lr['verdict']} ({lr['reason']})")
    if labeled:
        counts = {}
        for r in labeled:
            counts[r["sidecar"]["label"]] = counts.get(r["sidecar"]["label"], 0) + 1
        print(f"NOTE:     {len(labeled)} files already have a color label in their sidecar: {counts}")
    for s in sheets:
        print(f"  {s['sheet']}  (#{s['first']}-#{s['last']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
