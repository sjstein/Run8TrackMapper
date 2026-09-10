"""Precompute a per-section track grade (%) for the grade heat-map viewer (#57).

Grade is rise/run along the track: ``(elevation_end_m - elevation_start_m) / length_m``.
The section elevations come straight from the track-DB node ``position.y`` (real altitude
in metres), extracted per section by ``region_extractor``. Validated against ground truth:
on BNSF Cajon this reproduces the known 2.2% (and old-South-track ~3.0%) ruling grades.

**Hybrid smoothing.** For all but the shortest sections the raw per-section grade is already
accurate (windowing changes the median section by ~0.01%). The exception is *very short*
sections (yard/switch stubs a few metres long) where the 1 cm elevation rounding turns into a
spurious 4-5% reading. So sections at/above ``smooth_below_m`` keep their exact grade, and only
shorter ones are replaced with a **windowed** grade measured over ~``window_m`` of connected
track (following the straightest continuation through switches so it stays on the through route,
not a diverging leg). This cleans the switch-area speckle without over-smoothing real grades.

The computation is done once at output-generation time (never live) and the result is emitted
as ``grade_pct`` on each section in the region JSON.

Public entry point: ``compute_section_grades(sections, ...)`` — mutates each section, setting
``section.grade_pct``. It only needs the duck-typed attributes ``paths``, ``length_m``,
``elevation_start_m``, ``elevation_end_m`` (so it works on ``SectionData`` and on plain
dict-backed objects alike).
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import List, Tuple

# A section's grade is measured along its horizontal run; sections shorter than this keep
# their raw grade only if they are long enough that cm-quantization is negligible.
DEFAULT_SMOOTH_BELOW_M = 20.0   # sections shorter than this get windowed smoothing
DEFAULT_WINDOW_M = 80.0         # total smoothing window (metres); half is walked off each end
DEFAULT_MAX_TURN_DEG = 60.0     # max heading change to accept a continuation through a junction

Point = Tuple[float, float]


def _bearing(p: Point, q: Point) -> float:
    return math.atan2(q[1] - p[1], q[0] - p[0])


def _ang_diff(a: float, b: float) -> float:
    d = abs(a - b) % (2 * math.pi)
    return min(d, 2 * math.pi - d)


def _representative_path(section) -> List[Point]:
    """The most detailed polyline for a section (a section may carry coarse + mirror +
    detailed paths); grade only needs one continuous route."""
    paths = getattr(section, 'paths', None) or []
    best = None
    for p in paths:
        if p and len(p) >= 2 and (best is None or len(p) > len(best)):
            best = p
    return best


class _Chain:
    """Section-endpoint graph used to walk connected track for windowed smoothing.

    Each section contributes two endpoints (A = path start, B = path end), tagged with the
    section's start/end elevations. Endpoints within 1 m are treated as the same node, which
    on the real data joins virtually every section (joints are coincident to <0.001 m).
    """

    __slots__ = ('secs', 'A', 'B', 'eA', 'eB', 'L', 'poly', 'nodes')

    def __init__(self, sections):
        self.secs = []
        self.A: List[Point] = []
        self.B: List[Point] = []
        self.eA: List[float] = []
        self.eB: List[float] = []
        self.L: List[float] = []
        self.poly: List[List[Point]] = []
        self.nodes = defaultdict(list)   # (rx, rz) -> list of (idx, end) with end in ('A','B')
        for s in sections:
            poly = _representative_path(s)
            L = getattr(s, 'length_m', 0.0) or 0.0
            if not poly or L < 1.0:
                self.secs.append(None)     # keep index alignment with `sections`
                self.A.append(None); self.B.append(None)
                self.eA.append(0.0); self.eB.append(0.0); self.L.append(L)
                self.poly.append(None)
                continue
            i = len(self.secs)
            a = (float(poly[0][0]), float(poly[0][1]))
            b = (float(poly[-1][0]), float(poly[-1][1]))
            self.secs.append(s)
            self.A.append(a); self.B.append(b)
            self.eA.append(float(getattr(s, 'elevation_start_m', 0.0) or 0.0))
            self.eB.append(float(getattr(s, 'elevation_end_m', 0.0) or 0.0))
            self.L.append(L)
            self.poly.append(poly)
            self.nodes[self._key(a)].append((i, 'A'))
            self.nodes[self._key(b)].append((i, 'B'))

    @staticmethod
    def _key(pt: Point) -> Tuple[int, int]:
        return (round(pt[0]), round(pt[1]))

    def _endpt(self, i, end):
        return self.A[i] if end == 'A' else self.B[i]

    def _endelev(self, i, end):
        return self.eA[i] if end == 'A' else self.eB[i]

    def _out_bearing(self, i, end):
        """Direction pointing INTO section i, entering at `end`."""
        poly = self.poly[i]
        return _bearing(poly[0], poly[1]) if end == 'A' else _bearing(poly[-1], poly[-2])

    def _walk_out(self, seg, exit_end, target, max_turn):
        """Walk out of `seg` at `exit_end` along the straightest continuation until `target`
        metres are covered (or a dead end / sharp junction stops it). Return the elevation at
        that reach (interpolated within the crossing section) and the distance actually
        covered."""
        other = 'B' if exit_end == 'A' else 'A'
        trav = _bearing(self._endpt(seg, other), self._endpt(seg, exit_end))
        node = self._key(self._endpt(seg, exit_end))
        elev = self._endelev(seg, exit_end)
        dist = 0.0
        visited = {seg}
        while dist < target:
            cands = [(j, e) for (j, e) in self.nodes.get(node, []) if j not in visited]
            if not cands:
                break
            best = None
            best_turn = 9.0
            for (j, e) in cands:
                dt = _ang_diff(trav, self._out_bearing(j, e))
                if dt < best_turn:
                    best_turn = dt
                    best = (j, e)
            if best is None or best_turn > max_turn:
                break
            j, e = best
            oe = 'B' if e == 'A' else 'A'
            e0, e1 = self._endelev(j, e), self._endelev(j, oe)   # entry/exit elevation of j
            Lj = self.L[j]
            if dist + Lj >= target:                              # stop exactly at target
                frac = (target - dist) / Lj if Lj > 0 else 0.0
                return e0 + frac * (e1 - e0), target
            dist += Lj
            node = self._key(self._endpt(j, oe))
            elev = e1
            trav = _bearing(self._endpt(j, e), self._endpt(j, oe))
            visited.add(j)
        return elev, dist

    def windowed_grade(self, i, whalf, max_turn):
        ef, df = self._walk_out(i, 'B', whalf, max_turn)
        eb, db = self._walk_out(i, 'A', whalf, max_turn)
        span = db + self.L[i] + df
        return (ef - eb) / span * 100.0 if span > 0 else 0.0


def compute_section_grades(sections,
                           smooth_below_m: float = DEFAULT_SMOOTH_BELOW_M,
                           window_m: float = DEFAULT_WINDOW_M,
                           max_turn_deg: float = DEFAULT_MAX_TURN_DEG) -> None:
    """Set ``grade_pct`` on every section in `sections` (a per-region list).

    Long sections (>= ``smooth_below_m``) get the exact raw grade; shorter ones get the
    windowed grade over ~``window_m`` of connected track. No-op-safe: a section with no usable
    path or zero length gets ``grade_pct = 0.0``.
    """
    chain = _Chain(sections)
    whalf = max(1.0, window_m / 2.0)
    max_turn = math.radians(max_turn_deg)
    for i, s in enumerate(sections):
        L = chain.L[i]
        if chain.secs[i] is None or L < 1.0:
            grade = 0.0
        elif L >= smooth_below_m:
            grade = (chain.eB[i] - chain.eA[i]) / L * 100.0     # exact per-section grade
        else:
            grade = chain.windowed_grade(i, whalf, max_turn)    # borrow neighbour context
        s.grade_pct = round(grade, 3)
