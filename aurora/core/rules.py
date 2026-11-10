"""Batch settings (no Qt): rules that patch many zone x scan settings at once, and copying one card to many.

A Rule is (scope, scan_filter, patch, enabled):
- scope: {'kind': 'project'} | {'kind': 'holders', 'holders': [key, ...]} | {'kind': 'groups', 'groups': [...]} |
  {'kind': 'zones', 'zones': [[holder key, zone id], ...]}
- scan_filter: any of {'edge': 'F K', 'plan': <regex on the scan folder name>, 'span': [lo, hi] (eV, the scan's
  energies lie inside), 'flags': ['sdd_saturated', 'i0_invalid', 'high_rate']}; an empty filter matches every scan
- patch: any subset of the session-file keys inc, sdd, line, fn, tey, tn, ds, k, i0src, pre, post, roi, sig (or the
  ScanSettings attribute names, or 'signal'); pre and post may be written relative to the edge energy E0
  ('E0-15..E0-5'), resolved per scan from xraydb
Fields listed in ScanSettings.pinned (set by hand on a card) are left alone unless override=True.

Rules are stored as JSON in ZonesFile.rules (session.json 'rules' block). dry_run lists what apply would change, as
(holder, zone, sid, field, old, new) rows.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Callable, NamedTuple

from aurora.core.constants import DETS

# session-file key -> ScanSettings attribute
FIELDS: dict[str, str] = {"inc": "include", "sdd": "sdd", "line": "line", "fn": "fluo_norm", "tey": "tey",
                          "tn": "tey_norm", "ds": "despike", "k": "k", "i0src": "i0_source", "pre": "pre",
                          "post": "post", "roi": "roi", "sig": "signal", "ipm": "ipfy_mode", "dsm": "despike_mode"}
ATTRS: dict[str, str] = {v: k for k, v in FIELDS.items()}
PATCH_KEYS = ("inc", "sdd", "line", "fn", "tey", "tn", "ds", "k", "i0src", "pre", "post", "roi", "sig")
NEVER_COPIED = ("inc", "n")
NORM_CODES = ("raw", "i0", "i0pre", "i0jump", "area", "max")

# words for the fields, in the vocabulary of the scan card and the manual
FIELD_LABELS: dict[str, str] = {
    "inc": "include", "sdd": "detectors", "line": "line", "fn": "fluorescence normalisation", "tey": "TEY",
    "tn": "TEY normalisation", "ds": "de-spike", "k": "de-spike threshold k", "i0src": "I0 source",
    "pre": "pre-edge window", "post": "post-edge window", "roi": "emission windows", "sig": "signal",
    "ipm": "IPFY definition", "dsm": "de-spike mode",
}

# what 'Copy to…' carries: (group, words, keys, ticked by default). include and n never travel.
COPY_GROUPS: tuple[tuple[str, str, tuple[str, ...], bool], ...] = (
    ("sdd", "detectors", ("sdd",), True),
    ("line", "line and emission windows", ("line", "roi"), True),
    ("fn", "fluorescence normalisation", ("fn",), True),
    ("tn", "TEY and its normalisation", ("tey", "tn"), True),
    ("ds", "de-spike and k", ("ds", "k"), True),
    ("i0src", "I0 source", ("i0src",), True),
    ("prepost", "pre / post windows", ("pre", "post"), True),
    ("sig", "signal", ("sig",), False),
)
DEFAULT_COPY = tuple(k for _g, _w, keys, on in COPY_GROUPS if on for k in keys)

COPY_TARGETS = {
    "zone": "all scans of this zone",
    "scan": "this scan in every zone of the holder",
    "edge": "every scan with the same edge in every zone of the holder",
}

_E0 = re.compile(r"^\s*E0\s*([+-]\s*\d+(?:\.\d*)?)?\s*$", re.I)


class Change(NamedTuple):
    holder: str
    zone: int
    sid: str
    field: str
    old: object
    new: object


def key_of(name: str) -> str:
    """The session-file key of a field given as a key, an attribute name or 'signal'."""
    n = str(name).strip()
    if n in FIELDS:
        return n
    if n in ATTRS:
        return ATTRS[n]
    if n.lower() == "signal":
        return "sig"
    raise KeyError(f"unknown settings field {name!r}")


def get_field(st, key: str):
    return copy.deepcopy(getattr(st, FIELDS[key_of(key)]))


def set_field(st, key: str, value) -> None:
    setattr(st, FIELDS[key_of(key)], copy.deepcopy(value))


def _same(a, b) -> bool:
    if isinstance(a, (list, tuple)) or isinstance(b, (list, tuple)):
        a = list(a) if a is not None else None
        b = list(b) if b is not None else None
    return a == b


# ----------------------------------------------------------------------------- values
def parse_range(value, e0: float | None = None):
    """A pre / post window: None / '' / 'auto' -> None (automatic); [a, b] or 'a-b' -> [a, b] eV; 'E0-15..E0-5'
    -> [E0 - 15, E0 - 5] with E0 the edge energy (ValueError without one)."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        a, b = float(value[0]), float(value[1])
        return [min(a, b), max(a, b)]
    t = str(value).strip()
    if not t or t.lower() == "auto":
        return None
    if "E0" in t.upper():
        parts = re.split(r"\s*(?:\.\.|:|\bto\b)\s*", t)
        if len(parts) != 2:
            raise ValueError(f"{value!r}: write the window as 'E0-15..E0-5'")
        if e0 is None:
            raise ValueError(f"{value!r}: the scan has no edge energy")
        out = []
        for p in parts:
            m = _E0.match(p)
            if not m:
                raise ValueError(f"{value!r}: write the window as 'E0-15..E0-5'")
            out.append(e0 + float((m.group(1) or "0").replace(" ", "")))
        return [min(out), max(out)]
    m = re.match(r"^\s*(-?\d+(?:\.\d*)?)\s*(?:-|\.\.|:|to|,)\s*(-?\d+(?:\.\d*)?)\s*$", t)
    if not m:
        raise ValueError(f"{value!r}: write the window as '680-684' (eV) or 'E0-15..E0-5'")
    a, b = float(m.group(1)), float(m.group(2))
    return [min(a, b), max(a, b)]


def is_relative(value) -> bool:
    return isinstance(value, str) and "E0" in value.upper()


def check_value(key: str, value, e0: float | None = None):
    """The value a patch writes for one field (validated, ranges resolved). ValueError when it cannot be used."""
    k = key_of(key)
    if k in ("inc", "tey", "ds"):
        return bool(value)
    if k == "sdd":
        v = [bool(x) for x in value]
        if len(v) != len(DETS):
            raise ValueError(f"sdd needs {len(DETS)} values, not {len(v)}")
        return v
    if k in ("fn", "tn"):
        if value not in NORM_CODES:
            raise ValueError(f"{value!r} is not a normalisation ({', '.join(NORM_CODES)})")
        return str(value)
    if k == "k":
        v = float(value)
        if not v > 0:
            raise ValueError("k must be above 0")
        return v
    if k == "i0src":
        if value not in ("own", "good"):
            raise ValueError("I0 source is 'own' or 'good'")
        return str(value)
    if k in ("pre", "post"):
        return parse_range(value, e0)
    if k == "roi":
        if value is None:
            return None
        from aurora.core.xrf_windows import normalise_windows
        return normalise_windows(value)
    if k == "sig":
        if value not in ("pfy", "ipfy"):
            raise ValueError("signal is 'pfy' or 'ipfy'")
        return str(value)
    return value if k not in ("line", "ipm", "dsm") else str(value)


def edge_ev(scan) -> float | None:
    edge = getattr(scan, "edge", None)
    if not edge:
        return None
    from aurora.core.lines import edge_energy
    return edge_energy(*edge)


def _edge_token(text: str) -> str:
    return re.sub(r"[\s_]+", " ", str(text or "")).strip().lower()


# ----------------------------------------------------------------------------- rules
@dataclass
class Rule:
    scope: dict = field(default_factory=lambda: {"kind": "project"})
    scan_filter: dict = field(default_factory=dict)
    patch: dict = field(default_factory=dict)
    enabled: bool = True
    name: str = ""
    extra: dict = field(default_factory=dict)       # keys this version does not know, written back unchanged

    KEYS = ("name", "enabled", "scope", "filter", "patch")

    # ------------------------------------------------------------------ JSON (ZonesFile.rules)
    def to_json(self) -> dict:
        out = {"name": self.name, "enabled": self.enabled, "scope": copy.deepcopy(self.scope),
               "filter": copy.deepcopy(self.scan_filter), "patch": copy.deepcopy(self.patch)}
        for k, v in self.extra.items():
            out.setdefault(k, copy.deepcopy(v))
        return out

    @classmethod
    def from_json(cls, d: dict) -> "Rule":
        patch = {}
        for k, v in dict(d.get("patch", {})).items():
            try:
                patch[key_of(k)] = v
            except KeyError:
                patch[str(k)] = v                     # an unknown field is kept as written (ignored when applied)
        return cls(scope=dict(d.get("scope") or {"kind": "project"}), scan_filter=dict(d.get("filter") or {}),
                   patch=patch, enabled=bool(d.get("enabled", True)), name=str(d.get("name", "")),
                   extra={k: copy.deepcopy(v) for k, v in d.items() if k not in cls.KEYS})

    # ------------------------------------------------------------------ matching
    def in_scope(self, holder: str, zone) -> bool:
        kind = self.scope.get("kind", "project")
        if kind == "project":
            return True
        if kind == "holders":
            return holder in set(self.scope.get("holders", []))
        if kind == "groups":
            return (zone.group or "").strip().lower() in {g.strip().lower() for g in self.scope.get("groups", [])}
        if kind == "zones":
            return [holder, int(zone.id)] in [[str(h), int(z)] for h, z in self.scope.get("zones", [])]
        return False

    def matches(self, scan, flags_of: Callable | None = None) -> bool:
        if scan is None:
            return False
        f = self.scan_filter
        if f.get("edge"):
            from aurora.core.names import pretty_edge
            if _edge_token(pretty_edge(getattr(scan, "edge", None))) != _edge_token(f["edge"]):
                return False
        if f.get("plan"):
            names = [getattr(scan, "name", "") or ""] + [str((getattr(scan, "md", None) or {}).get(k, ""))
                                                        for k in ("plan_name", "scan_name")]
            try:
                if not any(re.search(f["plan"], n) for n in names if n):
                    return False
            except re.error:
                return False
        if f.get("span"):
            lo, hi = float(f["span"][0]), float(f["span"][1])
            e0, e1 = float(getattr(scan, "e_min", float("nan"))), float(getattr(scan, "e_max", float("nan")))
            if not (e0 == e0 and e1 == e1 and e0 >= lo - 1e-6 and e1 <= hi + 1e-6):
                return False
        if f.get("flags"):
            if flags_of is None:
                from aurora.core.flags import merged_flags
                flags = merged_flags(None, scan)
            else:
                flags = flags_of(scan)
            kinds = {fl.kind for fl in flags or []}
            if not set(f["flags"]) <= kinds:
                return False
        return True

    def values_for(self, scan) -> dict:
        """The patch for one scan: keys -> checked values (relative pre / post resolved; unusable entries dropped)."""
        e0 = edge_ev(scan) if any(is_relative(v) for v in self.patch.values()) else None
        out = {}
        for k, v in self.patch.items():
            if k not in FIELDS:
                continue
            try:
                out[k] = check_value(k, v, e0)
            except (ValueError, TypeError, KeyError):
                continue
        return out

    # ------------------------------------------------------------------ dry run and apply
    def dry_run(self, zones_file, scans, override: bool = False, flags_of: Callable | None = None) -> list[Change]:
        return dry_run([self], zones_file, scans, override, flags_of)

    def apply(self, zones_file, scans, override: bool = False, flags_of: Callable | None = None) -> list[Change]:
        return apply([self], zones_file, scans, override, flags_of)


def rules_from_json(items) -> list[Rule]:
    return [Rule.from_json(d) for d in items or [] if isinstance(d, dict)]


def rules_to_json(rules) -> list[dict]:
    return [r.to_json() for r in rules]


def _scan_map(scans) -> dict:
    """sid -> ScanInfo from a mapping, a list of ScanInfo or a Project."""
    if scans is None:
        return {}
    if hasattr(scans, "scans") and not isinstance(scans, dict):
        scans = scans.scans
    if isinstance(scans, dict):
        return dict(scans)
    return {f"{s.day}_{s.name}": s for s in scans}


def _plan(rules, zones_file, scans, override, flags_of):
    """[(holder, zone, sid, st, {key: new})] for every card a rule touches, rules applied in order (later win)."""
    smap = _scan_map(scans)
    out = []
    for hk, hz in zones_file.holders.items():
        for z in hz.zones:
            for sid, st in z.settings.items():
                scan = smap.get(sid)
                new: dict = {}
                for r in rules:
                    if not r.enabled or not r.in_scope(hk, z) or not r.matches(scan, flags_of):
                        continue
                    for k, v in r.values_for(scan).items():
                        if k in (st.pinned or []) and not override:
                            continue
                        new[k] = v
                if new:
                    out.append((hk, z, sid, st, new))
    return out


def dry_run(rules, zones_file, scans, override: bool = False, flags_of: Callable | None = None) -> list[Change]:
    """What apply would change: (holder, zone, sid, field, old, new) for every field whose value would change."""
    rows = []
    for hk, z, sid, st, new in _plan(list(rules), zones_file, scans, override, flags_of):
        for k, v in new.items():
            old = get_field(st, k)
            if not _same(old, v):
                rows.append(Change(hk, int(z.id), sid, k, old, v))
    return rows


def apply(rules, zones_file, scans, override: bool = False, flags_of: Callable | None = None) -> list[Change]:
    """Write the rules into the settings (in order; pinned fields kept unless override). Returns the changes made.
    With override, a field written is no longer pinned."""
    done = []
    for hk, z, sid, st, new in _plan(list(rules), zones_file, scans, override, flags_of):
        for k, v in new.items():
            old = get_field(st, k)
            if _same(old, v):
                continue
            set_field(st, k, v)
            if override and k in (st.pinned or []):
                st.pinned = [p for p in st.pinned if p != k]
            done.append(Change(hk, int(z.id), sid, k, old, v))
    return done


def apply_to_new(rules, holder: str | None, zone, scan, st, flags_of: Callable | None = None) -> list[str]:
    """Saved rules on one new zone x scan settings (the settings-default hook). `zone` may be None when unknown: rules
    scoped to groups or zones are then skipped. Returns the keys written."""
    written = []
    for r in rules:
        if not r.enabled or not r.matches(scan, flags_of):
            continue
        kind = r.scope.get("kind", "project")
        if kind != "project":
            if kind == "holders" and holder is None:
                continue
            if kind in ("groups", "zones") and (zone is None or holder is None):
                continue
            if not r.in_scope(holder, zone):
                continue
        for k, v in r.values_for(scan).items():
            if k in (st.pinned or []):
                continue
            set_field(st, k, v)
            written.append(k)
    return written


# ----------------------------------------------------------------------------- copy one card to many
def copy_targets(zones_file, holder: str, zone_id: int, sid: str, target: str, scans=None) -> list[tuple]:
    """The cards a copy reaches, without the source: [(holder, zone, sid, settings)].
    target 'zone': every other scan of this zone; 'scan': this scan in every other zone of the holder; 'edge': every
    scan with the same edge as this one, in every zone of the holder."""
    hz = zones_file.holders.get(holder)
    if hz is None:
        return []
    out = []
    if target == "zone":
        z = next((zz for zz in hz.zones if zz.id == zone_id), None)
        if z is not None:
            out = [(holder, z, s, st) for s, st in z.settings.items() if s != sid]
    elif target == "scan":
        out = [(holder, z, sid, z.settings[sid]) for z in hz.zones if z.id != zone_id and sid in z.settings]
    elif target == "edge":
        smap = _scan_map(scans)
        src = smap.get(sid)
        edge = getattr(src, "edge", None) if src is not None else None
        for z in hz.zones:
            for s, st in z.settings.items():
                if z.id == zone_id and s == sid:
                    continue
                other = smap.get(s)
                if edge is not None and other is not None and getattr(other, "edge", None) == edge:
                    out.append((holder, z, s, st))
    else:
        raise ValueError(f"unknown copy target {target!r} (one of {', '.join(COPY_TARGETS)})")
    return out


def copy_settings(source, targets, fields=DEFAULT_COPY, pin: bool = True) -> list[Change]:
    """Copy the given fields of `source` (ScanSettings) into every target settings ([(holder, zone, sid, st)] or a
    list of ScanSettings). include and n never travel. The copied fields count as set by hand (pinned)."""
    keys = [key_of(f) for f in fields if key_of(f) not in NEVER_COPIED]
    done = []
    for t in targets:
        hk, z, sid, st = t if isinstance(t, tuple) else ("", None, "", t)
        for k in keys:
            new = get_field(source, k)
            old = get_field(st, k)
            if _same(old, new):
                continue
            set_field(st, k, new)
            if pin and k not in st.pinned:
                st.pinned = list(st.pinned) + [k]
            done.append(Change(hk, int(z.id) if z is not None else 0, sid, k, old, new))
    return done


def describe_value(key: str, value) -> str:
    """A field value in words: '/ I0, edge jump = 1', 'on', 'SDD3 off', '680-684 eV', 'auto', '60:74 ch'."""
    k = key_of(key)
    if k in ("fn", "tn"):
        labels = {"raw": "raw counts", "i0": "/ I0", "i0pre": "/ I0, pre-edge = 0", "i0jump": "/ I0, edge jump = 1",
                  "area": "/ I0, area = 1", "max": "/ I0, maximum = 1"}
        return labels.get(value, str(value))
    if k in ("inc", "tey", "ds"):
        return "on" if value else "off"
    if k == "sdd":
        off = [f"SDD{i + 1}" for i, on in enumerate(value or []) if not on]
        return "all on" if not off else ", ".join(off) + " off"
    if k in ("pre", "post"):
        return "auto" if not value else f"{value[0]:g}-{value[1]:g} eV"
    if k == "roi":
        if not value:
            return "the line's fixed bins"
        from aurora.core.xrf_windows import describe
        return describe(value)
    if k == "i0src":
        return "good-I0 mean" if value == "good" else "own"
    if k == "sig":
        return {"pfy": "PFY", "ipfy": "IPFY"}.get(value, str(value))
    if k == "k":
        return f"{float(value):g} MAD"
    return str(value)


def summary(changes) -> str:
    """'settings copied to 23 cards in 11 zones' style counts: (cards, zones)."""
    cards = {(c.holder, c.zone, c.sid) for c in changes}
    zones = {(c.holder, c.zone) for c in changes}
    return f"{len(cards)} card{'s' if len(cards) != 1 else ''} in {len(zones)} zone{'s' if len(zones) != 1 else ''}"
