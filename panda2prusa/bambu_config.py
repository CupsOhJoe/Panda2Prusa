"""Access to Bambu Studio's ``Metadata/project_settings.config``.

The file is one JSON object holding the merged process + filament + machine
settings of the project. Scalars are strings; vectors are lists of strings. Since
Bambu Studio 2.x many vectors are expanded per *extruder variant* (e.g. "Direct
Drive Standard" / "Direct Drive High Flow" on a P2S): a process speed may carry two
entries, one per variant, and per-filament keys may carry ``filaments x variants``
entries with ``filament_self_index`` telling which filament each entry belongs
to. ``BambuConfig`` collapses all of that back to one value per filament /
extruder, picking the variant the project is actually set up to print with.
"""

from __future__ import annotations

import json
from typing import Optional


def _as_list(v) -> list:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v]
    return [str(v)]


# Bambu bed type -> (bed temp key, first layer bed temp key)
BED_TEMP_KEYS = {
    "Cool Plate": ("cool_plate_temp", "cool_plate_temp_initial_layer"),
    "Engineering Plate": ("eng_plate_temp", "eng_plate_temp_initial_layer"),
    "High Temp Plate": ("hot_plate_temp", "hot_plate_temp_initial_layer"),
    "Textured PEI Plate": ("textured_plate_temp", "textured_plate_temp_initial_layer"),
    "Supertack Plate": ("supertack_plate_temp", "supertack_plate_temp_initial_layer"),
    "Cool Plate (SuperTack)": ("supertack_plate_temp", "supertack_plate_temp_initial_layer"),
}


class BambuConfig:
    def __init__(self, raw: dict):
        self.raw: dict = raw if isinstance(raw, dict) else {}
        colours = self.raw.get("filament_colour")
        ids = self.raw.get("filament_settings_id")
        base = colours if isinstance(colours, list) and colours else ids
        self.num_filaments: int = len(base) if isinstance(base, list) and base else 1

        nd = self.raw.get("nozzle_diameter")
        self.extruder_count: int = len(nd) if isinstance(nd, list) and nd else 1

        ext_types = _as_list(self.raw.get("extruder_type"))
        vol_types = _as_list(self.raw.get("nozzle_volume_type"))
        self.active_variants: list = []
        for i in range(self.extruder_count):
            et = ext_types[i] if i < len(ext_types) else (ext_types[-1] if ext_types else "")
            vt = vol_types[i] if i < len(vol_types) else (vol_types[-1] if vol_types else "")
            self.active_variants.append(f"{et} {vt}".strip())

        self.print_variants = _as_list(self.raw.get("print_extruder_variant"))
        self.printer_variants = _as_list(self.raw.get("printer_extruder_variant"))
        self.filament_variants = _as_list(self.raw.get("filament_extruder_variant"))
        self.filament_self_index = [
            int(x) for x in _as_list(self.raw.get("filament_self_index")) if x.strip().isdigit()
        ]
        self.filament_map = [
            int(x) for x in _as_list(self.raw.get("filament_map")) if x.strip().lstrip("-").isdigit()
        ]

    @classmethod
    def from_bytes(cls, data: bytes) -> "BambuConfig":
        try:
            return cls(json.loads(data.decode("utf-8", "replace")))
        except ValueError:
            return cls({})

    @classmethod
    def empty(cls) -> "BambuConfig":
        return cls({})

    def __bool__(self) -> bool:
        return bool(self.raw)

    def has(self, key: str) -> bool:
        return key in self.raw

    def raw_value(self, key: str):
        return self.raw.get(key)

    # -- variant helpers ----------------------------------------------------------
    def _variant_index(self, n_values: int, variants: list, extruder: int) -> Optional[int]:
        if len(variants) > 1 and n_values == len(variants):
            want = self.active_variants[min(extruder, len(self.active_variants) - 1)]
            if want in variants:
                return variants.index(want)
        return None

    # -- accessors ------------------------------------------------------------------
    def scalar(self, key: str, extruder: int = 0) -> Optional[str]:
        """One value for a process / machine key (variant-expanded vectors collapsed)."""
        v = self.raw.get(key)
        if v is None:
            return None
        if isinstance(v, list):
            if not v:
                return None
            idx = self._variant_index(len(v), self.print_variants, extruder)
            if idx is None:
                idx = self._variant_index(len(v), self.printer_variants, extruder)
            if idx is None and len(v) > extruder and len(v) == self.extruder_count:
                idx = extruder
            return str(v[idx if idx is not None else 0])
        return str(v)

    def per_filament(self, key: str) -> Optional[list]:
        """Exactly ``num_filaments`` values for a filament key, or None if absent."""
        v = self.raw.get(key)
        if v is None:
            return None
        n_f = self.num_filaments
        if not isinstance(v, list):
            return [str(v)] * n_f
        n = len(v)
        if n == 0:
            return None
        if n == n_f:
            return [str(x) for x in v]
        if self.filament_self_index and n == len(self.filament_self_index):
            out = []
            for f in range(1, n_f + 1):
                idxs = [j for j, si in enumerate(self.filament_self_index) if si == f]
                if not idxs:
                    out.append(str(v[0]))
                    continue
                ext = (self.filament_map[f - 1] - 1) if f - 1 < len(self.filament_map) else 0
                want = self.active_variants[min(max(ext, 0), len(self.active_variants) - 1)]
                pick = next(
                    (j for j in idxs if j < len(self.filament_variants) and self.filament_variants[j] == want),
                    idxs[0],
                )
                out.append(str(v[pick]))
            return out
        if n > n_f and n % n_f == 0:
            k = n // n_f
            return [str(v[i * k]) for i in range(n_f)]
        if n == 1:
            return [str(v[0])] * n_f
        return [str(v[i]) if i < n else str(v[-1]) for i in range(n_f)]

    def per_extruder(self, key: str) -> Optional[list]:
        """Exactly ``extruder_count`` values for a per-extruder machine key."""
        v = self.raw.get(key)
        if v is None:
            return None
        n_e = self.extruder_count
        if not isinstance(v, list):
            return [str(v)] * n_e
        if not v:
            return None
        n = len(v)
        if n == n_e:
            return [str(x) for x in v]
        if len(self.printer_variants) > 1 and n == len(self.printer_variants):
            out = []
            for e in range(n_e):
                idx = self._variant_index(n, self.printer_variants, e)
                out.append(str(v[idx if idx is not None else 0]))
            return out
        return [str(v[i]) if i < n else str(v[-1]) for i in range(n_e)]

    def machine_limit(self, key: str) -> Optional[tuple]:
        """(normal, silent) pair for a machine_max_* key."""
        v = _as_list(self.raw.get(key))
        if not v:
            return None
        nv = len(self.printer_variants)
        if nv > 1 and len(v) == 2 * nv:
            idx = self._variant_index(nv, self.printer_variants, 0) or 0
            return (v[2 * idx], v[2 * idx + 1])
        if len(v) >= 2:
            return (v[0], v[1])
        return (v[0], v[0])

    # -- convenience ----------------------------------------------------------------
    @property
    def bed_type(self) -> str:
        return self.scalar("curr_bed_type") or ""

    def bed_temp_keys(self) -> tuple:
        return BED_TEMP_KEYS.get(self.bed_type, ("hot_plate_temp", "hot_plate_temp_initial_layer"))

    @property
    def filament_colours(self) -> list:
        return self.per_filament("filament_colour") or []

    @property
    def filament_types(self) -> list:
        return self.per_filament("filament_type") or []

    @property
    def filament_ids(self) -> list:
        return self.per_filament("filament_settings_id") or []

    @property
    def single_extruder_multi_material(self) -> bool:
        return (self.scalar("single_extruder_multi_material") or "0") not in ("0", "false", "")
