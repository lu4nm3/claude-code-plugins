#!/usr/bin/env python3
"""Write cull results to Lightroom-readable XMP sidecars.

Reads <work-dir>/groups.json and <work-dir>/manifest.json. In every group
the "best" frame gets the best label (Green) and every other member gets
the other label (Yellow). Only xmp:Label (and xmp:MetadataDate) is changed
in an existing sidecar; a missing sidecar is created with just the label.

    python3 apply_labels.py --work-dir DIR            # dry run: show what would change
    python3 apply_labels.py --work-dir DIR --write    # back up existing sidecars, then write
    python3 apply_labels.py --work-dir DIR --undo     # revert the most recent --write

groups.json:
    {"groups": [
        {"id": 1, "members": [1, 2, 3], "best": 2, "note": "why #2 won"},
        {"id": 2, "members": ["IMG_0104.CR3"], "best": "IMG_0104.CR3"},
        {"id": 3, "members": [9], "reject": true, "note": "blank frame"}
    ]}
Members and best may be contact-sheet numbers or file names. A group with
"reject": true (misfires: blank, black, accidental shots) has no best, and
all its members get the other label.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import xmp_sidecar  # noqa: E402


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load(work: str):
    with open(os.path.join(work, "manifest.json")) as f:
        manifest = json.load(f)
    gpath = os.path.join(work, "groups.json")
    if not os.path.exists(gpath):
        raise SystemExit(f"missing {gpath}")
    with open(gpath) as f:
        groups = json.load(f)
    if isinstance(groups, list):
        groups = {"groups": groups}
    return manifest, groups


def build_plan(manifest: dict, groups: dict, best_label: str, other_label: str):
    by_index = {r["index"]: r for r in manifest["images"]}
    by_name = {}
    for r in manifest["images"]:
        by_name[r["file"]] = r
        by_name.setdefault(os.path.basename(r["file"]), r)

    def find(ref):
        if isinstance(ref, int) or (isinstance(ref, str) and ref.lstrip("#").isdigit() and not ref.lower().endswith(".cr3")):
            return by_index.get(int(str(ref).lstrip("#")))
        return by_name.get(ref)

    problems, assigned, plan = [], {}, []
    for g in groups["groups"]:
        gid = g.get("id", "?")
        members = [find(m) for m in g.get("members", [])]
        for ref, rec in zip(g.get("members", []), members):
            if rec is None:
                problems.append(f"group {gid}: unknown member {ref!r}")
        members = [m for m in members if m]
        if not members:
            problems.append(f"group {gid}: no valid members")
            continue
        if g.get("reject"):
            # Misfires (blank, black, lens cap, accidental shots) are not a
            # moment: every member gets the other label and there is no pick.
            if g.get("best") is not None:
                problems.append(f"group {gid}: a reject group has no 'best'")
            best = None
        else:
            best = find(g.get("best")) if g.get("best") is not None else (members[0] if len(members) == 1 else None)
            if best is None:
                problems.append(f"group {gid}: 'best' missing or unknown ({g.get('best')!r})")
                continue
            if best["index"] not in {m["index"] for m in members}:
                problems.append(f"group {gid}: best #{best['index']} is not one of its members")
        for m in members:
            if m["index"] in assigned:
                problems.append(f"#{m['index']} {m['file']} is in group {assigned[m['index']]} and group {gid}")
            assigned[m["index"]] = gid
            plan.append((m, best_label if best and m["index"] == best["index"] else other_label, gid))
    missing = [r for r in manifest["images"] if r["index"] not in assigned]
    return plan, problems, missing


def cmd_plan_or_write(args) -> int:
    work = os.path.abspath(os.path.expanduser(args.work_dir))
    manifest, groups = load(work)
    plan, problems, missing = build_plan(manifest, groups, args.best_label, args.other_label)
    if missing:
        problems.append(
            f"{len(missing)} frames are not in any group: "
            + ", ".join(f"#{r['index']}" for r in missing[:40])
            + (" ..." if len(missing) > 40 else "")
        )
    if problems:
        print("groups.json has problems; nothing was written:")
        for p in problems:
            print("  - " + p)
        return 1

    rows, changes = [], Counter()
    for rec, label, gid in plan:
        raw = rec["path"]
        sc = xmp_sidecar.read_sidecar(raw)
        old = sc["label"]
        if not sc["exists"]:
            action = "create"
        elif old == label:
            action = "unchanged"
        else:
            action = "modify"
        changes[action] += 1
        rows.append({"rec": rec, "label": label, "group": gid, "old": old, "action": action, "sidecar": sc["path"]})

    rejects = [g for g in groups["groups"] if g.get("reject")]
    n_groups = len(groups["groups"]) - len(rejects)
    best_n = sum(1 for r in rows if r["label"] == args.best_label)
    print(f"{len(rows)} frames in {n_groups} moments -> {best_n} {args.best_label}, {len(rows) - best_n} {args.other_label}")
    if rejects:
        n_rej = sum(len(g.get("members", [])) for g in rejects)
        print(f"  {n_rej} of the {args.other_label} frames are in reject groups (misfires), with no pick")
    print(f"sidecars: {changes['create']} to create, {changes['modify']} to modify, {changes['unchanged']} already correct")
    overwrites = [r for r in rows if r["action"] == "modify" and r["old"]]
    if overwrites:
        flips = Counter(f"{r['old']} -> {r['label']}" for r in overwrites)
        print("existing labels that would be replaced: " + ", ".join(f"{k}: {v}" for k, v in flips.most_common()))
        foreign = [r for r in overwrites if r["old"] not in (args.best_label, args.other_label)]
        if foreign:
            print(
                f"  {len(foreign)} of them are labels this tool does not use "
                f"({', '.join(sorted({r['old'] for r in foreign}))}), probably set by hand or another tool"
            )

    if not args.write:
        print("\ndry run: nothing written. Re-run with --write to apply.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = os.path.join(work, "backups", stamp)
    log = {"created": stamp, "source_dir": manifest["source_dir"], "entries": []}
    for r in rows:
        if r["action"] == "unchanged":
            continue
        path = r["sidecar"]
        entry = {"sidecar": path, "action": r["action"], "old_label": r["old"], "new_label": r["label"]}
        if r["action"] == "create":
            text = xmp_sidecar.build_new_sidecar(r["label"], r["rec"].get("camera_rating"))
        else:
            with open(path, "r", encoding="utf-8") as f:
                original = f.read()
            os.makedirs(backup_dir, exist_ok=True)
            backup = os.path.join(backup_dir, os.path.relpath(path, manifest["source_dir"]).replace(os.sep, "__"))
            shutil.copy2(path, backup)
            entry["backup"] = backup
            text = xmp_sidecar.update_sidecar_text(original, r["label"])
        xmp_sidecar.atomic_write(path, text)
        entry["written_sha256"] = sha(text)
        log["entries"].append(entry)

    log_path = os.path.join(work, f"apply-log-{stamp}.json")
    with open(log_path, "w") as f:
        json.dump(log, f, indent=1)
    print(f"\nwrote {len(log['entries'])} sidecars; log: {log_path}")
    if log["entries"] and any(e.get("backup") for e in log["entries"]):
        print(f"backups of modified sidecars: {backup_dir}")
    return 0


def cmd_undo(args) -> int:
    work = os.path.abspath(os.path.expanduser(args.work_dir))
    logs = sorted(
        f for f in os.listdir(work)
        if f.startswith("apply-log-") and f.endswith(".json") and not f.endswith(".undone.json")
    )
    if not logs:
        print("no apply log found; nothing to undo")
        return 1
    log_path = os.path.join(work, logs[-1])
    with open(log_path) as f:
        log = json.load(f)
    restored = removed = skipped = 0
    for e in log["entries"]:
        path = e["sidecar"]
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            current = f.read()
        if sha(current) != e["written_sha256"]:
            # Lightroom (or someone) rewrote it since; don't clobber their changes.
            print(f"skipped (changed since labeling): {path}")
            skipped += 1
            continue
        if e["action"] == "create":
            os.unlink(path)
            removed += 1
        else:
            shutil.copy2(e["backup"], path)
            restored += 1
    os.rename(log_path, log_path.replace(".json", ".undone.json"))
    print(f"undo: {restored} sidecars restored, {removed} created sidecars removed, {skipped} skipped")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--write", action="store_true", help="actually write sidecars (default is a dry run)")
    ap.add_argument("--undo", action="store_true", help="revert the most recent --write")
    ap.add_argument("--best-label", default="Green")
    ap.add_argument("--other-label", default="Yellow")
    args = ap.parse_args()
    return cmd_undo(args) if args.undo else cmd_plan_or_write(args)


if __name__ == "__main__":
    sys.exit(main())
