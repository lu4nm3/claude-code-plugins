#!/usr/bin/env python3
"""Build a test shoot for one eval case from the photographer's own folders.

    python3 build_fixture.py <case-name> <dest-dir> [--snapshot snapshot.json]

The raws are APFS clones (`cp -c`): instant, no extra disk space, and a write
to the clone can never reach the original. Sidecars and .acr files are plain
copies. --snapshot records the sha256 of every file placed, so a grader can
tell exactly what a run changed.

The source folders are on this Mac only (~/Pictures/...), so these cases
only run here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

PICS = os.path.expanduser("~/Pictures")
PASSPORT = os.path.join(PICS, "20260810_emma_passport/canon_raw")

CASES = {
    # 74 frames, kid on a swing; no sidecars, not imported.
    "juneteenth-fresh-cull": {"src": os.path.join(PICS, "20260619_juneteenth/canon_raw")},
    # 47 frames from a baptism; no sidecars, not imported.
    "baptism-fresh-cull": {"src": os.path.join(PICS, "20260516_emma_baptism/canon_raw")},
    # First 42 passport frames with their Lightroom/Aftershoot sidecars and .acr
    # files, labels intact (xmp:Label and photoshop:LabelColor): an imported,
    # edited shoot whose labels the user wants replaced.
    "imported-shoot-keeps-edits": {
        "src": PASSPORT, "range": ("20260809_18_46_21", "20260809_18_51_57"), "strip_labels": False,
    },
    # Passport frames already culled by another tool (Red/Purple/Green/Yellow).
    "existing-labels-ask-first": {
        "src": PASSPORT, "range": ("20260809_19_32_24", "20260809_19_33_05-2"), "strip_labels": False,
    },
}


def sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("case", choices=sorted(CASES))
    ap.add_argument("dest")
    ap.add_argument("--snapshot")
    a = ap.parse_args()
    spec = CASES[a.case]
    src, dest = spec["src"], os.path.abspath(a.dest)
    if os.path.commonpath([dest, PICS]) == PICS:
        sys.exit("refusing to build a fixture inside ~/Pictures")
    os.makedirs(dest, exist_ok=True)
    stems = sorted(os.path.splitext(f)[0] for f in os.listdir(src) if f.lower().endswith(".cr3"))
    if "range" in spec:
        lo, hi = spec["range"]
        stems = [s for s in stems if lo <= s <= hi]
    snap = {}
    for s in stems:
        for f in os.listdir(src):
            if os.path.splitext(f)[0] != s:
                continue
            sp, dp = os.path.join(src, f), os.path.join(dest, f)
            if f.lower().endswith(".cr3"):
                if not os.path.exists(dp):
                    subprocess.run(["cp", "-c", sp, dp], check=True)
            else:
                shutil.copy2(sp, dp)
                if f.lower().endswith(".xmp") and spec.get("strip_labels"):
                    text = open(dp, encoding="utf-8").read()
                    text = re.sub(r'\n\s*(xmp:Label|photoshop:LabelColor)="[^"]*"', "", text)
                    text = re.sub(r"\n\s*<xmp:Label>[^<]*</xmp:Label>", "", text)
                    open(dp, "w", encoding="utf-8").write(text)
    for f in sorted(os.listdir(dest)):
        p = os.path.join(dest, f)
        snap[f] = {"sha256": sha(p) if not f.lower().endswith(".cr3") else None,
                   "size": os.path.getsize(p), "mtime": os.path.getmtime(p)}
    if a.snapshot:
        with open(a.snapshot, "w") as fh:
            json.dump({"case": a.case, "dir": dest, "files": snap}, fh, indent=1)
    n = sum(1 for f in snap if f.lower().endswith(".cr3"))
    print(f"{a.case}: {n} raws, {len(snap) - n} other files -> {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
