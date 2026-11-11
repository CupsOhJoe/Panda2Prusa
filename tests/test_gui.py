"""The Tkinter GUI must run a conversion in every settings mode without crashing.

Builds the real App and drives the same code path the Convert button takes, with the
Tk main loop running as it does in the app. One Tk root serves the whole module (a
second root in the same process does not always find init.tcl on Windows); each case
gets its own window. Skipped where Tk cannot open a display.
"""

import os
import time
import tkinter

import pytest

from panda2prusa import gui
from test_plates import make_project


@pytest.fixture(scope="module")
def tk_root():
    try:
        root = tkinter.Tk()
    except tkinter.TclError as exc:
        pytest.skip(f"Tk has no display: {exc}")
    root.withdraw()
    yield root
    root.destroy()


@pytest.mark.parametrize("mode", ["presets", "bambu", "none"])
def test_gui_converts_in_every_settings_mode(tk_root, tmp_path, mode):
    inp = make_project(tmp_path / "in.3mf", [(1, 1, 1, 100.0, 100.0), (2, 1, 2, 50.0, 60.0)])
    outp = str(tmp_path / "out.3mf")
    ini = tmp_path / "xl.ini"
    ini.write_text(
        "printer_settings_id = Test XL\nbed_shape = 0x0,360x0,360x360,0x360\nmax_print_height = 360\n"
        "nozzle_diameter = 0.4,0.4\nretract_length = 0.8,0.8\ngcode_flavor = marlin2\n",
        encoding="utf-8",
    )
    win = tkinter.Toplevel(tk_root)
    win.withdraw()
    try:
        app = gui.App(win)
        app.settings_var.set(mode)
        app.printer_file = str(ini)
        app.printer_var.set(f"File: {ini}")
        deadline = time.monotonic() + 120

        def poll():
            text = app.log.get("1.0", "end")
            if "Done." in text or "ERROR" in text or time.monotonic() > deadline:
                tk_root.quit()
            else:
                tk_root.after(50, poll)

        app._start(inp, outp, [1], {1: 1, 2: 2})
        tk_root.after(50, poll)
        tk_root.mainloop()
        log = app.log.get("1.0", "end")
    finally:
        win.destroy()
    assert "ERROR" not in log, log
    assert "Done." in log, log
    assert os.path.exists(outp)
