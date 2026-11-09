#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["pillow>=10.1"]
# ///
"""Check the file-level assertions for one eval run and draw its Green picks.

    uv run check_run.py <run-dir> <case-name>

<run-dir> holds shoot/ and snapshot.json (from build_fixture.py). Prints a JSON
report and writes <run-dir>/outputs/labels.csv and outputs/greens.jpg.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import cr3  # noqa: E402
import xmp_sidecar  # noqa: E402
from prepare import font, upright  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def strip_label(text: str) -> str:
    text = re.sub(r'\s*(xmp:Label|xmp:MetadataDate|photoshop:LabelColor)="[^"]*"', "", text)
    text = re.sub(r"\s*<(xmp:Label|xmp:MetadataDate|photoshop:LabelColor)>[^<]*</\1>", "", text)
    return text


def when(path):
    i = cr3.read_info(path)
    dt = datetime.strptime(i.datetime_original, "%Y:%m:%d %H:%M:%S")
    if i.subsec and i.subsec.isdigit():
        dt = dt.replace(microsecond=int(float("0." + i.subsec) * 1e6))
    return dt, i


def main():
    run, case = sys.argv[1], sys.argv[2]
    shoot = os.path.join(run, "shoot")
    snap = json.load(open(os.path.join(run, "snapshot.json")))["files"]
    now = sorted(os.listdir(shoot))
    raws = sorted(f for f in now if f.lower().endswith(".cr3"))
    frames = []
    for f in raws:
        dt, info = when(os.path.join(shoot, f))
        sc = xmp_sidecar.read_sidecar(os.path.join(shoot, f))
        frames.append({"file": f, "dt": dt, "label": sc["label"], "label_color": sc.get("label_color"), "sidecar": sc["path"] if sc["exists"] else None,
                       "orientation": info.orientation})
    frames.sort(key=lambda r: (r["dt"], r["file"]))
    for i, r in enumerate(frames, 1):
        r["index"] = i

    added = [f for f in now if f not in snap]
    added_non_xmp = [f for f in added if not f.lower().endswith(".xmp")]
    removed = [f for f in snap if f not in now]
    raws_changed = [f for f in raws if f in snap and (os.path.getsize(os.path.join(shoot, f)) != snap[f]["size"]
                    or abs(os.path.getmtime(os.path.join(shoot, f)) - snap[f]["mtime"]) > 1)]
    other_changed, sidecars_changed, sidecars_other_fields_changed = [], [], []
    for f, s in snap.items():
        p = os.path.join(shoot, f)
        if s["sha256"] is None or not os.path.exists(p):
            continue
        if sha(p) != s["sha256"]:
            if f.lower().endswith(".xmp"):
                sidecars_changed.append(f)
            else:
                other_changed.append(f)
    # compare pre-existing sidecars to the originals with label/date removed
    src_case = json.load(open(os.path.join(run, "snapshot.json")))
    labels_ok = [r for r in frames if r["label"] in ("Green", "Yellow")]
    greens = [r for r in frames if r["label"] == "Green"]

    bursts, cur = [], [frames[0]]
    for a, b in zip(frames, frames[1:]):
        if (b["dt"] - a["dt"]).total_seconds() < 1.0:
            cur.append(b)
        else:
            bursts.append(cur)
            cur = [b]
    bursts.append(cur)
    bad_bursts = [[f"#{r['index']}" for r in bu] for bu in bursts if sum(r["label"] == "Green" for r in bu) > 1]

    rep = {
        "raws": len(frames), "labeled_green_or_yellow": len(labels_ok),
        "unlabeled_or_other": [(r["index"], r["file"], r["label"]) for r in frames if r["label"] not in ("Green", "Yellow")],
        "greens": len(greens), "green_index": [r["index"] for r in greens],
        "yellows": sum(r["label"] == "Yellow" for r in frames),
        "added_files": added, "added_non_xmp": added_non_xmp, "removed_files": removed,
        "raws_changed": raws_changed, "non_xmp_changed": other_changed,
        "sidecars_changed": len(sidecars_changed),
        "label_field_mismatch": [r["file"] for r in frames
                                 if r["label_color"] and (r["label"] or "").lower() != r["label_color"].lower()],
        "bursts_multi_green": bad_bursts, "n_bursts": sum(len(b) > 1 for b in bursts),
    }
    if case == "juneteenth-fresh-cull":
        rep["scene_greens"] = {k: sum(1 for r in greens if lo <= r["index"] <= hi)
                               for k, (lo, hi) in {"solo 2-32": (2, 32), "mom 33-61": (33, 61), "after pause 62-74": (62, 74)}.items()}
    if case == "imported-shoot-keeps-edits":
        # originals: rebuild from the fixture source to compare everything but the label/date
        sys.path.insert(0, HERE)
        import build_fixture
        src = build_fixture.CASES[case]["src"]
        diffs = []
        for f in sorted(snap):
            if not f.lower().endswith(".xmp"):
                continue
            orig = strip_label(open(os.path.join(src, f), encoding="utf-8").read())
            cur_t = strip_label(open(os.path.join(shoot, f), encoding="utf-8").read())
            if orig != cur_t:
                diffs.append(f)
        rep["sidecars_with_other_changes"] = diffs

    os.makedirs(os.path.join(run, "outputs"), exist_ok=True)
    with open(os.path.join(run, "outputs", "labels.csv"), "w") as fh:
        fh.write("index,file,capture_time,label\n")
        for r in frames:
            fh.write(f"{r['index']},{r['file']},{r['dt'].isoformat(timespec='milliseconds')},{r['label'] or ''}\n")
    if greens:
        cols, cw = 5, 300
        ch = 200
        rows = (len(greens) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * (cw + 6) + 6, rows * (ch + 30) + 6), (24, 24, 24))
        d = ImageDraw.Draw(sheet)
        for k, r in enumerate(greens):
            im = upright(Image.open(io.BytesIO(cr3.read_preview_jpeg(os.path.join(shoot, r["file"])))), r["orientation"])
            im.thumbnail((cw, ch))
            x, y = 6 + (k % cols) * (cw + 6), 6 + (k // cols) * (ch + 30)
            sheet.paste(im, (x + (cw - im.width) // 2, y + (ch - im.height) // 2))
            d.text((x + 4, y + ch + 6), f"#{r['index']}  {r['file']}", fill=(120, 220, 120), font=font(15))
        sheet.save(os.path.join(run, "outputs", "greens.jpg"), quality=85)
    print(json.dumps(rep, indent=1, default=str))


if __name__ == "__main__":
    main()
