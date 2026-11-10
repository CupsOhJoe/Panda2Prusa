# Panda2Prusa 🐼

**Bambu Studio → PrusaSlicer, nothing lost in translation.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Downloads](https://img.shields.io/github/downloads/CupsOhJoe/Panda2Prusa/total?label=downloads)](https://github.com/CupsOhJoe/Panda2Prusa/releases)
[![Stars](https://img.shields.io/github/stars/CupsOhJoe/Panda2Prusa?style=flat&label=stars)](https://github.com/CupsOhJoe/Panda2Prusa/stargazers)
[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-support-ffdd00?logo=buymeacoffee&logoColor=black)](https://www.buymeacoffee.com/cupsohjoe)

Convert **Bambu Studio / OrcaSlicer** `.3mf` project files into **PrusaSlicer**
`.3mf` project files — geometry *and* settings. The converted file opens in
PrusaSlicer with the Bambu process and filament settings applied, on your own
printer profile, with the right colours on the right tools.

## What carries over

**Geometry**

- **Real per-object transforms** (position, rotation, scale) — composed correctly from the
  build items and component references, not a hardcoded matrix.
- **Component / assembly structure** — Bambu splits geometry across `3D/Objects/object_N.model`
  and references them via the 3MF *production extension*; these are resolved and flattened into
  a single PrusaSlicer-style `3D/3dmodel.model`.
- **Part types and per-part extruders** — Bambu parts become PrusaSlicer volumes with
  `firstid`/`lastid` ranges (the only form PrusaSlicer keeps per-part extruders in);
  modifiers, negative volumes, support enforcers and blockers keep their type.
- **Text stays editable** — a Bambu Studio text part becomes a PrusaSlicer text volume
  (the *Emboss* tool): text, font, size, bold/italic, depth and surface mode carry over, so
  you can change the wording in PrusaSlicer. Each part also keeps its own transform
  (PrusaSlicer's volume matrix), so the text is re-created in its own plane. The font has
  to be installed on the PrusaSlicer PC; otherwise PrusaSlicer offers a substitute when the
  text is edited. Text wrapped around a curved surface comes back as PrusaSlicer's
  projected surface text once edited.
- **Painting** — colour (`paint_color` → `mmu_segmentation`), support and seam painting,
  fuzzy skin painting. The TriangleSelector bitstream is shared between the two slicers
  and is transcoded when extruders are renumbered.
- **Filament → extruder mapping** — Bambu AMS slots are sparse (a two-color print may use
  slots 1 and 3); the converter asks how to map them (or auto-compacts to extruders 1..K)
  so the model prints on a 2-tool/5-tool Prusa. Paint data, per-part extruders, purge
  volumes and colour changes are all re-encoded to match.
- **Multiple plates** — pick one or convert all. Bambu Studio stores every plate in one
  side-by-side layout; each converted plate is moved back onto the bed. Converting several
  plates writes one file per plate (`name_plate1.3mf`, `name_plate2.3mf`, ...) by default,
  or one file with each plate on its own PrusaSlicer 2.9 bed with `--plates-as-beds`.

**Settings** (new in 1.1)

- **Print settings** from `Metadata/project_settings.config`: layer heights, walls, shells,
  infill density/pattern, supports (normal or tree, auto or painted, angles, interfaces,
  distances), brim/skirt, seam and scarf seams, ironing, fuzzy skin, speeds and
  accelerations including overhang slowdown, extrusion widths, compensation, arc fitting,
  Arachne options, prime tower width and purge volumes. Written to `Metadata/Slic3r_PE.config`.
- **Filament settings** per extruder: type, colour, temperatures (bed temperature follows
  the plate type chosen in Bambu Studio), flow ratio, max volumetric speed, cooling rules,
  shrinkage compensation, abrasive flag, retraction overrides, filament start/end G-code.
- **Per-object and per-part settings** (e.g. a part with more walls or a different infill)
  become PrusaSlicer object/volume settings.
- **Layer height ranges**, **variable layer height profiles**, **per-layer colour changes
  and pauses**, and the **wipe tower position** (moved forward if PrusaSlicer's tower would
  not fit on the bed there).

Every value is checked against PrusaSlicer's option schema before it is written, so the
project loads without "unknown value" substitutions.

## Which printer does the output use?

A PrusaSlicer project always carries a printer profile. Panda2Prusa does not translate the
Bambu machine settings onto your Prusa by default — that would give you a 256 mm bed and
Bambu start G-code. Instead:

| `--printer` | What you get |
|---|---|
| `auto` (default) | The printer currently selected in your installed PrusaSlicer, copied exactly, so it opens cleanly selected. Your currently selected print and filament presets are used as the base for anything Bambu Studio has no setting for (tool-change ramming, cancel-object mode, ...). |
| `"Original Prusa XL - 5T Input Shaper 0.4 nozzle"` | Any installed preset by name — see `--list-printers`. |
| `path\to\profile.ini` or `project.3mf` | A config exported from PrusaSlicer (*File → Export → Export Config*) or a PrusaSlicer project to take the printer from. |
| `bambu` | Keep the Bambu Lab printer as-is: its bed, height, limits and retraction, all filaments through one nozzle (PrusaSlicer models the AMS as an MMU with a purge tower), generic start/end G-code. Only for PCs **without** PrusaSlicer, to preview and then switch to the real printer after opening. On a PC with PrusaSlicer this is the wrong choice: the file will not print correctly on a Prusa as-is, and the GUI asks before using it. |

In PrusaSlicer the Bambu process and filaments show up as project presets named after
the file, e.g. `sign.3mf (0.20mm Standard @BBL P2S)` and `sign.3mf (Bambu PLA Basic)`,
so your own presets are never modified. Mapped tools get the Bambu filament colour as
their tool colour so the 3D view matches (`--keep-tool-colours` to opt out).

## Install

```
pip install -r requirements.txt
```

(Requires Python 3.9+. `tkinter` ships with the standard Python installer for the GUI.)

## Usage

CLI:

```
python -m panda2prusa input.3mf output.3mf           # convert all plates, settings included
python -m panda2prusa input.3mf output.3mf --plate 1 # convert only plate 1
python -m panda2prusa input.3mf output.3mf --plates-as-beds  # all plates in one file, one bed each (PrusaSlicer 2.9+)
python -m panda2prusa --info input.3mf               # inspect: plates, filaments, settings
python -m panda2prusa --list-printers                # PrusaSlicer printer presets for --printer
python -m panda2prusa input.3mf output.3mf --printer "Original Prusa XL - 2T Input Shaper 0.4 nozzle"
python -m panda2prusa input.3mf output.3mf --printer bambu       # no PrusaSlicer installed
python -m panda2prusa input.3mf output.3mf --map 3=2             # Bambu filament 3 -> Prusa extruder 2
python -m panda2prusa input.3mf output.3mf --keep-extruders      # no renumbering, no prompt
python -m panda2prusa input.3mf output.3mf --no-settings         # geometry only (1.0 behaviour)
python -m panda2prusa input.3mf output.3mf --no-base-presets     # layer on PrusaSlicer defaults
```

Run interactively and the CLI shows the filaments the file uses (with their colors) and
asks how to map them; the GUI pops the same question with color swatches. Lossy
conversions (for example Bambu's *auto brim*, which has no PrusaSlicer equivalent) are
reported as notes.

GUI:

```
python -m panda2prusa.gui
```

Standalone Windows exe (no Python needed to run it):

```
pip install pyinstaller
pyinstaller --onefile --windowed --name Panda2Prusa gui_launcher.py
```

The result lands in `dist\Panda2Prusa.exe` — double-click to launch the GUI. Prebuilt
exes are attached to the [GitHub releases](https://github.com/CupsOhJoe/Panda2Prusa/releases).

## What does not carry over

- Sliced toolpaths / G-code. PrusaSlicer re-slices from the converted settings; slice
  your first converted file with preview on and check it before a long print.
- Features PrusaSlicer does not have: auto brim and brim ears (written as an explicit
  outer brim of the configured width), XY hole compensation, timelapse, AMS mapping,
  pressure advance, most prime tower refinements, Bambu-specific machine G-code.
- Bambu's flush volumes. They are what the printer dumps into its purge chute on a
  filament change; PrusaSlicer purges on the wipe tower instead, so copying them would
  build a tower hundreds of millimetres deep. Purge amounts come from your PrusaSlicer
  printer and filament profiles (multi-tool printers like the XL purge very little; MMU
  printers use their own purging volumes). With `--printer bambu` the tower purges
  Bambu's prime volume per change, so its waste and time estimates read lower than
  Bambu Studio's.
- With `--plates-as-beds`, only the first plate's colour changes/pauses and wipe tower
  position are carried over, and PrusaSlicer's command line cannot slice such a file (its
  GUI can). One file per plate (the default) has neither limitation.

## Things PrusaSlicer does that are not conversion problems

- **The wipe tower is stepped and stops below the top of the model.** PrusaSlicer sizes each
  tower layer by the colour changes at that height and above, and prints no tower at all
  above the last colour change. A print whose top is a single colour has a short tower.
- **A short line floats above the tower at the end.** That is PrusaSlicer's end-of-print
  unload/ramming of the last tool, which it performs at the final layer height over the
  tower footprint. Prusa's own profiles produce the same line; it is harmless.

## Running the tests

The suite runs against any Bambu/Orca `.3mf` files you drop into `samples/` (kept out of
git), or a directory pointed at by the `B2P_SAMPLES` environment variable. Painted,
multi-plate, and multi-material files give the best coverage; everything skips cleanly
if no samples are present. When a PrusaSlicer console executable is installed (or
`PRUSA_SLICER_EXE` points at one) the converted samples are also sliced end to end.

```
pip install pytest
pytest
```

The option schema in `panda2prusa/prusa_schema.py` / `bambu_schema.py` is generated from
the slicer sources; regenerate for a new slicer version with
`python tools/gen_schema.py --prusa-ref version_2.9.6 --prusa-defaults defaults.ini`
(`defaults.ini` from `prusa-slicer-console --save defaults.ini`).

## Status

Working end to end — see [CHANGELOG.md](CHANGELOG.md) for what's in the current release
and its known limitations. Issues and PRs welcome, ideally with a sample `.3mf` that
shows the problem.

## Support

If this saved your print (or your sanity), you can
[buy me a coffee](https://www.buymeacoffee.com/cupsohjoe). ☕
