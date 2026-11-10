# Changelog

## v1.1.0 — 2026-11-09

Settings now come along with the geometry. A converted file opens in PrusaSlicer
with the Bambu process and filament settings applied, on your own printer profile.

- **Print settings** translated from `Metadata/project_settings.config` into
  `Metadata/Slic3r_PE.config`: layer heights, walls, top/bottom shells, infill
  density and pattern, supports (normal/tree, auto/manual, thresholds, interface
  layers, distances), brim and skirt, seam and scarf seams, ironing, fuzzy skin,
  speeds and accelerations (including overhang slowdown bands), extrusion widths,
  compensation, arc fitting, Arachne settings, prime tower size and position.
- **Filament settings** per extruder: type, colour, diameter, temperatures (bed
  temperature follows the plate type selected in Bambu Studio), flow ratio, max
  volumetric speed, cooling and fan rules, shrinkage, abrasive flag, retraction
  overrides, filament start/end G-code, cost and density.
- **Printer profile selection.** By default the output targets the printer
  currently selected in your installed PrusaSlicer (`--printer auto`), so it opens
  with that printer cleanly selected; print and filament presets you have selected
  serve as the base for anything Bambu Studio has no setting for. `--printer` also
  accepts an installed preset name, an exported `.ini`/PrusaSlicer `.3mf`, or
  `bambu` to keep the Bambu Lab printer as-is (bed, limits, retraction, one nozzle
  with an MMU-style purge tower, generic start/end G-code) for PCs without
  PrusaSlicer; the GUI asks before using it when PrusaSlicer is installed.
- **Purge volumes stay PrusaSlicer's.** Bambu's flush volumes are purge-chute waste,
  not wipe tower purge; fed to PrusaSlicer as purging volumes they built a wipe tower
  hundreds of millimetres deep that hung off the bed. The printer profile's purging
  settings are used instead (with `--printer bambu`, Bambu's prime volume per change),
  and the wipe tower is moved forward when PrusaSlicer's tower would not fit at the
  Bambu position.
- When the print or filament preset selected in PrusaSlicer is a project preset (left
  behind by a converted file opened earlier), the printer's default print and filament
  profiles serve as the base instead, so printer-specific values (XL tool-change
  ramming, no MMU priming lines, wipe tower cone) are never lost.
- **Tool colours.** Mapped extruders get the Bambu filament colour as their tool
  colour, so the 3D view shows the right colours even on printers whose profile
  defines tool colours (Prusa XL). `--keep-tool-colours` turns this off.
- **Per-object and per-part settings** from `model_settings.config` (walls, infill,
  supports, layer height, speeds, ...) are written as PrusaSlicer object/volume
  settings; part types carry over (modifiers, negative volumes, support enforcers
  and blockers used to be printed as solid parts).
- **Support and seam painting** (`paint_supports`, `paint_seam`, `paint_fuzzy_skin`)
  are kept alongside colour painting.
- **Text parts stay editable.** Bambu Studio's `text_info` (text, font, size, bold,
  italic, thickness, embedded depth, surface mode, letter gap) is written as
  PrusaSlicer's `slic3rpe:text` / `slic3rpe:shape` volume data, and every volume now
  carries its part transform as PrusaSlicer `matrix` metadata, so PrusaSlicer's text
  tool re-creates the text in its own plane. The font must be installed on the
  PrusaSlicer PC (a Windows font descriptor plus the face name are written; other
  systems fall back to the face name).
- **Per-layer colour changes and pauses**, **layer height ranges** and **variable
  layer height profiles** are converted to their PrusaSlicer files.
- **Plates land on the bed.** Bambu Studio keeps all plates in one side-by-side
  layout (plate 2 starts 1.2 bed widths to the right), so converting plate 2 used to
  put its objects outside the print volume. Each plate is now moved back to its own
  origin. Converting several plates writes one file per plate (`name_plateN.3mf`);
  `--plates-as-beds` puts them in one file on successive PrusaSlicer 2.9 beds instead.
- Bambu Studio 2.x "extruder variant" expanded vectors (Standard / High Flow
  nozzle) are collapsed to the variant the project prints with.
- Every value is validated against PrusaSlicer 2.9.6's option schema (types, enum
  names, ranges) before writing; anything PrusaSlicer would refuse is repaired or
  dropped with a note. The schema tables are generated from the slicer sources by
  `tools/gen_schema.py`.
- New command line options: `--printer WHICH`, `--plates-as-beds`, `--no-settings`
  (geometry-only behaviour of 1.0), `--no-base-presets` (layer the Bambu settings on
  PrusaSlicer defaults instead of your selected presets), `--keep-tool-colours`,
  `--prusa-config-dir DIR` (PrusaSlicer configuration folder when it is not in the
  usual place), `--list-printers` (PrusaSlicer printer presets available for
  `--printer`). `--info` now also prints the project's printer, process and filament
  names. Notes about lossy conversions go to stderr.
- The GUI gained a target-printer selector (current PrusaSlicer printer, any installed
  preset, the Bambu printer as-is, or an exported profile file), a "convert settings
  too" checkbox, an "all plates in one file on separate beds" checkbox, and a result
  log that lists the output files, the extruder mapping, the presets used and any
  notes.
- Test suite: unit tests for config serialization, preset inheritance and fallbacks,
  Bambu vector collapsing, the translation rules, plate placement and text parts, plus
  an end-to-end test that slices every sample with PrusaSlicer's command line (both
  printer modes) when it is installed.

Known limitations:

- Sliced toolpaths/G-code are not carried over; PrusaSlicer re-slices from the
  settings.
- Bambu-only features have no equivalent and are dropped (with a note where it
  matters): auto brim and brim ears become an explicit outer brim of the configured
  width, XY hole compensation, timelapse, AMS mapping, pressure advance, most prime
  tower refinements, Bambu machine start/end G-code (replaced by generic G-code in
  `--printer bambu` mode).
- With `--plates-as-beds`, only the first plate's colour changes/pauses and wipe tower
  position are carried over, and PrusaSlicer's command line cannot slice the file
  (the GUI can).
- A multi-colour project aimed at a single-nozzle printer without an MMU keeps its
  extruder assignments instead of becoming per-layer colour changes.
- Text parts: the font must be installed on the PrusaSlicer PC (PrusaSlicer offers a
  substitute when the text is edited), and text that Bambu Studio wrapped around a
  curved surface is re-created as PrusaSlicer's projected surface text once edited;
  the original mesh is kept until then.
- The option schema is generated from PrusaSlicer 2.9.6 sources; 2.9.7 loads the
  output without substitutions. A new PrusaSlicer major version may need the schema
  regenerated (`tools/gen_schema.py`).

## v1.0.0 — 2026-07-11

First public release.

- Reads Bambu Studio / OrcaSlicer project files with their real structure:
  referenced object files, component transforms, build items, plates, and
  per-part extruder assignments.
- Writes PrusaSlicer-native output: one merged mesh per object with
  `firstid`/`lastid` volume ranges, so part names and extruder assignments
  actually survive the import.
- Per-triangle painting carries over. The paint bitstream is decoded and
  re-encoded when filament slots are renumbered, so painted regions follow
  their filament to the new extruder.
- Filament mapping: sparse AMS slots (say, 1 and 3) get compacted to
  sequential extruders so a two-color model prints on a two-tool machine.
  The CLI and GUI both show the file's filaments (with colors) and ask how
  to map them; `--map` and `--keep-extruders` cover scripted use.
- Plate selection (`--plate 1`), file inspection (`--info`), Tkinter GUI,
  and a standalone Windows exe built with PyInstaller.

Known limitations:

- Slicing itself isn't tested end to end (PrusaSlicer `--info` load checks
  only); slice your first converted file with supports/preview on and eyeball
  it before committing a long print.
- Object/volume print settings beyond name and extruder (custom layer
  heights, modifiers, per-part profiles) aren't carried over.
