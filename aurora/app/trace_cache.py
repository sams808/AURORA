"""One cache of zone traces for every view that draws them (Reduce's preview, Check's four detectors, the Survey's
zone preview): after a zone edit each (zone pixels, stack, settings) is computed once, whichever view asks first.

zone_traces_cached(sd, pix, settings, good) is aurora.core.zones.zone_traces with an LRU of CACHE_SIZE entries. The key
holds everything the traces depend on: the stack object (a reload is a new object, a weak reference checks that the
cached one is still the same), its id, the pixels (a digest of their bytes), the settings (their JSON), the good-I0
curve (a digest) and whether the stack's XRF window counts are ready (zone_sums reads them, or falls back to the fixed
bins with a note). The cached ZoneTraces are shared: callers read them and never change them.
"""
from __future__ import annotations

import hashlib
import json
import weakref
from collections import OrderedDict

import numpy as np

from aurora.core.io.config import json_default

CACHE_SIZE = 64

_cache: OrderedDict = OrderedDict()            # key -> (weakref to the stack, ZoneTraces | None)
stats = {"hits": 0, "misses": 0}               # read by the tests (one computation per zone x stack)


def _digest(a) -> str:
    if a is None:
        return ""
    a = np.ascontiguousarray(a)
    return f"{a.dtype.str}{a.shape}" + hashlib.blake2b(a.tobytes(), digest_size=16).hexdigest()


def _windows_ready(sd, settings) -> bool:
    roi = getattr(settings, "roi", None)
    if not roi:
        return True
    from aurora.core import xrf_windows as xw
    try:
        return bool(xw.stack_window_ready(sd, xw.normalise_windows(roi)))
    except (OSError, ValueError, TypeError):
        return True


def key_of(sd, pix, settings, good=None) -> tuple:
    pix = np.asarray(pix, int)
    st = json.dumps(settings.to_json(), sort_keys=True, default=json_default) if settings is not None else ""
    return (id(sd), str(getattr(sd, "id", "")), _digest(pix), st, _digest(None if good is None else np.asarray(good, float)),
            _windows_ready(sd, settings))


def zone_traces_cached(sd, pix, settings, good=None):
    """aurora.core.zones.zone_traces(sd, pix, settings, good), computed once per key (see the module docstring)."""
    from aurora.core.zones import zone_traces
    key = key_of(sd, pix, settings, good)
    hit = _cache.get(key)
    if hit is not None and hit[0]() is sd:
        _cache.move_to_end(key)
        stats["hits"] += 1
        return hit[1]
    stats["misses"] += 1
    t = zone_traces(sd, pix, settings, good)
    try:
        ref = weakref.ref(sd)
    except TypeError:                           # an object without weak references: never cached
        return t
    _cache[key] = (ref, t)
    _cache.move_to_end(key)
    while len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)
    return t


def clear() -> None:
    """Forget every cached trace (a project switch)."""
    _cache.clear()
