#!/usr/bin/env python3
"""Minimal, dependency-free reader for Canon CR3 files.

CR3 is an ISO base media (MP4-like) container. We only need three things
from it, all of which can be read without touching the raw sensor data:

  * the ~1620px preview JPEG (top-level `uuid` box eaf42b5e... -> PRVW)
  * the full-resolution JPEG Canon stores in the first track (trak #1)
  * a few EXIF fields (CMT1 = IFD0, CMT2 = Exif IFD) inside the Canon
    `uuid` box in `moov`: capture time, sub-seconds, orientation, model

Usage as a CLI (handy for debugging a single file):
    python3 cr3.py info  IMG_0001.CR3
    python3 cr3.py preview IMG_0001.CR3 out.jpg
    python3 cr3.py full    IMG_0001.CR3 out.jpg
"""

from __future__ import annotations

import json
import struct
import sys
from dataclasses import asdict, dataclass
from typing import BinaryIO, Iterator, Optional, Tuple

CANON_UUID = bytes.fromhex("85c0b687820f11e08111f4ce462b6a48")
PREVIEW_UUID = bytes.fromhex("eaf42b5e1c984b88b9fbb7dc406e4d16")
XMP_UUID = bytes.fromhex("be7acfcb97a942e89c71999491e3afac")

CONTAINER_BOXES = {b"moov", b"trak", b"mdia", b"minf", b"stbl"}


class CR3Error(Exception):
    pass


@dataclass
class CR3Info:
    model: Optional[str] = None
    orientation: int = 1  # EXIF orientation (1 = normal, 6 = rotate 90 CW, 8 = 90 CCW, 3 = 180)
    datetime_original: Optional[str] = None  # "YYYY:MM:DD HH:MM:SS"
    subsec: Optional[str] = None
    preview_size: Optional[Tuple[int, int]] = None
    camera_rating: Optional[int] = None  # xmp:Rating from the in-camera XMP packet, if any


# ---------------------------------------------------------------------------
# Box walking
# ---------------------------------------------------------------------------

def _iter_boxes(f: BinaryIO, start: int, end: int) -> Iterator[Tuple[bytes, int, int, int]]:
    """Yield (type, box_offset, payload_offset, box_end) for boxes in [start, end)."""
    off = start
    while off + 8 <= end:
        f.seek(off)
        hdr = f.read(16)
        if len(hdr) < 8:
            return
        size, typ = struct.unpack(">I4s", hdr[:8])
        payload = off + 8
        if size == 1:
            size = struct.unpack(">Q", hdr[8:16])[0]
            payload = off + 16
        elif size == 0:
            size = end - off
        if size < 8:
            return
        yield typ, off, payload, min(off + size, end)
        off += size


def _file_size(f: BinaryIO) -> int:
    f.seek(0, 2)
    return f.tell()


def _find_top_uuid(f: BinaryIO, wanted: bytes) -> Optional[Tuple[int, int]]:
    for typ, _off, payload, box_end in _iter_boxes(f, 0, _file_size(f)):
        if typ == b"uuid":
            f.seek(payload)
            if f.read(16) == wanted:
                return payload + 16, box_end
        if typ == b"mdat":
            # Everything we need lives before the (huge) media data box.
            break
    return None


def _find_child(f: BinaryIO, start: int, end: int, path: Tuple[bytes, ...], index: int = 0):
    """Find a nested box by path, e.g. (b'moov', b'trak'). `index` picks the Nth match of the last element."""
    if not path:
        return None
    head, rest = path[0], path[1:]
    seen = 0
    for typ, _off, payload, box_end in _iter_boxes(f, start, end):
        if typ == head:
            if rest:
                found = _find_child(f, payload, box_end, rest, index)
                if found:
                    return found
            else:
                if seen == index:
                    return payload, box_end
                seen += 1
    return None


# ---------------------------------------------------------------------------
# JPEG extraction
# ---------------------------------------------------------------------------

def read_preview_jpeg(path: str) -> bytes:
    """Return the ~1620x1080 preview JPEG embedded in the CR3."""
    with open(path, "rb") as f:
        loc = _find_top_uuid(f, PREVIEW_UUID)
        if not loc:
            raise CR3Error(f"{path}: no preview (PRVW) box found")
        start, end = loc
        f.seek(start)
        blob = f.read(end - start)
    i = blob.find(b"PRVW")
    if i < 0:
        raise CR3Error(f"{path}: PRVW tag missing")
    soi = blob.find(b"\xff\xd8\xff", i)
    if soi < 0:
        raise CR3Error(f"{path}: preview JPEG start marker missing")
    # The 4 bytes right before the JPEG are its length (big-endian).
    length = struct.unpack(">I", blob[soi - 4:soi])[0]
    if not (0 < length <= len(blob) - soi):
        length = len(blob) - soi
    return blob[soi:soi + length]


def read_full_jpeg(path: str) -> bytes:
    """Return the full-resolution JPEG stored in the CR3's first track.

    This is large (often 2-6 MB); only use it for close inspection such as
    checking focus on eyes when choosing between near-identical frames.
    """
    with open(path, "rb") as f:
        size = _file_size(f)
        moov = _find_child(f, 0, size, (b"moov",))
        if not moov:
            raise CR3Error(f"{path}: no moov box")
        trak = _find_child(f, moov[0], moov[1], (b"trak",), 0)
        if not trak:
            raise CR3Error(f"{path}: no first track")
        stbl = _find_child(f, trak[0], trak[1], (b"mdia", b"minf", b"stbl"))
        if not stbl:
            raise CR3Error(f"{path}: no sample table in first track")
        stsz = _find_child(f, stbl[0], stbl[1], (b"stsz",))
        co64 = _find_child(f, stbl[0], stbl[1], (b"co64",))
        if not (stsz and co64):
            raise CR3Error(f"{path}: first track missing stsz/co64")
        f.seek(stsz[0])
        _vf, sample_size, count = struct.unpack(">III", f.read(12))
        if sample_size == 0 and count:
            sample_size = struct.unpack(">I", f.read(4))[0]
        f.seek(co64[0])
        _vf, n = struct.unpack(">II", f.read(8))
        if not n:
            raise CR3Error(f"{path}: empty chunk offset table")
        offset = struct.unpack(">Q", f.read(8))[0]
        f.seek(offset)
        data = f.read(sample_size)
    if not data.startswith(b"\xff\xd8"):
        raise CR3Error(f"{path}: first track is not a JPEG")
    return data


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def _parse_tiff(blob: bytes, wanted: set) -> dict:
    """Parse the first IFD of a small TIFF blob and return {tag: value} for wanted tags."""
    if len(blob) < 8:
        return {}
    bo = blob[:2]
    if bo == b"II":
        e = "<"
    elif bo == b"MM":
        e = ">"
    else:
        return {}
    ifd = struct.unpack(e + "I", blob[4:8])[0]
    if ifd + 2 > len(blob):
        return {}
    (count,) = struct.unpack(e + "H", blob[ifd:ifd + 2])
    out = {}
    type_sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}
    for k in range(count):
        ent = ifd + 2 + 12 * k
        if ent + 12 > len(blob):
            break
        tag, typ, n = struct.unpack(e + "HHI", blob[ent:ent + 8])
        if tag not in wanted:
            continue
        nbytes = type_sizes.get(typ, 1) * n
        if nbytes <= 4:
            raw = blob[ent + 8:ent + 8 + nbytes]
        else:
            (ptr,) = struct.unpack(e + "I", blob[ent + 8:ent + 12])
            raw = blob[ptr:ptr + nbytes]
        if typ == 2:
            out[tag] = raw.split(b"\x00", 1)[0].decode("ascii", "replace").strip()
        elif typ == 3:
            out[tag] = struct.unpack(e + "H", raw[:2])[0]
        elif typ == 4:
            out[tag] = struct.unpack(e + "I", raw[:4])[0]
    return out


def read_info(path: str) -> CR3Info:
    info = CR3Info()
    with open(path, "rb") as f:
        size = _file_size(f)
        moov = _find_child(f, 0, size, (b"moov",))
        if moov:
            for typ, _off, payload, box_end in _iter_boxes(f, moov[0], moov[1]):
                if typ != b"uuid":
                    continue
                f.seek(payload)
                if f.read(16) != CANON_UUID:
                    continue
                for ctyp, _o, cpay, cend in _iter_boxes(f, payload + 16, box_end):
                    if ctyp not in (b"CMT1", b"CMT2"):
                        continue
                    f.seek(cpay)
                    blob = f.read(cend - cpay)
                    if ctyp == b"CMT1":
                        t = _parse_tiff(blob, {0x0110, 0x0112, 0x0132})
                        info.model = t.get(0x0110, info.model)
                        info.orientation = int(t.get(0x0112, 1) or 1)
                        info.datetime_original = info.datetime_original or t.get(0x0132)
                    else:
                        t = _parse_tiff(blob, {0x9003, 0x9291})
                        if t.get(0x9003):
                            info.datetime_original = t[0x9003]
                        info.subsec = t.get(0x9291) or info.subsec
                break
        xmp = _find_top_uuid(f, XMP_UUID)
        if xmp:
            f.seek(xmp[0])
            packet = f.read(xmp[1] - xmp[0]).decode("utf-8", "replace")
            import re

            m = re.search(r'xmp:Rating\s*=\s*"(-?\d+)"|<xmp:Rating>\s*(-?\d+)\s*<', packet)
            if m:
                info.camera_rating = int(m.group(1) or m.group(2))
        prv = _find_top_uuid(f, PREVIEW_UUID)
        if prv:
            f.seek(prv[0])
            head = f.read(64)
            i = head.find(b"PRVW")
            # PRVW layout: tag(4) unknown(4) unknown(2) width(2) height(2) ...
            if i >= 0 and i + 14 <= len(head):
                w, h = struct.unpack(">HH", head[i + 10:i + 14])
                info.preview_size = (w, h)
    return info


def main(argv) -> int:
    if len(argv) < 3 or argv[1] not in {"info", "preview", "full"}:
        print(__doc__)
        return 2
    cmd, path = argv[1], argv[2]
    if cmd == "info":
        print(json.dumps(asdict(read_info(path)), indent=2))
        return 0
    if len(argv) < 4:
        print("output path required", file=sys.stderr)
        return 2
    data = read_preview_jpeg(path) if cmd == "preview" else read_full_jpeg(path)
    with open(argv[3], "wb") as out:
        out.write(data)
    print(f"wrote {len(data)} bytes to {argv[3]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
