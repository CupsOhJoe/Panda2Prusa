"""Minimal Tkinter GUI: python -m panda2prusa.gui

It inspects the file first (producer, plates, filaments, settings), lets you pick
which plate(s) to convert and which PrusaSlicer printer profile the converted
settings should target, asks how to map the filaments, and reports what was written.
"""

from __future__ import annotations

import os
import threading
import traceback
from tkinter import (
    Tk,
    Toplevel,
    StringVar,
    BooleanVar,
    Label,
    Button,
    Checkbutton,
    Entry,
    Frame,
    filedialog,
    messagebox,
    END,
    Text,
    DISABLED,
    NORMAL,
)
from tkinter import ttk

from .convert import (
    PLATES_BEDS,
    PLATES_SPLIT,
    PRINTER_AUTO,
    PRINTER_BAMBU,
    convert_file,
    current_printer,
    describe,
    filament_label,
    installed_printers,
    suggest_extruder_map,
    used_filaments,
)

_AUTO_LABEL = "Auto: printer currently selected in PrusaSlicer"
_BAMBU_LABEL = "Bambu Lab printer as-is (only if PrusaSlicer is not installed)"
_BROWSE_LABEL = "Browse for an exported .ini / PrusaSlicer .3mf…"


def _center_on_screen(win, min_w: int = 0, min_h: int = 0) -> None:
    """Place a window in the middle of the monitor."""
    win.update_idletasks()
    w = max(win.winfo_reqwidth(), min_w)
    h = max(win.winfo_reqheight(), min_h)
    x = (win.winfo_screenwidth() - w) // 2
    y = (win.winfo_screenheight() - h) // 2
    win.geometry(f"{w}x{h}+{max(x, 0)}+{max(y, 0)}")


class App:
    def __init__(self, master: Tk):
        self.master = master
        master.title("Panda2Prusa - Bambu to Prusa 3mf converter")
        master.minsize(620, 460)

        self.input_var = StringVar()
        self.output_var = StringVar()
        self.plate_var = StringVar(value="all")
        self.beds_var = BooleanVar(value=False)
        self.settings_var = BooleanVar(value=True)
        self.printer_var = StringVar()
        self.printer_file: str | None = None

        row = 0
        Label(master, text="Input Bambu / Orca .3mf:").grid(row=row, column=0, sticky="w", padx=8, pady=(10, 2))
        row += 1
        Entry(master, textvariable=self.input_var, width=60).grid(row=row, column=0, padx=8, sticky="we")
        Button(master, text="Browse…", command=self.pick_input).grid(row=row, column=1, padx=(4, 8))
        row += 1
        Label(master, text="Output Prusa .3mf:").grid(row=row, column=0, sticky="w", padx=8, pady=(8, 2))
        row += 1
        Entry(master, textvariable=self.output_var, width=60).grid(row=row, column=0, padx=8, sticky="we")
        Button(master, text="Browse…", command=self.pick_output).grid(row=row, column=1, padx=(4, 8))
        row += 1
        Label(master, text="Plates (e.g. 'all' or '1' or '1,2'):").grid(row=row, column=0, sticky="w", padx=8, pady=(8, 2))
        row += 1
        plate_row = Frame(master)
        plate_row.grid(row=row, column=0, columnspan=2, padx=8, sticky="w")
        Entry(plate_row, textvariable=self.plate_var, width=20).pack(side="left")
        Checkbutton(
            plate_row,
            text="All plates in one file on separate beds (PrusaSlicer 2.9+); default: one file per plate",
            variable=self.beds_var,
        ).pack(side="left", padx=(12, 0))
        row += 1
        Checkbutton(
            master,
            text="Convert settings too (process, filaments, colour changes, layer ranges)",
            variable=self.settings_var,
            command=self._toggle_settings,
        ).grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 2))
        row += 1
        Label(master, text="Target PrusaSlicer printer profile:").grid(row=row, column=0, sticky="w", padx=8, pady=(4, 2))
        row += 1
        self.printer_box = ttk.Combobox(master, textvariable=self.printer_var, state="readonly", width=58)
        self.printer_box.grid(row=row, column=0, padx=8, sticky="we")
        self.printer_box.bind("<<ComboboxSelected>>", self._printer_selected)
        self._fill_printers()
        row += 1
        Button(master, text="Convert", command=self.convert, height=2).grid(
            row=row, column=0, columnspan=2, padx=8, pady=8, sticky="we"
        )
        row += 1
        self.log = Text(master, height=12, wrap="word", state=DISABLED)
        self.log.grid(row=row, column=0, columnspan=2, padx=8, pady=(0, 10), sticky="nsew")
        master.grid_columnconfigure(0, weight=1)
        master.grid_rowconfigure(row, weight=1)
        _center_on_screen(master, 620, 460)

    # -- helpers --------------------------------------------------------------
    def _log(self, msg: str) -> None:
        self.log.config(state=NORMAL)
        self.log.insert(END, msg + "\n")
        self.log.see(END)
        self.log.config(state=DISABLED)

    def _fill_printers(self) -> None:
        values = []
        cur = current_printer()
        self.prusa_installed = bool(cur)
        if cur:
            values.append(f"{_AUTO_LABEL} ({cur})")
        try:
            names = installed_printers()
        except Exception:
            names = []
        values.extend(names)
        values.append(_BAMBU_LABEL)
        values.append(_BROWSE_LABEL)
        self.printer_box["values"] = values
        self.printer_var.set(values[0])

    def _toggle_settings(self) -> None:
        self.printer_box.config(state="readonly" if self.settings_var.get() else "disabled")

    def _printer_selected(self, _event=None) -> None:
        if self.printer_var.get() == _BAMBU_LABEL and self.prusa_installed:
            keep = messagebox.askyesno(
                "Bambu printer profile",
                "This keeps the Bambu Lab printer profile in the converted file: Bambu bed size, "
                "all filaments through one nozzle with an MMU-style purge tower, generic start/end "
                "G-code.\n\n"
                "It will not print correctly on your Prusa as-is. It is meant for PCs without "
                "PrusaSlicer, where the printer is switched after opening the file.\n\n"
                "PrusaSlicer is installed here, so 'Auto' (your current printer) or a printer from "
                "the list gives a file that is ready to slice.\n\n"
                "Use the Bambu printer profile anyway?",
                icon="warning",
            )
            if not keep:
                self.printer_var.set(self.printer_box["values"][0])
            return
        if self.printer_var.get() == _BROWSE_LABEL:
            path = filedialog.askopenfilename(
                title="PrusaSlicer printer profile",
                filetypes=[("PrusaSlicer config / project", "*.ini *.3mf"), ("All files", "*.*")],
            )
            if path:
                self.printer_file = path
                values = list(self.printer_box["values"])
                label = f"File: {path}"
                if label not in values:
                    values.insert(len(values) - 1, label)
                    self.printer_box["values"] = values
                self.printer_var.set(label)
            else:
                self.printer_var.set(self.printer_box["values"][0])

    def _printer_spec(self) -> str:
        choice = self.printer_var.get()
        if choice.startswith(_AUTO_LABEL):
            return PRINTER_AUTO
        if choice == _BAMBU_LABEL:
            return PRINTER_BAMBU
        if choice.startswith("File: "):
            return choice[len("File: "):]
        return choice

    def pick_input(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("3mf files", "*.3mf")])
        if not path:
            return
        self.input_var.set(path)
        base, _ = os.path.splitext(path)
        if not self.output_var.get():
            self.output_var.set(base + "_prusa.3mf")
        try:
            project = describe(path)
            plates = project.plate_ids or ["(single)"]
            self._log(f"Loaded: {os.path.basename(path)}")
            self._log(f"  producer: {project.producer or 'unknown'}")
            self._log(f"  plates:   {', '.join(str(p) for p in plates)}")
            used = used_filaments(project)
            if used:
                self._log(f"  filaments: {', '.join(filament_label(project, f) for f in used)}")
            cfg = project.config
            if cfg:
                self._log(
                    f"  settings: {cfg.scalar('print_settings_id') or '?'} on "
                    f"{cfg.scalar('printer_settings_id') or '?'}"
                )
            else:
                self._log("  settings: none in file")
        except Exception as exc:
            self._log(f"  could not inspect: {exc}")

    def pick_output(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".3mf", filetypes=[("3mf files", "*.3mf")])
        if path:
            self.output_var.set(path)

    def convert(self) -> None:
        inp, outp = self.input_var.get(), self.output_var.get()
        if not inp or not outp:
            messagebox.showwarning("Missing paths", "Choose both an input and an output file.")
            return
        plates = None
        pv = self.plate_var.get().strip().lower()
        if pv and pv != "all":
            try:
                plates = [int(p) for p in pv.replace(",", " ").split()]
            except ValueError:
                messagebox.showwarning("Bad plate", "Plates must be 'all' or numbers like 1 or 1,2.")
                return
        try:
            project = describe(inp)
        except Exception as exc:
            messagebox.showerror("Cannot read file", str(exc))
            return
        used = used_filaments(project)
        if len(used) > 1 or used not in ([], [1]):
            self._ask_mapping(project, used, inp, outp, plates)
        else:
            self._start(inp, outp, plates, None)

    def _ask_mapping(self, project, used, inp, outp, plates) -> None:
        """This file has N colors — ask how to map them to Prusa extruders."""
        dlg = Toplevel(self.master)
        dlg.title("Filament mapping")
        dlg.transient(self.master)
        dlg.grab_set()
        Label(
            dlg,
            text=(
                f"This file uses {len(used)} filament(s).\n"
                "Choose the Prusa extruder/tool for each (e.g. a two-tool XL has 1 and 2):"
            ),
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(10, 6))

        suggestion = suggest_extruder_map(used)
        entries: dict = {}
        for i, f in enumerate(used, start=1):
            row = Frame(dlg)
            row.grid(row=i, column=0, columnspan=2, sticky="w", padx=10, pady=2)
            color = None
            if 0 < f <= len(project.filament_colors):
                c = project.filament_colors[f - 1]
                color = c[:7] if c.startswith("#") and len(c) >= 7 else None
            sw = Label(row, text="    ", relief="solid", bd=1)
            if color:
                try:
                    sw.config(bg=color)
                except Exception:
                    pass
            sw.pack(side="left", padx=(0, 6))
            Label(row, text=f"Bambu filament {filament_label(project, f)}  →  extruder").pack(side="left")
            e = Entry(row, width=4)
            e.insert(0, str(suggestion[f]))
            e.pack(side="left", padx=(6, 0))
            entries[f] = e

        def start_with(mapping) -> None:
            dlg.destroy()
            self._start(inp, outp, plates, mapping)

        def ok() -> None:
            try:
                mapping = {f: int(e.get()) for f, e in entries.items()}
            except ValueError:
                messagebox.showwarning("Bad mapping", "Extruder numbers must be integers.", parent=dlg)
                return
            start_with(mapping)

        btns = Frame(dlg)
        btns.grid(row=len(used) + 1, column=0, columnspan=2, pady=10, padx=10, sticky="we")
        Button(btns, text="Convert", command=ok, width=12).pack(side="left")
        Button(btns, text="Keep original numbers", command=lambda: start_with({})).pack(side="left", padx=6)
        Button(btns, text="Cancel", command=dlg.destroy).pack(side="right")
        _center_on_screen(dlg)

    def _start(self, inp, outp, plates, extruder_map) -> None:
        self._log(f"Converting → {os.path.basename(outp)} …")
        settings = self.settings_var.get()
        printer = self._printer_spec()
        layout = PLATES_BEDS if self.beds_var.get() else PLATES_SPLIT
        threading.Thread(
            target=self._run, args=(inp, outp, plates, extruder_map, settings, printer, layout), daemon=True
        ).start()

    def _run(self, inp, outp, plates, extruder_map, settings, printer, layout) -> None:
        try:
            result = convert_file(
                inp, outp, plates=plates, extruder_map=extruder_map, settings=settings,
                printer=printer, plate_layout=layout,
            )
            s = result.stats
            lines = [
                f"Done. {s.objects_written} objects, {s.volumes_written} volumes, "
                f"{s.build_items_written} build items, {s.painted_triangles} painted triangles."
            ]
            if len(result.outputs) > 1:
                lines.append("  Written: " + ", ".join(os.path.basename(p) for p in result.outputs))
            elif s.beds_used > 1:
                lines.append(f"  {s.beds_used} plates placed on PrusaSlicer beds 1-{s.beds_used}")
            if s.extruder_map:
                lines.append("  Extruders remapped: " + ", ".join(
                    f"{k}→{v}" for k, v in sorted(s.extruder_map.items())
                ))
            if s.settings_written:
                lines.append(f"  Settings: {result.target_description}")
                lines.append(f"  Printer profile: {s.printer_name} ({s.extruders} extruders)")
                lines.append(f"  Print profile:   {s.print_name}")
                for i, name in enumerate(s.filament_names, start=1):
                    lines.append(f"  Extruder {i}: {name}")
                if s.custom_gcodes_written:
                    lines.append(f"  Colour changes/pauses carried over: {s.custom_gcodes_written}")
                if s.layer_ranges_written:
                    lines.append(f"  Layer height ranges carried over: {s.layer_ranges_written}")
                if s.text_volumes:
                    lines.append(f"  Editable text parts: {s.text_volumes}")
            else:
                lines.append(f"  Settings not converted ({result.target_description})")
            for w in s.warnings:
                lines.append(f"  note: {w}")
            self.master.after(0, lambda: self._log("\n".join(lines)))
        except Exception:
            err = traceback.format_exc()
            self.master.after(0, lambda: self._log("ERROR:\n" + err))


def main() -> None:
    root = Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
