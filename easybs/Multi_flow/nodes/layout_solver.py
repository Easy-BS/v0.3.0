# -*- coding: utf-8 -*-
"""
Created on Sun Sep  6 15:50:50 2026

@author: Xiguan Liang @SKKU
"""

import sys
from typing import Any, Dict, List, Optional, Tuple

from state_schema import SimulationState


# --------------------------------------------------------------------------
# Units. Work in integer millimetres internally, metres at the boundary.
# --------------------------------------------------------------------------

def _mm(value: Any) -> Optional[int]:
    try:
        return int(round(float(value) * 1000.0))
    except (TypeError, ValueError):
        return None


def _m(value_mm: int) -> float:
    return round(value_mm / 1000.0, 4)


# Cardinal handling. Directions are BUILDING-relative; the North Axis rotation
# is applied later at export, exactly as in the current pipeline.
_CARDINALS = ("N", "E", "S", "W")

_CORNERS = {
    "SW": ("W", "S"), "SE": ("E", "S"),
    "NW": ("W", "N"), "NE": ("E", "N"),
}


def _norm_dir(value: Any) -> Optional[str]:
    s = str(value or "").strip().upper()
    alias = {
        "NORTH": "N", "EAST": "E", "SOUTH": "S", "WEST": "W",
        "N": "N", "E": "E", "S": "S", "W": "W",
    }
    return alias.get(s)


def _norm_corner(value: Any) -> Optional[str]:
    s = str(value or "").strip().upper().replace("-", "").replace(" ", "")
    alias = {
        "SW": "SW", "SOUTHWEST": "SW",
        "SE": "SE", "SOUTHEAST": "SE",
        "NW": "NW", "NORTHWEST": "NW",
        "NE": "NE", "NORTHEAST": "NE",
    }
    return alias.get(s)


# --------------------------------------------------------------------------
# Diagnostics
# --------------------------------------------------------------------------

def _diag(code: str, message: str, **extra) -> Dict[str, Any]:
    d = {"code": code, "message": message}
    d.update(extra)
    return d


# --------------------------------------------------------------------------
# Box helper. A zone is [x0, y0, x1, y1] in mm.
# --------------------------------------------------------------------------

def _polygon(box: List[int]) -> List[List[float]]:
    """Emit vertices in the same order used by the published cases:
    (x0,y0) -> (x0,y1) -> (x1,y1) -> (x1,y0). force_cw() downstream will
    normalise winding regardless."""
    x0, y0, x1, y1 = box
    return [
        [_m(x0), _m(y0)],
        [_m(x0), _m(y1)],
        [_m(x1), _m(y1)],
        [_m(x1), _m(y0)],
    ]


def _overlap_area(a: List[int], b: List[int]) -> int:
    dx = min(a[2], b[2]) - max(a[0], b[0])
    dy = min(a[3], b[3]) - max(a[1], b[1])
    return dx * dy if dx > 0 and dy > 0 else 0


def _touches(a: List[int], b: List[int]) -> bool:
    """Share a wall segment of non-zero length."""
    if a[2] == b[0] or b[2] == a[0]:
        return min(a[3], b[3]) - max(a[1], b[1]) > 0
    if a[3] == b[1] or b[3] == a[1]:
        return min(a[2], b[2]) - max(a[0], b[0]) > 0
    return False


# --------------------------------------------------------------------------
# Core solver
# --------------------------------------------------------------------------

class LayoutSolver:

    def __init__(self, layout: Dict[str, Any]):
        self.raw = layout or {}
        self.diagnostics: List[Dict[str, Any]] = []

        fp = self.raw.get("footprint") or {}
        self.fp_w = _mm(fp.get("width"))
        self.fp_d = _mm(fp.get("depth"))

        self.dims: Dict[str, Tuple[Optional[int], Optional[int]]] = {}
        for name, spec in (self.raw.get("zones") or {}).items():
            spec = spec or {}
            self.dims[str(name)] = (_mm(spec.get("width")), _mm(spec.get("depth")))

        self.relations: List[Dict[str, Any]] = []
        for r in (self.raw.get("relations") or []):
            if isinstance(r, dict) and r.get("type"):
                self.relations.append(r)

        self.boxes: Dict[str, List[int]] = {}
        # (anchor, target, direction) triples that had an adjacency to a placed
        # zone but no way to resolve position along the shared wall.
        self.unaligned: List[Tuple[str, str, str]] = []

    # -- placement primitives ---------------------------------------------

    def _place(self, name: str, x0: int, y0: int, source: str) -> bool:
        w, d = self.dims.get(name, (None, None))
        if w is None or d is None:
            return False
        box = [x0, y0, x0 + w, y0 + d]
        existing = self.boxes.get(name)
        if existing is not None:
            if existing != box:
                self.diagnostics.append(_diag(
                    "D2_CONFLICT",
                    "Zone '%s' is placed inconsistently by two different "
                    "statements. One implies %s, the other %s (metres)." % (
                        name,
                        [_m(v) for v in existing],
                        [_m(v) for v in box],
                    ),
                    zone=name, source=source,
                ))
            return False
        self.boxes[name] = box
        return True

    def _anchor(self) -> None:
        """Place any zone fixed by a corner relation."""
        for r in self.relations:
            if r.get("type") != "corner":
                continue
            name = str(r.get("zone") or "")
            corner = _norm_corner(r.get("corner"))
            if name not in self.dims or corner is None:
                continue
            w, d = self.dims[name]
            if w is None or d is None:
                continue
            if self.fp_w is None or self.fp_d is None:
                self.diagnostics.append(_diag(
                    "D1_UNDERSPECIFIED",
                    "Zone '%s' is described as being in the %s corner, but no "
                    "overall plan footprint was given, so the corner has no "
                    "fixed position." % (name, corner),
                    zone=name,
                ))
                continue
            ew, ns = _CORNERS[corner]
            x0 = 0 if ew == "W" else self.fp_w - w
            y0 = 0 if ns == "S" else self.fp_d - d
            self._place(name, x0, y0, "corner")

        if not self.boxes and self.dims:
            # No corner given. Anchor the first zone at the origin; the layout
            # is then defined up to translation, which is harmless for E+.
            first = next(iter(self.dims))
            self._place(first, 0, 0, "default anchor")

    # -- relation lookup ---------------------------------------------------

    def _align_between(self, a: str, b: str) -> List[str]:
        """Cardinal edges asserted collinear between a and b."""
        edges = []
        for r in self.relations:
            if r.get("type") != "align":
                continue
            ra, rb = str(r.get("a") or ""), str(r.get("b") or "")
            if {ra, rb} != {a, b}:
                continue
            e = _norm_dir(r.get("edge"))
            if e:
                edges.append(e)
        return edges

    def _offset_origin(self, anchor: str, target: str, axis: str) -> Optional[int]:
        """Resolve position from an 'offset' relation: B's <edge> wall sits a
        stated distance from A's <edge> wall. Handles the relation being given
        in either order."""
        ax = self.boxes[anchor]
        tw, td = self.dims[target]
        t_len = td if axis == "y" else tw
        a_lo, a_hi = (ax[1], ax[3]) if axis == "y" else (ax[0], ax[2])
        valid = ("S", "N") if axis == "y" else ("W", "E")

        for r in self.relations:
            if r.get("type") != "offset":
                continue
            ra, rb = str(r.get("a") or ""), str(r.get("b") or "")
            if {ra, rb} != {anchor, target}:
                continue
            edge = _norm_dir(r.get("edge"))
            dist = _mm(r.get("distance"))
            if edge not in valid or dist is None:
                continue
            forward = (ra == anchor)          # relation reads a -> b
            if edge in ("W", "S"):
                return a_lo + dist if forward else a_lo - dist
            return (a_hi - dist - t_len) if forward else (a_hi + dist - t_len)
        return None

    def _center_origin(self, anchor: str, target: str, axis: str) -> Optional[int]:
        """Resolve position from a 'center' relation: B is centred on the wall
        it shares with A."""
        ax = self.boxes[anchor]
        tw, td = self.dims[target]
        if axis == "y":
            a_lo, a_len, t_len = ax[1], ax[3] - ax[1], td
        else:
            a_lo, a_len, t_len = ax[0], ax[2] - ax[0], tw

        for r in self.relations:
            if r.get("type") != "center":
                continue
            if {str(r.get("a") or ""), str(r.get("b") or "")} != {anchor, target}:
                continue
            return a_lo + (a_len - t_len) // 2
        return None

    def _parallel_origin(self, anchor: str, target: str, axis: str,
                         extent: str) -> Optional[int]:
        """Resolve the coordinate along the shared wall.

        axis 'y' for an east/west adjacency, 'x' for north/south.
        Returns the target's origin on that axis, or None if undetermined.
        """
        ax = self.boxes[anchor]
        tw, td = self.dims[target]
        aw = ax[2] - ax[0]
        ad = ax[3] - ax[1]

        if axis == "y":
            a_lo, a_hi, t_len, a_len = ax[1], ax[3], td, ad
        else:
            a_lo, a_hi, t_len, a_len = ax[0], ax[2], tw, aw

        # 1) Explicit alignment wins.
        for edge in self._align_between(anchor, target):
            if axis == "y" and edge == "S":
                return a_lo
            if axis == "y" and edge == "N":
                return a_hi - t_len
            if axis == "x" and edge == "W":
                return a_lo
            if axis == "x" and edge == "E":
                return a_hi - t_len

        # 2) An explicit offset from one of the anchor's walls.
        v = self._offset_origin(anchor, target, axis)
        if v is not None:
            return v

        # 3) Centred on the shared wall.
        v = self._center_origin(anchor, target, axis)
        if v is not None:
            return v

        # 4) Equal extents along the shared wall means the edges coincide.
        #    Derived from the DIMENSIONS, not from the stated "extent" label:
        #    an LLM may mark an adjacency "full" when the two rooms actually
        #    differ, and trusting that label silently stalls propagation.
        #    An explicit align (checked above) always takes priority.
        if t_len == a_len:
            return a_lo

        return None

    def _propagate(self) -> None:
        changed = True
        while changed:
            changed = False
            for r in self.relations:
                if r.get("type") != "adjacent":
                    continue
                a, b = str(r.get("a") or ""), str(r.get("b") or "")
                direction = _norm_dir(r.get("direction"))
                extent = str(r.get("extent") or "unspecified").strip().lower()
                if direction is None or a not in self.dims or b not in self.dims:
                    continue

                # b is to the <direction> of a. Try both orders.
                for anchor, target, d in ((a, b, direction),
                                          (b, a, _opposite(direction))):
                    if anchor not in self.boxes or target in self.boxes:
                        continue
                    tw, td = self.dims[target]
                    if tw is None or td is None:
                        continue
                    ax = self.boxes[anchor]

                    if d in ("E", "W"):
                        x0 = ax[2] if d == "E" else ax[0] - tw
                        y0 = self._parallel_origin(anchor, target, "y", extent)
                        if y0 is None:
                            self.unaligned.append((anchor, target, d))
                            continue
                    else:
                        y0 = ax[3] if d == "N" else ax[1] - td
                        x0 = self._parallel_origin(anchor, target, "x", extent)
                        if x0 is None:
                            self.unaligned.append((anchor, target, d))
                            continue

                    if self._place(target, x0, y0, "adjacent(%s,%s,%s)" % (a, b, direction)):
                        changed = True

    # -- validation --------------------------------------------------------

    def _check_alignments(self) -> None:
        for r in self.relations:
            if r.get("type") != "align":
                continue
            a, b = str(r.get("a") or ""), str(r.get("b") or "")
            edge = _norm_dir(r.get("edge"))
            if a not in self.boxes or b not in self.boxes or edge is None:
                continue
            idx = {"W": 0, "S": 1, "E": 2, "N": 3}[edge]
            if self.boxes[a][idx] != self.boxes[b][idx]:
                self.diagnostics.append(_diag(
                    "D2_CONFLICT",
                    "The %s walls of '%s' and '%s' were stated as flush, but "
                    "the resolved layout puts them at %.3f m and %.3f m." % (
                        edge, a, b,
                        _m(self.boxes[a][idx]), _m(self.boxes[b][idx]),
                    ),
                    zones=[a, b],
                ))

    def _check_overlaps(self) -> None:
        names = sorted(self.boxes)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                area = _overlap_area(self.boxes[a], self.boxes[b])
                if area > 0:
                    self.diagnostics.append(_diag(
                        "D5_OVERLAP",
                        "Zones '%s' and '%s' overlap by %.2f m2 in the "
                        "resolved layout." % (a, b, area / 1e6),
                        zones=[a, b],
                    ))

    def _check_footprint(self) -> None:
        if self.fp_w is None or self.fp_d is None or not self.boxes:
            return
        for name, box in sorted(self.boxes.items()):
            if box[0] < 0 or box[1] < 0 or box[2] > self.fp_w or box[3] > self.fp_d:
                self.diagnostics.append(_diag(
                    "D3_FOOTPRINT",
                    "Zone '%s' extends outside the stated %.2f x %.2f m plan "
                    "footprint." % (name, _m(self.fp_w), _m(self.fp_d)),
                    zone=name,
                ))
        total = sum((b[2] - b[0]) * (b[3] - b[1]) for b in self.boxes.values())
        if total > self.fp_w * self.fp_d:
            self.diagnostics.append(_diag(
                "D3_FOOTPRINT",
                "Stated room areas total %.2f m2, which exceeds the %.2f m2 "
                "plan footprint." % (total / 1e6, (self.fp_w * self.fp_d) / 1e6),
            ))
        # NOTE: a deficit is legitimate. Plans may contain open ground or
        # unenclosed area, as in the published Case 2 layout, so a shortfall is
        # deliberately not reported.

    def _check_connectivity(self) -> None:
        if len(self.boxes) < 2:
            return
        names = sorted(self.boxes)
        adj = {n: set() for n in names}
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                if _touches(self.boxes[a], self.boxes[b]):
                    adj[a].add(b)
                    adj[b].add(a)
        seen, stack = {names[0]}, [names[0]]
        while stack:
            cur = stack.pop()
            for nb in adj[cur]:
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        missing = [n for n in names if n not in seen]
        if missing:
            self.diagnostics.append(_diag(
                "D4_DISCONNECTED",
                "These zones do not share a wall with the rest of the "
                "building: %s." % ", ".join(missing),
                zones=missing,
            ))

    _DIRNAME = {"N": "north", "S": "south", "E": "east", "W": "west"}
    _PERP = {"N": ("east", "west"), "S": ("east", "west"),
             "E": ("north", "south"), "W": ("north", "south")}

    def _check_unplaced(self) -> None:
        missing = [n for n in sorted(self.dims) if n not in self.boxes]
        if not missing:
            return

        # Distinguish "no relation at all" from "an adjacency was given but
        # nothing says how the two rooms line up along the shared wall".
        # The second case is the common one and it is actionable.
        reported = set()
        for anchor, target, d in self.unaligned:
            if target not in missing or target in reported:
                continue
            reported.add(target)
            a_side, b_side = self._PERP[d]
            self.diagnostics.append(_diag(
                "D1_UNDERSPECIFIED",
                "'%s' is described as lying to the %s of '%s', but nothing "
                "states how the two line up along that shared wall and their "
                "extents differ. Add a statement such as \'with their %s walls "
                "flush\' or \'with their %s walls flush\'." % (
                    target, self._DIRNAME[d], anchor, a_side, b_side),
                zones=[anchor, target]))

        rest = [n for n in missing if n not in reported]
        if rest:
            self.diagnostics.append(_diag(
                "D1_UNDERSPECIFIED",
                "The position of these zones cannot be determined from the "
                "description: %s. Add an adjacency or a flush-wall statement "
                "linking each of them to a placed zone." % ", ".join(rest),
                zones=rest))

    # -- entry point -------------------------------------------------------

    def solve(self) -> Dict[str, List[List[float]]]:
        if not self.dims:
            self.diagnostics.append(_diag(
                "D1_UNDERSPECIFIED",
                "No room dimensions were found in the description."))
            return {}
        self._anchor()
        self._propagate()
        self._check_unplaced()
        self._check_alignments()
        self._check_overlaps()
        self._check_footprint()
        self._check_connectivity()
        return {name: _polygon(box) for name, box in self.boxes.items()}


def _opposite(direction: str) -> str:
    return {"N": "S", "S": "N", "E": "W", "W": "E"}[direction]


# --------------------------------------------------------------------------
# Opening feasibility (D6). Runs once geometry exists.
# --------------------------------------------------------------------------

def check_openings(rooms: Dict[str, List[List[float]]],
                   windows_ext: Dict[str, Any],
                   windows_int: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    def wall_length(poly: List[List[float]], ori: str) -> float:
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        return (max(xs) - min(xs)) if ori in ("N", "S") else (max(ys) - min(ys))

    for room, specs in (windows_ext or {}).items():
        if room not in rooms:
            out.append(_diag(
                "D6_OPENING",
                "A window is specified for '%s', but no such room exists in "
                "the layout." % room, zone=room))
            continue
        for s in specs or []:
            ori, w = s.get("ori"), s.get("w")
            if not ori or w is None:
                continue
            wl = wall_length(rooms[room], ori)
            if float(w) > wl:
                out.append(_diag(
                    "D6_OPENING",
                    "The %s window of '%s' is %.2f m wide but that wall is "
                    "only %.2f m long." % (ori, room, float(w), wl),
                    zone=room))

    for ig in (windows_int or []):
        for key in ("room_a", "room_b"):
            name = ig.get(key)
            if name and name not in rooms:
                out.append(_diag(
                    "D6_OPENING",
                    "An interior opening references '%s', which is not in the "
                    "layout." % name, zone=name))
    return out


# --------------------------------------------------------------------------
# LangGraph node
# --------------------------------------------------------------------------

def validate_supplied_rooms(rooms: Dict[str, List[List[float]]],
                            footprint: Optional[Dict[str, Any]] = None
                            ) -> List[Dict[str, Any]]:
    """Geometric checks for polygons supplied directly by the user.

    The relational channel gets D1..D6 for free because the solver builds the
    geometry. Explicit coordinates previously bypassed every check, so a typo
    in a vertex list reached EnergyPlus unexamined. This applies the
    input-independent subset (D3, D4, D5) to supplied polygons so that both
    input modes receive the same geometric quality assurance.

    D1 and D2 do not apply: coordinates are by definition fully determined and
    cannot contradict one another. D6 is applied separately by check_openings().
    """
    out: List[Dict[str, Any]] = []
    boxes: Dict[str, List[int]] = {}
    for name, poly in (rooms or {}).items():
        xs = [_mm(p[0]) for p in poly]
        ys = [_mm(p[1]) for p in poly]
        if not xs or not ys or None in xs or None in ys:
            continue
        boxes[name] = [min(xs), min(ys), max(xs), max(ys)]
    if not boxes:
        return out

    names = sorted(boxes)

    # D5: overlap
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            area = _overlap_area(boxes[a], boxes[b])
            if area > 0:
                out.append(_diag(
                    "D5_OVERLAP",
                    "Zones '%s' and '%s' overlap by %.2f m2 in the supplied "
                    "coordinates." % (a, b, area / 1e6), zones=[a, b]))

    # D3: footprint, only when the user stated one
    fp_w = _mm((footprint or {}).get("width"))
    fp_d = _mm((footprint or {}).get("depth"))
    if fp_w and fp_d:
        for name in names:
            x0, y0, x1, y1 = boxes[name]
            if x0 < 0 or y0 < 0 or x1 > fp_w or y1 > fp_d:
                out.append(_diag(
                    "D3_FOOTPRINT",
                    "Zone '%s' extends outside the stated %.2f x %.2f m plan "
                    "footprint." % (name, _m(fp_w), _m(fp_d)), zone=name))

    # D4: connectivity
    if len(names) > 1:
        adj = {n: set() for n in names}
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                if _touches(boxes[a], boxes[b]):
                    adj[a].add(b)
                    adj[b].add(a)
        seen, stack = {names[0]}, [names[0]]
        while stack:
            cur = stack.pop()
            for nb in adj[cur]:
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        missing = [n for n in names if n not in seen]
        if missing:
            out.append(_diag(
                "D4_DISCONNECTED",
                "These zones do not share a wall with the rest of the "
                "building: %s." % ", ".join(missing), zones=missing))
    return out


def solve_layout(state: SimulationState) -> SimulationState:
    """Resolve a relational layout into absolute room polygons.

    Pass-through when explicit coordinates were supplied, so the original
    input mode keeps working without change.
    """
    parsed = state.get("parsed_building_data") or {}

    if parsed.get("rooms"):
        # Explicit-coordinate channel. No layout to solve, but the geometric
        # checks still apply so that both input modes are held to the same
        # standard.
        rooms = parsed["rooms"]
        diags = validate_supplied_rooms(
            rooms, (parsed.get("layout") or {}).get("footprint"))
        diags += check_openings(rooms,
                                parsed.get("windows_ext") or {},
                                parsed.get("windows_int") or [])
        print("[SOLVER] channel=coordinates rooms=%d diagnostics=%s" % (
            len(rooms), [d["code"] for d in diags]), file=sys.stderr)
        state["layout_diagnostics"] = diags
        blocking = [d for d in diags if d["code"] != "D6_OPENING"]
        if blocking:
            state["errors"] = (state.get("errors") or []) + \
                [d["message"] for d in blocking]
        return state

    layout = parsed.get("layout") or state.get("relational_spec") or {}
    if not layout:
        state["layout_diagnostics"] = [_diag(
            "D1_UNDERSPECIFIED",
            "No room coordinates and no relative layout description were "
            "found in the input.")]
        state["errors"] = (state.get("errors") or []) + \
            [state["layout_diagnostics"][0]["message"]]
        return state

    solver = LayoutSolver(layout)
    rooms = solver.solve()
    diags = list(solver.diagnostics)

    if rooms:
        diags += check_openings(rooms,
                                parsed.get("windows_ext") or {},
                                parsed.get("windows_int") or [])

    # Trace on stderr so stdout stays pure JSON for server.js.
    print("[SOLVER] zones=%s relations=%d solved=%d diagnostics=%s" % (
        sorted((layout.get("zones") or {}).keys()),
        len(layout.get("relations") or []),
        len(rooms),
        [d["code"] for d in diags],
    ), file=sys.stderr)

    parsed["rooms"] = rooms
    parsed["solved_from_relations"] = True
    state["parsed_building_data"] = parsed
    state["layout_diagnostics"] = diags

    blocking = [d for d in diags if d["code"] != "D6_OPENING"]
    if blocking or not rooms:
        state["errors"] = (state.get("errors") or []) + \
            [d["message"] for d in blocking]

    return state
