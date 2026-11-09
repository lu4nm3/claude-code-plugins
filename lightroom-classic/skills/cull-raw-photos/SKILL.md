---
name: cull-raw-photos
description: Cull a shoot of Canon CR3 raw photos for Lightroom Classic. Groups frames of the same moment (bursts, repeated poses, same scene), picks the single best frame of each group, and marks it with a Green color label and the rest Yellow by writing XMP sidecar files that Lightroom reads. Works from the JPEG previews embedded in the raws, so it's fast. Use this whenever the user wants to cull, sort, pick keepers or selects, find the best shots, remove near-duplicates from bursts, or color-label a folder of raw photos (especially .CR3 or a Lightroom import folder), even if they don't say "cull" or "XMP".
---

# Cull raw photos for Lightroom Classic

The result is one XMP sidecar per raw file (`IMG_0123.CR3` → `IMG_0123.xmp`, saved next to it). In each group of frames showing the same moment, the best frame gets `xmp:Label="Green"` and every other frame gets `xmp:Label="Yellow"`. Lightroom Classic reads the sidecar and shows these as color labels, so the photographer can filter to Green and get one keeper per moment. Never modify the raw files themselves, and don't put anything else in the photo folder.

A CR3 is 30–60 MB, but it carries a 1620 px preview JPEG and a full-resolution JPEG inside it. The bundled scripts read those directly using plain Python, with no exiftool or raw decoder needed, so you never have to open the raws.

## Scripts

Everything is in `scripts/` next to this file. Refer to the scripts by absolute path (`<skill-dir>/scripts/...`).

| script | what it does | needs |
|---|---|---|
| `prepare.py` | extract previews, read capture times, build numbered contact sheets and `manifest.json` | Pillow |
| `compare.py` | side-by-side image of chosen frames, optionally zoomed from the full-res JPEG | Pillow |
| `apply_labels.py` | validate `groups.json`, dry-run, write sidecars, undo | stdlib |
| `cr3.py`, `xmp_sidecar.py` | helpers used by the above | stdlib |

Run the Pillow scripts with `uv run <script>`. Their inline metadata makes uv install Pillow on the fly. If uv isn't available, run `python3 -m pip install --user pillow` and use `python3` instead.

All working files (previews, sheets, manifest, groups, backups) go in a **work directory outside the photo folder**. The default is `~/.cache/cull-raw-photos/<folder>-<hash>/`. Keeping them out matters because Lightroom's import would otherwise pick up the extracted JPEGs as photos.

## Workflow

### 1. Check where the photos are in Lightroom

Lightroom reads sidecars only when it imports photos, or when you explicitly tell it to re-read them. If the photos are **already in a Lightroom catalog** and have edits, re-reading the sidecars replaces the catalog's develop settings with whatever the sidecar contains. So before writing anything, the sidecars need to hold the current edits. If the user hasn't said whether the photos are imported, ask once. If they are imported, have the user select the photos in Lightroom and run **Metadata ▸ Save Metadata to File** (⌘S) *before* step 5. This doesn't block steps 2–4, so carry on with those. `references/lightroom.md` has the details for this and for the hand-off.

### 2. Prepare

```bash
uv run <skill-dir>/scripts/prepare.py "/path/to/shoot" [--recursive]
```

The script prints the work dir, the list of contact sheets, any files it couldn't read, and how many frames **already have a color label**. If many frames are already labeled, the folder may have been culled before, by hand or by another tool. Mention this to the user now rather than surprising them at the end.

`manifest.json` lists every frame in capture order: `index` (the `#` on the sheets), `file`, `time`, `gap_s` (seconds since the previous frame), `sharpness`, and existing sidecar label/rating.

### 3. Group frames into moments

Look at the contact sheets in order with the Read tool. Each sheet holds 16 frames in capture order. Each frame shows its `#`, file name, capture time and the gap since the previous frame. An **orange bar** on a frame's left edge means a pause of 20 s or more came before it. That's a likely start of a new moment, but not a certain one. If a thumbnail is too small to judge, open its full preview at `<work-dir>/previews/<name>.jpg`.

**What counts as the same moment.** Frames belong together when they show the same subjects, in the same place, in essentially the same pose or action, framed the same way, and differ only in small things: expression, a blink, a slight head turn, a hand moving, a small camera shift, the frames of one burst. Start a new group when something the viewer would notice changes:

- the people change (someone joins, leaves, or a different person is the subject)
- the pose or action changes meaningfully (standing → sitting, looking at the camera → looking away, a different activity)
- the background or location changes
- the photographer deliberately reframes (wide → tight, horizontal → vertical, a new angle)

When you're torn, ask: *would the photographer plausibly want both of these frames in the final gallery?* If yes, they're different moments. If one makes the other redundant, they're the same moment. Over-splitting leaves near-duplicate Greens, and over-merging throws away real keepers. Both make the cull less useful, so aim for the grouping a thoughtful second shooter would make.

Use time as evidence, not as a rule. A **burst** (frames under about a second apart) is one moment unless something from the list above changes inside it. The subject moving within the frame doesn't count. Two frames five seconds apart, though, can be different pictures (a vertical after a horizontal, for example). One moment rarely runs longer than a minute or two.

**Repetitive action** (a kid on a swing, someone pacing, a dance move done again and again) moves the subject through the same positions on every cycle: swing close, swing far, close again. The photographer wants **one keeper per framing**: each time they zoomed, moved or recomposed, they were making a new picture, and they expect a Green from it. Two mistakes are easy to make here, and they pull in opposite directions:

- **Splitting on the cycle.** Near and far on the same swing, shot from the same spot with the same framing and the same people, is one moment. Don't make one group of the "close" frames and another of the "far" frames. Split a sequence along time, at the frame where the framing changes, never by where the subject is in the cycle.
- **Merging across framings.** The same activity is not the same moment. A sequence of 30 frames of one kid on one swing usually holds several framings, and calling it one group because "it's all the kid on the swing" throws away keepers the photographer shot on purpose.

A framing change during continuous action is easy to miss, because the photographer keeps shooting while zooming or walking, so each frame looks like its neighbour. Look for it this way:

- **Compare frames at the same point in the cycle**, such as the swing at its closest in each burst. If the subject is clearly bigger or smaller than at the same point earlier, the photographer zoomed or stepped in.
- **Look at the edges of the frame, not the subject.** A pole, the next swing, a slide or a bystander's shoulder that enters, leaves or moves across the frame means the camera moved, even when the subject looks the same.
- **Watch the other people.** A parent who was watching the child and now smiles at the camera, or who steps in to push, makes a different picture with the same framing.
- **Compare the ends of a long group.** For any group over about 12 frames or 15 seconds, run `compare.py` on its first and last frames and three or four in between. If the first and last wouldn't pass as takes of the same picture, the framing changed somewhere inside. Find where and split there. The change usually falls at a pause of two seconds or more between bursts, not inside a burst.

Small drift doesn't count. Hand-held framing wanders a little from burst to burst, and a split needs a difference the photographer would have made on purpose. As a rough guide, a minute of continuous action shot by someone moving around gives about one group every 5–15 frames. If you end up with one group for the whole minute, or a group for every burst, look again. A long pause (an orange bar) followed by more of the same action also starts a new group. If the photographer comes back to an earlier framing after shooting a different one, the frames from both visits can share one non-contiguous group.

Apart from that case, groups are contiguous runs in capture order. A moment can continue across the boundary between two sheets, so compare the last frames of one sheet with the first frames of the next. A frame with no near-duplicates is its own group of one, and it becomes Green.

**Misfires** aren't moments: a blank, black or completely blurred frame, a shot of the ground, the lens cap. Put them in a group with `"reject": true` and no `best`. Every frame in that group becomes Yellow, and you list them in the hand-back so the user can delete them.

Before moving on, look over your groups as a whole. If two groups would give the user near-identical Greens (same people, same spot, same framing), merge them. If one group spans framings the photographer clearly chose on purpose, split it. Any group much larger than the rest deserves the first-and-last comparison described above.

On shoots with more than a few sheets, write `<work-dir>/groups.json` as you go (after each sheet) instead of holding every group in your head. The format is in step 5.

### 4. Pick the best frame in each group

For every group with two or more frames, build a comparison image and look at it:

```bash
uv run <skill-dir>/scripts/compare.py --work-dir <work-dir> 27 28 29
```

When frames look identical at preview size, zoom into the subject's face or eyes to compare focus and blinks. The crop comes from the full-resolution embedded JPEG. `--zoom x,y,w,h` takes fractions of the frame, and you can estimate them from the comparison image:

```bash
uv run <skill-dir>/scripts/compare.py --work-dir <work-dir> 27 28 29 --zoom 0.3,0.2,0.4,0.25
```

A comparison image holds up to 9 frames. For a bigger group, compare in rounds: pick 2–3 finalists from each batch, then compare the finalists.

Judge in this order. The order reflects what can't be fixed in Lightroom versus what can:

1. **Dealbreakers.** Missed focus on the subject (especially the eyes), motion blur or camera shake, closed or half-closed eyes, something blocking the subject, an awkward crop through the subject. None of these can be fixed later.
2. **Expression and moment.** A genuine expression, eye contact where it was intended, a mouth that isn't caught mid-word, the peak of the action. In group photos, prefer the frame where the most people look good. Check every face, not just the main subject.
3. **Composition.** Cleaner framing, a level horizon, no stray elements entering the edge, no limbs cut at the joints.
4. **Exposure, last.** Raws have a lot of latitude. Only let exposure decide when a frame is badly clipped where it matters.

Each comparison tile shows a focus score. Without `--zoom` it's a **frame** score that mostly measures how much in-focus detail the whole frame has. A wide frame full of sharp wood chips will outscore a tight, perfectly focused face several times over, so don't rank frames by it. With `--zoom`, the score is computed on the same crop in every frame and the sharpest crop is starred. That comparison is fair, and it's a good tiebreaker between frames that look the same. Either way, trust what you see over the number.

Every moment gets exactly one Green, even when every frame is weak. That's the rule the user asked for. Reject groups of misfires are the only exception. Note weak groups in the summary instead of leaving them unlabeled. Give each pick a short note saying why it won. Those notes are what the user reads to trust or overrule your choices.

### 5. Write the labels

Write `<work-dir>/groups.json`. Members and best can be `#` numbers or file names:

```json
{"groups": [
  {"id": 1, "desc": "swing, close to camera", "members": [9, 10, 11, 12], "best": 10, "note": "big smile, eyes sharp; #11 blinked"},
  {"id": 2, "desc": "mom pushing swing", "members": [13], "best": 13},
  {"id": 3, "desc": "blank frame", "members": [14], "reject": true, "note": "all white, accidental"}
]}
```

Do a dry run first. It validates the groups: every frame is in exactly one group, every best is one of its group's members, and no frame is left out. It also reports what would change:

```bash
python3 <skill-dir>/scripts/apply_labels.py --work-dir <work-dir>
```

Fix any problems it reports. If the dry run says it would replace labels this tool doesn't use (Red, Blue, Purple), or a lot of existing Greens and Yellows, ask the user before writing. Those labels are probably someone's earlier work. Otherwise write:

```bash
python3 <skill-dir>/scripts/apply_labels.py --work-dir <work-dir> --write
```

Writing backs up every sidecar it modifies. It changes only the label and `xmp:MetadataDate`. Lightroom Classic and tools like Aftershoot often store the label twice, as `xmp:Label="Red"` and `photoshop:LabelColor="red"`. The script reads either one and keeps both matching when it writes. Develop settings, keywords, ratings and everything else stay byte-for-byte the same. `--undo` reverts the most recent write, and it skips any sidecar that Lightroom has rewritten since. If the user uses a custom Lightroom color label set, pass `--best-label` and `--other-label` with that set's names (see the reference).

### 6. Hand back

Tell the user:

- the totals: N frames → G moments → G Green, N − G Yellow
- anything worth a second look: groups where the pick was a close call, groups where every frame was weak, misfires you marked Yellow (by file name, so they can be deleted), frames that couldn't be read, labels you replaced
- the exact Lightroom steps for their situation (not yet imported vs. already in the catalog), taken from `references/lightroom.md`
- the undo command, with the work dir filled in
