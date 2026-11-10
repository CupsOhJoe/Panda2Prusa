# Changelog

## v1.1.0 — 2026-11-09

### Added
- Print settings: layers, walls, shells, infill, supports, brim/skirt, seams, ironing,
  fuzzy skin, speeds, accelerations, widths, compensation, arc fitting, Arachne, prime tower.
- Filament settings per extruder: type, colour, temperatures (per Bambu plate type), flow,
  max volumetric speed, cooling, shrinkage, abrasive flag, retraction overrides, start/end G-code.
- Printer targeting: the printer selected in your PrusaSlicer by default; `--printer` takes an
  installed preset name, an exported `.ini`/`.3mf`, or `bambu` (Bambu printer as-is, for PCs
  without PrusaSlicer; the GUI warns before using it).
- Your selected print and filament presets fill in what Bambu has no setting for.
- Mapped tools get the Bambu filament colour (`--keep-tool-colours` to opt out).
- Per-object and per-part settings.
- Support, seam and fuzzy-skin painting.
- Editable text parts for PrusaSlicer's text tool (text, font, size, style, depth, surface mode).
- Colour changes, pauses, layer height ranges and variable layer height profiles.
- `--plates-as-beds`: all plates in one file on PrusaSlicer 2.9 beds.
- CLI: `--printer`, `--no-settings`, `--no-base-presets`, `--keep-tool-colours`,
  `--prusa-config-dir`, `--list-printers`; `--info` shows preset names.
- GUI: printer selector, settings and beds checkboxes, result log.
- Tests for config handling, presets, translation rules, plates and text, plus end-to-end
  slicing with PrusaSlicer's command line when installed.

### Changed
- Converting several plates writes one file per plate (`name_plateN.3mf`).
- Every part keeps its transform as PrusaSlicer volume matrix metadata.
- Bambu Studio 2.x nozzle-variant vectors are collapsed to the active variant.
- Every value is checked against PrusaSlicer's option schema; bad values are fixed or
  dropped with a note.

### Fixed
- Objects of plate 2 and later landed outside the print volume.
- Modifiers, negative volumes, support enforcers and blockers were printed as solid parts.
- Bambu's flush volumes were used as purge volumes and built a wipe tower hanging off the
  bed; PrusaSlicer's own purging is used, and the tower is moved forward if it would not fit.
- A project preset selected in PrusaSlicer left the output without the printer's own print
  and filament values; the printer's default profiles are used instead.

### Known limitations
- Toolpaths are not carried over; PrusaSlicer re-slices.
- No equivalent, dropped with a note: auto brim/brim ears (written as outer brim), XY hole
  compensation, timelapse, AMS mapping, pressure advance, Bambu machine G-code.
- `--plates-as-beds`: only plate 1's colour changes and wipe tower position; CLI cannot slice it.
- Multi-colour projects on a single-nozzle printer without MMU keep extruder assignments.
- Text: the font must be installed on the PrusaSlicer PC; text wrapped around a curved
  surface is re-projected when edited.
- Schema generated from PrusaSlicer 2.9.6; regenerate with `tools/gen_schema.py` for a new major.

## v1.0.0 — 2026-07-11

### Added
- Reads Bambu Studio / OrcaSlicer project files: referenced object files, component
  transforms, build items, plates, per-part extruders.
- Writes PrusaSlicer-native output: one merged mesh per object with `firstid`/`lastid`
  volumes, so part names and extruders survive import.
- Per-triangle painting, re-encoded when filament slots are renumbered.
- Filament mapping: sparse AMS slots compacted to sequential extruders; CLI and GUI ask
  how to map them (`--map`, `--keep-extruders` for scripts).
- Plate selection (`--plate 1`), `--info`, Tkinter GUI, standalone Windows exe.

### Known limitations
- Slicing not tested end to end; check the first converted file in preview.
- Object/volume settings beyond name and extruder are not carried over.
