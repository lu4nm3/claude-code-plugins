# lightroom-classic

Skills for working with Adobe Lightroom Classic.

| skill | what it does |
|---|---|
| [`cull-raw-photos`](skills/cull-raw-photos/SKILL.md) | Groups a folder of Canon CR3 raws into moments, picks the best frame of each, and writes Green/Yellow color labels to XMP sidecars that Lightroom Classic reads |

## Install

From a local clone of this repository:

```
/plugin marketplace add /path/to/claude-code-plugins
/plugin install lightroom-classic@luis-plugins
```

Or straight from GitHub:

```
/plugin marketplace add lu4nm3/claude-code-plugins
/plugin install lightroom-classic@luis-plugins
```

## Requirements

- Python 3.9 or newer.
- [uv](https://docs.astral.sh/uv/) is recommended. The scripts that need Pillow declare it as inline metadata, so `uv run` installs it for them. Without uv, run `python3 -m pip install --user pillow` first.
- You don't need exiftool, a raw decoder, or Lightroom on the machine running the cull. The scripts read the JPEG previews embedded in each CR3 using plain Python.
- Only Canon `.CR3` files are supported.

## cull-raw-photos

### What it does

You point Claude at a folder of CR3 raws. Claude then:

1. Extracts the embedded previews and builds numbered contact sheets, with capture time and the gap since the previous frame on each thumbnail.
2. Groups frames that show the same moment: a burst, repeated takes of one pose, or the same framing of continuous action.
3. Compares the frames in each group, zooming into faces and eyes from the full-resolution embedded JPEG when needed, and picks the best one. Missed focus, blur and closed eyes count first, then expression, then composition, then exposure.
4. Writes one `<basename>.xmp` sidecar per raw (`IMG_0123.CR3` → `IMG_0123.xmp`). The pick in each group gets `xmp:Label="Green"` and the rest get `Yellow`. Misfires such as blank frames or a shot of the ground are all Yellow and listed so you can delete them.
5. Reports the totals (frames → moments → Greens), any close calls or weak groups, the Lightroom steps for your situation, and an undo command.

Your raw files are never modified, and nothing other than `.xmp` sidecars is added to the photo folder. Previews, contact sheets, `manifest.json`, `groups.json` and backups go in a work directory under `~/.cache/cull-raw-photos/`, so Lightroom's import doesn't pick them up.

### How to use it

Ask in plain language. Mention whether the photos are already in your Lightroom catalog, because it changes the steps:

> Cull the raws in ~/Pictures/2026-05-16-baptism. They're not in Lightroom yet.

> Pick the keepers from each burst in /Volumes/SD/DCIM/100CANON and label them green.

> These are already imported and edited in Lightroom. Re-cull them and replace the old labels.

If you don't say, Claude works it out from the sidecars already in the folder. Lightroom Classic writes a CR3's `.xmp` sidecar only once the photo is in a catalog, so sidecars written by Lightroom mean the shoot is imported, and Claude tells you to save metadata first. No sidecars means it's probably not imported. Claude asks only when the sidecars came from another tool, such as Aftershoot, and the answer isn't clear. If the folder already has color labels (from an earlier cull, by hand or by another tool such as Aftershoot), Claude tells you and asks before replacing them.

### Getting the labels into Lightroom Classic

**Not imported yet.** After the cull, use **File ▸ Import Photos and Video**, choose the folder, and pick **Add** (or Copy/Move). Lightroom reads the sidecars during import.

**Already in a catalog.** Re-reading sidecars replaces the catalog's metadata, develop settings included, with the sidecar's contents. Do it in this order:

1. **Before the cull writes anything,** select the photos in the Library module and choose **Metadata ▸ Save Metadata to File** (⌘S). You can skip this if **Catalog Settings ▸ Metadata ▸ Automatically write changes into XMP** is on.
2. Run the cull. Only the label (and `xmp:MetadataDate`) in each sidecar changes. Develop settings, keywords, ratings and everything else stay byte-for-byte the same.
3. Select the same photos and choose **Metadata ▸ Read Metadata from Files**.

Don't edit those photos in Lightroom between steps 1 and 3.

**Reviewing.** In the Library filter bar, open **Attribute** and click the green swatch to see one keeper per moment. Press 8 to make a photo Green or 7 to make it Yellow. The Yellows are alternates, not rejects.

**Custom color label sets.** The labels are stored as text and matched against the active color label set. With **Lightroom Default** they show as green and yellow. With another set they show as white: switch back to Lightroom Default, or rewrite the labels with your set's names (see below).

More detail is in [`references/lightroom.md`](skills/cull-raw-photos/references/lightroom.md).

### Undo and other commands

Claude runs these for you, but you can run them yourself. `<skill-dir>` is `skills/cull-raw-photos` inside the installed plugin, and `<work-dir>` is the one printed by `prepare.py` and given in Claude's summary.

```bash
python3 <skill-dir>/scripts/apply_labels.py --work-dir <work-dir> --undo
```

This restores the backed-up sidecars and deletes the ones the cull created. A sidecar that changed after the cull wrote it is left alone and reported. For imported photos, run **Read Metadata from Files** again afterwards.

```bash
python3 <skill-dir>/scripts/apply_labels.py --work-dir <work-dir> --write --best-label "<your green>" --other-label "<your yellow>"
```

This rewrites the labels with the names from a custom color label set (**Metadata ▸ Color Label Set ▸ Edit…**).

### Scripts

All in [`skills/cull-raw-photos/scripts/`](skills/cull-raw-photos/scripts):

| script | what it does | needs |
|---|---|---|
| `prepare.py <folder> [--recursive] [--work-dir DIR]` | extracts previews, reads capture times and existing labels, detects whether existing sidecars point to a Lightroom catalog, builds contact sheets and `manifest.json` | Pillow (`uv run`) |
| `compare.py --work-dir DIR <frames…> [--zoom x,y,w,h]` | side-by-side image of up to 9 frames with focus scores, optionally a zoomed crop from the full-res JPEG | Pillow (`uv run`) |
| `apply_labels.py --work-dir DIR [--write \| --undo]` | validates `groups.json`, dry-runs by default, writes sidecars with backups, undoes the last write | stdlib |
| `cr3.py info\|preview\|full <file.CR3> [out.jpg]` | minimal CR3 reader, also handy for debugging one file | stdlib |
| `xmp_sidecar.py` | reads and edits the label in a sidecar in place | stdlib |

### Evals

[`skills/cull-raw-photos/evals/`](skills/cull-raw-photos/evals) holds four test cases: two fresh culls, an imported shoot whose edits must survive, and a pre-labeled shoot where Claude must ask first. `build_fixture.py` builds each case's shoot from source folders under `~/Pictures` on the author's Mac, and `check_run.py` checks a run's sidecars against a snapshot. The cases can't be run elsewhere without those folders.
