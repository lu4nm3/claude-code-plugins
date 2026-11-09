"""Read and update Lightroom-compatible XMP sidecar files.

Lightroom Classic keeps metadata for proprietary raw files (CR3 included) in
a sidecar named `<basename>.xmp` next to the raw file. A sidecar written by
Lightroom also carries develop settings, crops, keywords and so on, so we
never rewrite the whole file: we change only the label and the
xmp:MetadataDate text in place, leave everything else byte-for-byte
untouched, and check that the result still parses as XML before saving it.

The label can live in two places. Lightroom reads `xmp:Label` ("Green"), but
sidecars written by Lightroom Classic and by tools such as Aftershoot also
carry `photoshop:LabelColor` ("green"). Both are kept in step: if a sidecar
has a LabelColor, it gets the same color as xmp:Label, so no file ever says
two different colors.
"""

from __future__ import annotations

import io
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional
from xml.sax.saxutils import escape, quoteattr

XMP_NS = "http://ns.adobe.com/xap/1.0/"
PS_NS = "http://ns.adobe.com/photoshop/1.0/"
RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
X_NS = "adobe:ns:meta/"
CRS_NS = "http://ns.adobe.com/camera-raw-settings/1.0/"
OWN_XMPTK = "cull-raw-photos"  # x:xmptk of sidecars this skill creates


def sidecar_path(raw_path: str) -> str:
    """Return the sidecar path Lightroom uses for a raw file (existing one if present)."""
    base, _ext = os.path.splitext(raw_path)
    for ext in (".xmp", ".XMP"):
        if os.path.exists(base + ext):
            return base + ext
    return base + ".xmp"


# photoshop:LabelColor values Lightroom writes for its default label set.
LABEL_COLORS = ("red", "yellow", "green", "blue", "purple")


def _xmp_prefixes(text: str, ns: str = XMP_NS) -> list:
    """Prefixes bound to namespace `ns` in this document (usually just 'xmp')."""
    prefixes = []
    try:
        for _event, (prefix, uri) in ET.iterparse(io.StringIO(text), events=("start-ns",)):
            if uri == ns and prefix not in prefixes:
                prefixes.append(prefix)
    except ET.ParseError:
        pass
    return prefixes


def read_property(text: str, name: str, ns: str = XMP_NS) -> Optional[str]:
    """Read a simple property (xmp:<name> by default), as an attribute or an element."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    key = f"{{{ns}}}{name}"
    for desc in root.iter(f"{{{RDF_NS}}}Description"):
        if key in desc.attrib:
            return desc.attrib[key]
        el = desc.find(key)
        if el is not None:
            return (el.text or "").strip()
    return None


def sidecar_origin(text: str) -> dict:
    """Who wrote this sidecar, and does it carry develop settings?

    Returns {'writer', 'creator_tool', 'has_develop'}. writer is one of
    'lightroom' (xmp:CreatorTool names Lightroom), 'cull-raw-photos' (a sidecar
    this skill created), 'other' (any other tool, e.g. Aftershoot or Bridge)
    or 'unknown' (nothing identifies the tool). has_develop is True when the
    sidecar holds Camera Raw / Lightroom develop settings (crs:*).
    """
    creator = read_property(text, "CreatorTool")
    xmptk = None
    try:
        root = ET.fromstring(text)
        xmptk = root.attrib.get(f"{{{X_NS}}}xmptk")
    except ET.ParseError:
        pass
    has_develop = bool(_xmp_prefixes(text, CRS_NS))
    if creator and "lightroom" in creator.lower():
        writer = "lightroom"
    elif xmptk == OWN_XMPTK and not creator and not has_develop:
        writer = "cull-raw-photos"
    elif creator:
        writer = "other"
    else:
        writer = "unknown"
    return {"writer": writer, "creator_tool": creator, "has_develop": has_develop}


def read_sidecar(raw_path: str) -> dict:
    """Return {'exists', 'path', 'label', 'rating', ...} for a raw file's sidecar.

    An existing sidecar also reports 'label_color' and, from sidecar_origin(),
    'writer', 'creator_tool' and 'has_develop'.
    """
    path = sidecar_path(raw_path)
    if not os.path.exists(path):
        return {"exists": False, "path": path, "label": None, "rating": None,
                "writer": None, "creator_tool": None, "has_develop": False}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    label = read_property(text, "Label")
    color = read_property(text, "LabelColor", PS_NS)
    if not label and color:
        label = color.capitalize()  # only the photoshop field is set
    return {
        "exists": True,
        "path": path,
        "label": label,
        "label_color": color,
        "rating": read_property(text, "Rating"),
        **sidecar_origin(text),
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _set_existing(text: str, name: str, value: Optional[str], ns: str = XMP_NS) -> Optional[str]:
    """Change (or with value=None, remove) an existing property. None if it isn't there."""
    for p in _xmp_prefixes(text, ns):
        attr = re.compile(r"(\s*)(\b%s:%s\s*=\s*)(\"[^\"]*\"|'[^']*')" % (re.escape(p), name))
        if attr.search(text):
            if value is None:
                return attr.sub("", text, count=1)
            return attr.sub(lambda m: m.group(1) + m.group(2) + quoteattr(value), text, count=1)
        elem = re.compile(r"(\s*)(<%s:%s>)(.*?)(</%s:%s>)" % (re.escape(p), name, re.escape(p), name), re.S)
        if elem.search(text):
            if value is None:
                return elem.sub("", text, count=1)
            return elem.sub(lambda m: m.group(1) + m.group(2) + escape(value) + m.group(4), text, count=1)
    return None


def _set_property(text: str, name: str, value: str) -> str:
    """Set xmp:<name> in place. Adds it to the first rdf:Description if missing."""
    prefixes = _xmp_prefixes(text) or ["xmp"]
    out = _set_existing(text, name, value)
    if out is not None:
        return out

    # Not present: add it as an attribute on the first rdf:Description.
    m = re.search(r"<rdf:Description\b[^>]*?(/?)>", text, re.S)
    if not m:
        raise ValueError("no rdf:Description element to attach the property to")
    prefix = prefixes[0]
    insert = ""
    if not _xmp_prefixes(text):
        insert += f'\n    xmlns:{prefix}="{XMP_NS}"'
    insert += f"\n   {prefix}:{name}={quoteattr(value)}"
    pos = m.start(1)  # just before '/>' or '>'
    return text[:pos] + insert + text[pos:]


NEW_SIDECAR = """<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="cull-raw-photos">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:xmp="http://ns.adobe.com/xap/1.0/"{props}/>
 </rdf:RDF>
</x:xmpmeta>
"""


def build_new_sidecar(label: str, rating: Optional[int] = None) -> str:
    props = f"\n   xmp:Label={quoteattr(label)}"
    if rating:
        props += f'\n   xmp:Rating="{int(rating)}"'
    props += f'\n   xmp:MetadataDate="{_now_iso()}"'
    return NEW_SIDECAR.format(props=props)


def update_sidecar_text(text: str, label: str) -> str:
    out = _set_property(text, "Label", label)
    if read_property(out, "LabelColor", PS_NS) is not None:
        # Keep photoshop:LabelColor in step. A custom label name has no
        # LabelColor equivalent, so drop the stale one rather than leave it.
        color = label.lower() if label.lower() in LABEL_COLORS else None
        out = _set_existing(out, "LabelColor", color, PS_NS)
    if read_property(out, "MetadataDate") is not None:
        out = _set_property(out, "MetadataDate", _now_iso())
    # Safety net: the edited file must still parse and must say what we meant.
    if read_property(out, "Label") != label:
        raise ValueError("label did not round-trip after editing; refusing to write")
    color = read_property(out, "LabelColor", PS_NS)
    if color is not None and color != label.lower():
        raise ValueError("photoshop:LabelColor disagrees with xmp:Label after editing; refusing to write")
    return out


def atomic_write(path: str, text: str) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    mode = None
    if os.path.exists(path):
        mode = os.stat(path).st_mode & 0o777
    fd, tmp = tempfile.mkstemp(prefix=".cull-", suffix=".xmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        if mode is not None:
            os.chmod(tmp, mode)
        else:
            os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
