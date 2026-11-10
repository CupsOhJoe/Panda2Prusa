"""PrusaSlicer configuration model.

Two jobs live here:

* ``PrusaConfig`` holds a flat ``key -> serialized value`` map in exactly the text
  form PrusaSlicer uses in ``Metadata/Slic3r_PE.config`` (``; key = value`` lines)
  and in ``.ini`` presets (``key = value``). Vector values are kept serialized
  (``0.4,0.4`` / ``"Prusament PLA";PLA``) and split/joined on demand with the same
  escaping rules as PrusaSlicer's ``escape_strings_cstyle``.
* ``PrusaInstall`` reads an installed PrusaSlicer's configuration directory: the
  presets currently selected in ``PrusaSlicer.ini``, user presets, and the vendor
  bundles (``vendor/*.ini``) whose presets inherit from each other.
"""

from __future__ import annotations

import os
import re
import zipfile
from typing import Iterable, Optional

from . import prusa_schema as S

VECTOR_TYPES = frozenset(
    {"floats", "ints", "bools", "strings", "percents", "floatsorpercents", "enums", "points"}
)
SLIC3R_PE_CONFIG = "Metadata/Slic3r_PE.config"


def option_type(key: str) -> Optional[str]:
    info = S.OPTIONS.get(key)
    return info[0] if info else None


def is_vector(key: str) -> bool:
    return option_type(key) in VECTOR_TYPES


def is_nullable(key: str) -> bool:
    info = S.OPTIONS.get(key)
    return bool(info and info[1])


def enum_values(key: str) -> Optional[tuple]:
    info = S.OPTIONS.get(key)
    return info[2] if info else None


# -- string escaping (mirrors libslic3r/Config.cpp) -----------------------------

def escape_string(s: str) -> str:
    return s.replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n")


def unescape_string(s: str) -> str:
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            i += 1
            c = s[i]
            out.append("\r" if c == "r" else "\n" if c == "n" else c)
        else:
            out.append(c)
        i += 1
    return "".join(out)


def escape_strings(values: Iterable[str]) -> str:
    values = list(values)
    parts = []
    for s in values:
        quote = (len(values) == 1 and s == "") or any(c in s for c in ' ;\t\\"\r\n')
        if quote:
            buf = ['"']
            for c in s:
                if c in '\\"':
                    buf.append("\\" + c)
                elif c == "\r":
                    buf.append("\\r")
                elif c == "\n":
                    buf.append("\\n")
                else:
                    buf.append(c)
            buf.append('"')
            parts.append("".join(buf))
        else:
            parts.append(s)
    return ";".join(parts)


def unescape_strings(text: str) -> list:
    out: list = []
    if text == "":
        return out
    i, n = 0, len(text)
    while True:
        while i < n and text[i] in " \t":
            i += 1
        if i >= n:
            return out
        buf = []
        if text[i] == '"':
            i += 1
            while i < n and text[i] != '"':
                c = text[i]
                if c == "\\" and i + 1 < n:
                    i += 1
                    c = text[i]
                    c = "\r" if c == "r" else "\n" if c == "n" else c
                buf.append(c)
                i += 1
            i += 1  # closing quote
        else:
            while i < n and text[i] != ";":
                buf.append(text[i])
                i += 1
        out.append("".join(buf))
        while i < n and text[i] in " \t":
            i += 1
        if i >= n:
            return out
        i += 1  # the ';'
        if i >= n:
            out.append("")
            return out


def split_vector(key: str, text: str) -> list:
    if text is None:
        return []
    if option_type(key) == "strings":
        return unescape_strings(text)
    if text == "":
        return []
    return text.split(",")


def join_vector(key: str, values: Iterable[str]) -> str:
    values = [str(v) for v in values]
    if option_type(key) == "strings":
        return escape_strings(values)
    return ",".join(values)


# -- the config -------------------------------------------------------------------

class PrusaConfig:
    """Ordered ``key -> serialized value`` map."""

    def __init__(self, data: Optional[dict] = None):
        self._d: dict = dict(data) if data else {}

    # basic mapping
    def __contains__(self, key: str) -> bool:
        return key in self._d

    def __len__(self) -> int:
        return len(self._d)

    def keys(self):
        return self._d.keys()

    def items(self):
        return self._d.items()

    def get(self, key: str, default=None):
        return self._d.get(key, default)

    def set(self, key: str, value) -> None:
        if isinstance(value, (list, tuple)):
            value = join_vector(key, value)
        elif isinstance(value, bool):
            value = "1" if value else "0"
        self._d[key] = str(value)

    def pop(self, key: str, default=None):
        return self._d.pop(key, default)

    def copy(self) -> "PrusaConfig":
        return PrusaConfig(self._d)

    def update_from(self, other: "PrusaConfig", keys: Optional[Iterable[str]] = None) -> None:
        """Copy values from ``other`` (all keys, or only ``keys`` that it has)."""
        if keys is None:
            self._d.update(other._d)
        else:
            for k in keys:
                if k in other._d:
                    self._d[k] = other._d[k]

    # vectors
    def vector(self, key: str) -> Optional[list]:
        if key not in self._d:
            return None
        return split_vector(key, self._d[key])

    def set_vector(self, key: str, values: Iterable[str]) -> None:
        self._d[key] = join_vector(key, values)

    def resize_vectors(self, n: int, keys: Iterable[str]) -> None:
        """Make the listed vector keys exactly ``n`` long (repeat the last value)."""
        for k in keys:
            if k not in self._d or not is_vector(k):
                continue
            vals = self.vector(k)
            if not vals:
                default = S.DEFAULTS.get(k, "")
                vals = split_vector(k, default) or [""]
            if len(vals) < n:
                vals = vals + [vals[-1]] * (n - len(vals))
            elif len(vals) > n:
                vals = vals[:n]
            self.set_vector(k, vals)

    # -- parsing / serialization --------------------------------------------------
    @classmethod
    def from_ini_text(cls, text: str) -> "PrusaConfig":
        """Flat ini (user preset / exported config). Section headers are ignored."""
        cfg = cls()
        for line in text.splitlines():
            line = line.strip("\r")
            if not line or line.lstrip().startswith(("#", ";", "[")):
                continue
            k, sep, v = line.partition("=")
            if not sep:
                continue
            cfg._d[k.strip()] = v.lstrip(" ").rstrip()
        return cfg

    @classmethod
    def from_slic3r_pe_text(cls, text: str) -> "PrusaConfig":
        """``; key = value`` lines as stored in Metadata/Slic3r_PE.config."""
        cfg = cls()
        for line in text.splitlines():
            line = line.strip("\r")
            if not line.startswith(";"):
                continue
            body = line[1:].strip()
            if not body or body.startswith("generated by"):
                continue
            k, sep, v = body.partition("=")
            if not sep:
                continue
            cfg._d[k.strip()] = v.lstrip(" ").rstrip()
        return cfg

    @classmethod
    def from_3mf(cls, path: str) -> Optional["PrusaConfig"]:
        with zipfile.ZipFile(path) as z:
            if SLIC3R_PE_CONFIG not in z.namelist():
                return None
            return cls.from_slic3r_pe_text(z.read(SLIC3R_PE_CONFIG).decode("utf-8", "replace"))

    @classmethod
    def from_file(cls, path: str) -> "PrusaConfig":
        """Load a PrusaSlicer ``.ini`` (exported config / preset) or a PrusaSlicer ``.3mf``."""
        if path.lower().endswith(".3mf"):
            cfg = cls.from_3mf(path)
            if cfg is None:
                raise ValueError(f"{path!r} has no {SLIC3R_PE_CONFIG}; not a PrusaSlicer project")
            return cfg
        with open(path, encoding="utf-8", errors="replace") as f:
            return cls.from_ini_text(f.read())

    def to_slic3r_pe_text(self, generator: str = "panda2prusa") -> str:
        lines = [f"; generated by {generator}", ""]
        for k in sorted(self._d):
            lines.append(f"; {k} = {self._d[k]}")
        return "\n".join(lines) + "\n"

    def to_ini_text(self) -> str:
        return "".join(f"{k} = {self._d[k]}\n" for k in sorted(self._d))


# -- an installed PrusaSlicer -----------------------------------------------------

_SECTION_RE = re.compile(r"^\[([^\]]+)\]\s*$")


def parse_ini_sections(text: str) -> dict:
    """``{section_name: {key: value}}`` preserving order; keys before any section go to ''."""
    sections: dict = {}
    current = sections.setdefault("", {})
    for raw in text.splitlines():
        line = raw.strip("\r")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _SECTION_RE.match(line.strip())
        if m:
            current = sections.setdefault(m.group(1), {})
            continue
        k, sep, v = line.partition("=")
        if sep:
            current[k.strip()] = v.lstrip(" ").rstrip()
    return sections


PRESET_KINDS = ("print", "filament", "printer")
_META_KEYS = {"renamed_from", "alias", "inherits"}


class PrusaInstall:
    """Read-only view of a PrusaSlicer configuration directory."""

    def __init__(self, config_dir: str):
        self.config_dir = config_dir
        ini_path = os.path.join(config_dir, "PrusaSlicer.ini")
        self.ini: dict = {}
        if os.path.isfile(ini_path):
            with open(ini_path, encoding="utf-8", errors="replace") as f:
                self.ini = parse_ini_sections(f.read())
        self._bundles: Optional[list] = None
        self._resolved: dict = {}

    # -- discovery ------------------------------------------------------------------
    @staticmethod
    def candidate_dirs() -> list:
        env = os.environ.get("PRUSA_SLICER_CONFIG_DIR")
        out = [env] if env else []
        appdata = os.environ.get("APPDATA")
        home = os.path.expanduser("~")
        bases = []
        if appdata:
            bases.append(appdata)
        bases.append(os.path.join(home, "Library", "Application Support"))
        bases.append(os.path.join(home, ".config"))
        for base in bases:
            for name in ("PrusaSlicer", "PrusaSlicer-beta", "PrusaSlicer-alpha"):
                out.append(os.path.join(base, name))
        return out

    @classmethod
    def find(cls, config_dir: Optional[str] = None) -> Optional["PrusaInstall"]:
        for d in [config_dir] if config_dir else cls.candidate_dirs():
            if d and os.path.isfile(os.path.join(d, "PrusaSlicer.ini")):
                return cls(d)
        return None

    @property
    def version(self) -> str:
        return self.ini.get("", {}).get("version", "")

    # -- selections -----------------------------------------------------------------
    def selected(self, kind: str) -> Optional[str]:
        name = self.ini.get("presets", {}).get(kind, "")
        return name or None

    def selected_filaments(self) -> list:
        presets = self.ini.get("presets", {})
        out = []
        first = presets.get("filament", "")
        if first:
            out.append(first)
        i = 1
        while f"filament_{i}" in presets:
            out.append(presets[f"filament_{i}"])
            i += 1
        return out

    # -- presets --------------------------------------------------------------------
    def user_preset(self, kind: str, name: str) -> Optional[PrusaConfig]:
        path = os.path.join(self.config_dir, kind, name + ".ini")
        if not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8", errors="replace") as f:
            cfg = PrusaConfig.from_ini_text(f.read())
        for k in _META_KEYS:
            cfg.pop(k, None)
        return cfg

    def user_preset_names(self, kind: str) -> list:
        d = os.path.join(self.config_dir, kind)
        if not os.path.isdir(d):
            return []
        return sorted(os.path.splitext(fn)[0] for fn in os.listdir(d) if fn.lower().endswith(".ini"))

    def bundles(self) -> list:
        """Parsed vendor bundles: list of (vendor_id, sections)."""
        if self._bundles is None:
            self._bundles = []
            vendor_dir = os.path.join(self.config_dir, "vendor")
            if os.path.isdir(vendor_dir):
                for fn in sorted(os.listdir(vendor_dir)):
                    if not fn.lower().endswith(".ini"):
                        continue
                    with open(os.path.join(vendor_dir, fn), encoding="utf-8", errors="replace") as f:
                        self._bundles.append((os.path.splitext(fn)[0], parse_ini_sections(f.read())))
        return self._bundles

    def _resolve_section(self, sections: dict, kind: str, name: str, seen: tuple) -> Optional[dict]:
        sec = sections.get(f"{kind}:{name}")
        if sec is None or name in seen:
            return None
        cache_key = (id(sections), kind, name)
        if cache_key in self._resolved:
            return dict(self._resolved[cache_key])
        out: dict = {}
        for parent in sec.get("inherits", "").split(";"):
            parent = parent.strip()
            if parent:
                base = self._resolve_section(sections, kind, parent, seen + (name,))
                if base:
                    out.update(base)
        out.update({k: v for k, v in sec.items() if k not in _META_KEYS})
        self._resolved[cache_key] = dict(out)
        return out

    def vendor_preset(self, kind: str, name: str) -> Optional[PrusaConfig]:
        for _vendor, sections in self.bundles():
            if f"{kind}:{name}" in sections:
                data = self._resolve_section(sections, kind, name, ())
                if data is not None:
                    return PrusaConfig(data)
        return None

    def preset(self, kind: str, name: str) -> Optional[PrusaConfig]:
        """User preset if one exists under that name, else the vendor (system) preset."""
        if not name:
            return None
        return self.user_preset(kind, name) or self.vendor_preset(kind, name)

    def preset_names(self, kind: str) -> list:
        """Installed (non-template) presets of ``kind``: user presets plus vendor presets
        whose printer model/variant is enabled in PrusaSlicer.ini."""
        names = list(self.user_preset_names(kind))
        for vendor, sections in self.bundles():
            installed = self.ini.get(f"vendor:{vendor}", {})
            models: dict = {}
            for k, v in installed.items():
                if k.startswith("model:"):
                    models[k[6:]] = {x.strip() for x in v.split(";") if x.strip()}
            if not models:
                continue
            for section in sections:
                if not section.startswith(f"{kind}:"):
                    continue
                name = section.split(":", 1)[1]
                if name.startswith("*") or name in names:
                    continue
                data = self._resolve_section(sections, kind, name, ())
                if not data:
                    continue
                if kind == "printer":
                    model = data.get("printer_model", "")
                    variant = data.get("printer_variant", "")
                    if model in models and (not models[model] or variant in models[model]):
                        names.append(name)
                else:
                    names.append(name)
        return names

    def extruder_count(self, printer_cfg: PrusaConfig) -> int:
        vals = printer_cfg.vector("nozzle_diameter") or []
        return max(1, len(vals))
