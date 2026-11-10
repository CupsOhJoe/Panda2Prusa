# Changelog

## v1.1.0 — 2026-11-09

Settings now convert along with the geometry.

- Print settings: layers, walls, shells, infill, supports, brim/skirt, seams, ironing,
  fuzzy skin, speeds, accelerations, widths, compensation, arc fitting, Arachne, prime tower.
- Filament settings per extruder: type, colour, temperatures (per Bambu plate type), flow,
  max volumetric speed, cooling, shrinkage, abrasive flag, retraction overrides, start/end G-code.
- Printer: targets the printer selected in your PrusaSlicer by default; `--printer` takes an
  installed preset name, an exported `.ini`/`.3mf`, or `bambu` (Bambu printer as-is, only for
  PCs without PrusaSlicer; the GUI warns before using it).
- Your selected print and filament presets fill in what Bambu has no setting for; if the
  selection is a project preset, the printer's default profiles are used.
- Purge volumes come from the PrusaSlicer profile, not Bambu's flush matrix (chute waste),
  which built a wipe tower hanging off the bed. The tower is moved forward if it would not fit.
- Mapped tools get the Bambu filament colour (`--keep-tool-colours` to opt out).
- Per-object and per-part settings; part types (modifier, negative, support enforcer/blocker).
- Support, seam and fuzzy-skin painting.
- Text parts stay editable in PrusaSlicer's text tool (text, font, size, style, depth,
  surface mode). Every part keeps its transform as volume matrix metadata.
- Colour changes, pauses, layer height ranges and variable layer height profiles.
- Plates are moved back onto the bed; several plates give one file per plate, or one file
  with PrusaSlicer 2.9 beds via `--plates-as-beds`.
- Bambu Studio 2.x nozzle-variant vectors collapsed to the active variant.
- Every value checked against PrusaSlicer's option schema; bad values fixed or dropped with a note.
- CLI: `--printer`, `--plates-as-beds`, `--no-settings`, `--no-base-presets`,
  `--keep-tool-colours`, `--prusa-config-dir`, `--list-printers`; `--info` shows preset names.
- GUI: printer selector, settings and beds checkboxes, result log.
- Tests for config handling, presets, translation rules, plates and text, plus end-to-end
  slicing with PrusaSlicer's command line when installed.

Known limitations:

- Toolpaths are not carried over; PrusaSlicer re-slices.
- No equivalent, dropped with a note: auto brim/brim ears (written as outer brim), XY hole
  compensation, timelapse, AMS mapping, pressure advance, Bambu machine G-code.
- `--plates-as-beds`: only plate 1's colour changes and wipe tower position; CLI cannot slice it.
- Multi-colour projects on a single-nozzle printer without MMU keep extruder assignments.
- Text: the font must be installed on the PrusaSlicer PC; text wrapped around a curved
  surface is re-projected when edited.
- Schema generated from PrusaSlicer 2.9.6; regenerate with `tools/gen_schema.py` for a new major.

## v1.0.0 — 2026-07-11

First public release.

- Reads Bambu Studio / OrcaSlicer project files: referenced object files, component
  transforms, build items, plates, per-part extruders.
- Writes PrusaSlicer-native output: one merged mesh per object with `firstid`/`lastid`
  volumes, so part names and extruders survive import.
- Per-triangle painting carries over; the paint bitstream is re-encoded when filament
  slots are renumbered.
- Filament mapping: sparse AMS slots are compacted to sequential extruders; CLI and GUI
  ask how to map them (`--map`, `--keep-extruders` for scripts).
- Plate selection (`--plate 1`), `--info`, Tkinter GUI, standalone Windows exe.

Known limitations:

- Slicing not tested end to end; check the first converted file in preview.
- Object/volume settings beyond name and extruder are not carried over.
