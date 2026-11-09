# Getting the labels into Lightroom Classic

Lightroom Classic stores metadata in its catalog. For proprietary raw formats like CR3, it mirrors that metadata to a `<basename>.xmp` sidecar next to the raw file, but only when told to (or when "Automatically write changes into XMP" is on). It reads sidecars back at import, or when you ask it to. Which steps the user needs depends on whether the photos are in the catalog yet.

## Photos not imported yet (simplest)

1. Run the cull. The sidecars now hold the labels.
2. In Lightroom: **File ▸ Import Photos and Video**, choose the folder, and pick **Add** (or Copy/Move). Lightroom reads each sidecar during import, so the labels show up immediately.

## Photos already in a catalog

Re-reading sidecars ("Read Metadata from Files") **replaces** the catalog's metadata for those photos with the sidecar contents. That includes develop settings, crops, keywords and ratings. If the catalog has edits that were never written to the sidecars, re-reading would lose them. So the order matters:

1. **Before the cull writes anything:** in the Library module, select the photos (⌘A in the folder) and choose **Metadata ▸ Save Metadata to File** (⌘S). Each sidecar now matches the catalog.
   - Skip this if **Catalog Settings ▸ Metadata ▸ Automatically write changes into XMP** is on. In that case the sidecars are already current.
2. Run the cull. It edits only the label in each sidecar and leaves the rest alone.
3. Back in Lightroom: select the same photos and choose **Metadata ▸ Read Metadata from Files**. You can also click the "metadata was changed externally" badge on a thumbnail and choose **Import Settings from Disk**, which does the same thing for that photo.

Don't edit those photos in Lightroom between steps 1 and 3. With auto-write on, Lightroom could rewrite a sidecar and drop the label.

## Color label sets

A label is stored as text (`xmp:Label="Green"`). Lightroom matches that text against the names in the active color label set. The default set (**Metadata ▸ Color Label Set ▸ Lightroom Default**) uses Red, Yellow, Green, Blue and Purple, which is what this skill writes. With the "Review Status" set or a custom set, Lightroom shows labels it doesn't recognize as white. Fix this in one of two ways:

- switch the set back to Lightroom Default, or
- re-run `apply_labels.py --write --best-label "<name of your green>" --other-label "<name of your yellow>"`, using the names from **Metadata ▸ Color Label Set ▸ Edit…**

## Reviewing the cull in Lightroom

- Show only the keepers: in the Library filter bar, open **Attribute** and click the green label swatch. Or use **Library ▸ Filter by Color Label ▸ Green**.
- Overrule a pick: select a photo and press 8 (Green) or 7 (Yellow). In Lightroom Classic, 6–9 set red/yellow/green/blue.
- Keep the Yellows for later. They're alternates, not rejects.

## Undo

```bash
python3 <skill-dir>/scripts/apply_labels.py --work-dir <work-dir> --undo
```

This restores the backed-up sidecars and deletes the ones the cull created. A sidecar that changed after the cull wrote it is left alone and reported. For already-imported photos, run **Read Metadata from Files** again afterwards.
