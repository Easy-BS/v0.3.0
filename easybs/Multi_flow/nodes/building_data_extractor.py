# -*- coding: utf-8 -*-
"""
Created on Mon Sep 22 14:08:39 2025

@author: Xiguan Liang @SKKU
"""

# ./Multi_flow/nodes/building_data_extractor.py
#
# CHANGE SUMMARY vs. the coordinate-only version
# ----------------------------------------------
# The LLM no longer emits geometry. It emits either
#   (a) "layout": a relational description (zone sizes + adjacency/alignment
#       relations), which nodes/layout_solver.py resolves to polygons, or
#   (b) "rooms": explicit polygons, used only when the user typed coordinates.
# Openings, orientation, floors and location are unchanged, and every
# normalisation helper below is unchanged except for the new _norm_layout().

import os
import sys
import json
import re
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from state_schema import SimulationState


OPENAI_URL = "https://api.openai.com/v1/chat/completions"


# --------------------------
# OpenAI helper (llm_router style)
# --------------------------

def _call_openai_chat_json(
    messages,
    model: str = "gpt-4o-mini",
    temperature: float = 0.0,
    timeout_s: int = 60
) -> Dict[str, Any]:
    """
    Calls OpenAI Chat Completions and parses the assistant response as JSON.
    API key is retrieved from environment variable OPENAI_API_KEY.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set in environment.")

    payload = {
        "model": model,
        "temperature": temperature,
        "messages": messages,
    }

    req = urllib.request.Request(
        OPENAI_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read().decode("utf-8")

    data = json.loads(raw)
    content = (data["choices"][0]["message"]["content"] or "").strip()

    # Trim accidental code fences if present
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.S).strip()

    return json.loads(content)


SCHEMA_EXAMPLE = {
  "floors": 1,
  "floor_height": 2.5,
  "orientation": 0.0,
  "location": "Seoul, South Korea",
  "layout": {
    "footprint": {"width": 9.0, "depth": 10.0},
    "zones": {
      "ExampleRoom": {"width": 3.0, "depth": 4.0},
      "AnotherRoom": {"width": 3.0, "depth": 3.0}
    },
    "relations": [
      {"type": "corner", "zone": "ExampleRoom", "corner": "SE"},
      {"type": "adjacent", "a": "ExampleRoom", "b": "AnotherRoom",
       "direction": "N"},
      {"type": "align", "a": "AnotherRoom", "b": "ExampleRoom", "edge": "W"}
    ]
  },
  "rooms": {},
  "windows_ext": {
    "ExampleRoom": [ {"ori": "S", "w": 1.5, "h": 1.2} ]
  },
  "windows_int": [
    {"room_a": "ExampleRoom", "room_b": "AnotherRoom",
     "w": 2.0, "h": 2.0, "subtype": "Door"}
  ]
}

PROMPT_TMPL = """You are an EnergyPlus/ASHRAE assistant.
Extract *multi-zone* building information from the user's text into STRICT JSON
matching this schema:

{schema}

You must NOT compute, invent, or infer any x/y coordinates. Coordinates are
produced by a downstream deterministic solver, not by you.

Choose exactly one geometry channel:
- If the user gives explicit vertex coordinates for rooms, copy them verbatim
  into "rooms" and leave "layout" empty.
- Otherwise, describe the plan relationally in "layout" and leave "rooms" empty.

Rules for "layout":
- footprint: overall plan extents in metres, if stated. "width" is the
  east-west extent, "depth" is the north-south extent. Omit if not given.
- zones: each room name -> {{"width": float, "depth": float}} in metres.
  "width" is east-west, "depth" is north-south. Record only stated values.
- relations: translate EVERY spatial statement in the text. Do not omit any.

  * {{"type":"corner","zone":str,"corner":"SW|SE|NW|NE"}}
    from "X is in the southwest corner".

  * {{"type":"adjacent","a":str,"b":str,"direction":"N|E|S|W"}}
    from "B is directly <direction> of A". Do NOT emit an "extent" field; the
    system computes it from the dimensions.

  * {{"type":"align","a":str,"b":str,"edge":"N|E|S|W"}}
    from "their west walls are flush", "with their south walls flush".

  * {{"type":"offset","a":str,"b":str,"edge":"N|E|S|W","distance":float}}
    from "B's west wall is 1.0 m east of A's west wall", or "B is set back
    0.8 m from A's east wall". "a" is the room the distance is measured FROM,
    "b" is the room being positioned, "edge" is the wall named on BOTH sides,
    and "distance" is in metres. Like align, "edge" must be perpendicular to
    the adjacency direction.

  * {{"type":"center","a":str,"b":str}}
    from "B is centered on A's north wall" or "B sits centrally on A".
    "a" is the room being centred ON, "b" is the room being positioned.
    No "edge" field.

Use offset or center for a room that sits partway along its neighbour's wall,
such as a recessed balcony narrower than the room it opens off.

CRITICAL: two different phrase types appear in these descriptions and they mean
different things. Do not confuse them.

  (i) "with their X walls flush", "their X walls line up", "X walls aligned"
      -> emit {{"type":"align","a":...,"b":...,"edge":"X"}}.
      X is ALWAYS perpendicular to the adjacency direction. If B is north or
      south of A, X can only be E or W. If B is east or west of A, X can only
      be N or S.

 (ii) "sharing A's entire north wall", "sharing the whole east wall",
      "shares its full south wall"
      -> this describes the CONTACT wall between the two rooms, i.e. that the
      shared wall spans the whole of it. It is NOT an alignment of the north or
      east edges. Emit ONLY the adjacency relation for this phrase. Do NOT emit
      an align.

Emit an align for every type (i) phrase and none for type (ii). Never emit an
align whose "edge" is parallel to the adjacency direction of the same pair.

Worked example. For the text:
  "Room_1 is 3.5 m wide and 3.7 m deep, in the southwest corner.
   The Closet is 2.3 m wide and 1.0 m deep, directly north of Room_1, with
   their west walls flush."
emit:
  {{"type":"corner","zone":"Room_1","corner":"SW"}},
  {{"type":"adjacent","a":"Room_1","b":"Closet","direction":"N"}},
  {{"type":"align","a":"Closet","b":"Room_1","edge":"W"}}
The align comes from "with their west walls flush" (type i), and W is
perpendicular to the N adjacency.

Second worked example, type (ii). For the text:
  "Room_2_Balcony is 3.5 m wide and 1.4 m deep, directly north of Room_2,
   sharing Room_2's entire north wall."
emit ONLY:
  {{"type":"adjacent","a":"Room_2","b":"Room_2_Balcony","direction":"N"}}
No align. "sharing its entire north wall" describes the contact wall. Emitting
{{"type":"align",...,"edge":"N"}} here would be wrong, because the balcony's
north edge is 1.4 m further north than Room_2's.

- All directions are building-relative. Do not rotate them by the orientation
  angle; that happens later.

Rules unchanged from before:
- Return JSON only (no markdown, no comments, no code fences).
- windows_ext: per room, list of {{"ori":"N|E|S|W","w":float,"h":float}}.
- windows_int: list of {{"room_a":str,"room_b":str,"w":float,"h":float,
  "subtype":"Window|Door"}}.
- floors (int), floor_height (m), orientation (deg, 0-360), location (string).
- If a field is missing, omit it. Do NOT guess a room dimension the user did
  not state; the solver reports missing information back to the user.

User text:
\"\"\"{user_text}\"\"\""""


# ------------------- normalization helpers -------------------

def _to_float(x, default=None):
    try:
        return float(x)
    except Exception:
        return default

def _to_int(x, default=None):
    try:
        return int(x)
    except Exception:
        return default

def _norm_orientation(deg):
    d = _to_float(deg, 0.0) or 0.0
    d = d % 360.0
    if d < 0:
        d += 360.0
    return d

def _upper_nesw(s):
    s = (s or "").strip().upper()
    return s if s in ("N", "E", "S", "W") else None

def _norm_rooms(rooms_in: Dict[str, Any]) -> Dict[str, List[List[float]]]:
    rooms_out: Dict[str, List[List[float]]] = {}
    if not isinstance(rooms_in, dict):
        return rooms_out
    for name, verts in rooms_in.items():
        clean: List[List[float]] = []
        if isinstance(verts, list):
            for p in verts:
                if isinstance(p, (list, tuple)) and len(p) >= 2:
                    x = _to_float(p[0], 0.0)
                    y = _to_float(p[1], 0.0)
                    clean.append([x, y])
        if len(clean) >= 3:
            rooms_out[str(name)] = clean
    return rooms_out


_CORNER_SET = ("SW", "SE", "NW", "NE")

_CARDINAL_ALIAS = {
    "N": "N", "NORTH": "N", "E": "E", "EAST": "E",
    "S": "S", "SOUTH": "S", "W": "W", "WEST": "W",
}

def _cardinal(value: Any) -> Optional[str]:
    """Accept 'N' or 'north' or 'North'. The previous version accepted only the
    single letter, so every relation written with a full word was silently
    discarded."""
    return _CARDINAL_ALIAS.get(str(value or "").strip().upper())

_CORNER_ALIAS = {
    "SW": "SW", "SOUTHWEST": "SW", "SOUTH-WEST": "SW",
    "SE": "SE", "SOUTHEAST": "SE", "SOUTH-EAST": "SE",
    "NW": "NW", "NORTHWEST": "NW", "NORTH-WEST": "NW",
    "NE": "NE", "NORTHEAST": "NE", "NORTH-EAST": "NE",
}

def _norm_layout(layout_in: Any) -> Dict[str, Any]:
    """Validate the relational description. Anything malformed is dropped here
    rather than reaching the solver, so solver diagnostics stay meaningful."""
    out: Dict[str, Any] = {"zones": {}, "relations": []}
    if not isinstance(layout_in, dict):
        return out

    fp = layout_in.get("footprint")
    if isinstance(fp, dict):
        w = _to_float(fp.get("width"), None)
        d = _to_float(fp.get("depth"), None)
        if w and d and w > 0 and d > 0:
            out["footprint"] = {"width": w, "depth": d}

    zones_in = layout_in.get("zones")
    if isinstance(zones_in, dict):
        for name, spec in zones_in.items():
            if not isinstance(spec, dict):
                continue
            z: Dict[str, float] = {}
            w = _to_float(spec.get("width"), None)
            d = _to_float(spec.get("depth"), None)
            if w and w > 0:
                z["width"] = w
            if d and d > 0:
                z["depth"] = d
            # A zone with no stated size is still recorded, so the solver can
            # name it in a D1_UNDERSPECIFIED diagnostic instead of silently
            # dropping it.
            out["zones"][str(name)] = z

    rels_in = layout_in.get("relations")
    if isinstance(rels_in, list):
        for r in rels_in:
            if not isinstance(r, dict):
                continue
            rtype = str(r.get("type") or "").strip().lower()

            if rtype == "corner":
                zone = str(r.get("zone") or "").strip()
                corner = _CORNER_ALIAS.get(
                    str(r.get("corner") or "").strip().upper().replace(" ", ""))
                if zone and corner:
                    out["relations"].append(
                        {"type": "corner", "zone": zone, "corner": corner})

            elif rtype in ("adjacent", "adjacency", "next_to"):
                a = str(r.get("a") or "").strip()
                b = str(r.get("b") or "").strip()
                direction = _cardinal(r.get("direction"))
                if a and b and a != b and direction:
                    # Derive 'extent' from the stated dimensions rather than
                    # trusting the model. Observed failure: an E/W adjacency
                    # between rooms 1.7 m and 5.6 m deep was labelled "full",
                    # and the model then omitted the align it needed.
                    key = "width" if direction in ("N", "S") else "depth"
                    da = (out["zones"].get(a) or {}).get(key)
                    db = (out["zones"].get(b) or {}).get(key)
                    if da is not None and db is not None:
                        extent = "full" if abs(da - db) < 1e-6 else "partial"
                    else:
                        extent = "unspecified"
                    out["relations"].append({
                        "type": "adjacent", "a": a, "b": b,
                        "direction": direction, "extent": extent})

            elif rtype in ("offset", "inset", "setback"):
                a = str(r.get("a") or "").strip()
                b = str(r.get("b") or "").strip()
                edge = _cardinal(r.get("edge"))
                dist = _to_float(r.get("distance") or r.get("d"), None)
                if a and b and a != b and edge and dist is not None and dist >= 0:
                    out["relations"].append({
                        "type": "offset", "a": a, "b": b,
                        "edge": edge, "distance": dist})

            elif rtype in ("center", "centre", "centered", "centred"):
                a = str(r.get("a") or "").strip()
                b = str(r.get("b") or "").strip()
                if a and b and a != b:
                    out["relations"].append({"type": "center", "a": a, "b": b})

            elif rtype in ("align", "aligned", "flush", "alignment"):
                a = str(r.get("a") or "").strip()
                b = str(r.get("b") or "").strip()
                edge = _cardinal(r.get("edge"))
                if a and b and a != b and edge:
                    out["relations"].append(
                        {"type": "align", "a": a, "b": b, "edge": edge})

    # An align between two rooms must be on an axis PERPENDICULAR to their
    # adjacency direction. If B is north of A, their north walls cannot be
    # flush (B's north edge is further north by construction) and neither can
    # their south walls. A parallel align is therefore always an extraction
    # error, most often produced by reading "sharing its entire north wall"
    # (which describes the CONTACT wall) as "their north walls are flush".
    # Drop those instead of letting them surface as D2 conflicts.
    _PARALLEL = {"N": ("N", "S"), "S": ("N", "S"),
                 "E": ("E", "W"), "W": ("E", "W")}
    _adj_dir = {}
    for r in out["relations"]:
        if r["type"] == "adjacent":
            _adj_dir[frozenset((r["a"], r["b"]))] = r["direction"]

    kept, dropped = [], []
    for r in out["relations"]:
        if r["type"] in ("align", "offset"):
            d = _adj_dir.get(frozenset((r["a"], r["b"])))
            if d and r["edge"] in _PARALLEL[d]:
                dropped.append(r)
                continue
        kept.append(r)
    if dropped:
        print("[EXTRACT] dropped %d align relation(s) parallel to their "
              "adjacency axis (these describe the shared wall, not a flush "
              "edge): %s" % (len(dropped), dropped), file=sys.stderr)
    out["relations"] = kept

    return out


def _norm_windows_ext(win_ext_in: Dict[str, Any]) -> Dict[str, List[Dict[str, float]]]:
    out: Dict[str, List[Dict[str, float]]] = {}
    if not isinstance(win_ext_in, dict):
        return out
    for room, specs in win_ext_in.items():
        lst: List[Dict[str, float]] = []
        if isinstance(specs, list):
            for s in specs:
                if not isinstance(s, dict):
                    continue
                ori = _upper_nesw(s.get("ori") or s.get("orientation"))
                w = _to_float(s.get("w") or s.get("width"), None)
                h = _to_float(s.get("h") or s.get("height"), None)
                if ori and w and h and w > 0 and h > 0:
                    lst.append({"ori": ori, "w": w, "h": h})
        if lst:
            out[str(room)] = lst
    return out

def _norm_windows_int(win_int_in: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(win_int_in, list):
        return out
    for s in win_int_in:
        if not isinstance(s, dict):
            continue
        room_a = s.get("room_a") or s.get("a") or s.get("from")
        room_b = s.get("room_b") or s.get("b") or s.get("to")
        w = _to_float(s.get("w") or s.get("width"), None)
        h = _to_float(s.get("h") or s.get("height"), None)
        subtype = (s.get("subtype") or "Window").strip().title()
        if room_a and room_b and w and h and w > 0 and h > 0:
            out.append({
                "room_a": str(room_a),
                "room_b": str(room_b),
                "w": w,
                "h": h,
                "subtype": subtype
            })
    return out

def _postprocess(parsed: Dict[str, Any], state: SimulationState) -> Dict[str, Any]:
    cleaned: Dict[str, Any] = {}
    cleaned["floors"] = _to_int(parsed.get("floors"), 1) or 1
    cleaned["floor_height"] = _to_float(parsed.get("floor_height"), 2.5) or 2.5
    cleaned["orientation"] = _norm_orientation(parsed.get("orientation"))
    cleaned["location"] = parsed.get("location") or "Seoul, South Korea"
    cleaned["rooms"] = _norm_rooms(parsed.get("rooms") or {})
    cleaned["layout"] = _norm_layout(parsed.get("layout") or {})
    cleaned["windows_ext"] = _norm_windows_ext(parsed.get("windows_ext") or {})
    cleaned["windows_int"] = _norm_windows_int(parsed.get("windows_int") or [])

    # Record which channel the geometry came from, for logging and for the
    # ablation reported in the paper.
    cleaned["geometry_source"] = "coordinates" if cleaned["rooms"] else "relations"

    default_out = state.get("idf_path") or os.path.abspath("./generated_idfs/geom_multizone.idf")
    cleaned["out_idf"] = parsed.get("out_idf") or default_out
    return cleaned


# ------------------- main node -------------------

def extract_building_geometry(state: SimulationState) -> SimulationState:
    """
    Multi-zone only extractor.
    If state already contains 'parsed_building_data' (override), it is normalized
    and used directly. Otherwise, the LLM is called and the result is normalized.

    The LLM produces either explicit 'rooms' polygons or a relational 'layout'.
    Resolving 'layout' into polygons is the job of nodes/layout_solver.py.
    """
    # 1) Fast path: pre-parsed override
    override = state.get("parsed_building_data")
    if isinstance(override, dict) and (
        override.get("rooms") or override.get("layout") or override.get("windows_ext")
    ):
        state["parsed_building_data"] = _postprocess(override, state)
        return state

    # 2) LLM path
    user_input = state.get("user_input") or ""
    if not user_input.strip():
        return {"errors": ["No user input available for geometry extraction."]}

    prompt = PROMPT_TMPL.format(
        schema=json.dumps(SCHEMA_EXAMPLE, ensure_ascii=False, indent=2),
        user_text=user_input
    )

    try:
        messages = [{"role": "user", "content": prompt}]
        model = state.get("model") or "gpt-4o-mini"
        raw = _call_openai_chat_json(messages=messages, model=model, temperature=0.0)

        print("[EXTRACT] raw LLM layout = %s" % json.dumps(
            raw.get("layout") or {}, ensure_ascii=False), file=sys.stderr)

        state["parsed_building_data"] = _postprocess(raw, state)
        _lay = state["parsed_building_data"].get("layout") or {}
        _rels = _lay.get("relations") or []
        _partial = {(r["a"], r["b"]) for r in _rels
                    if r["type"] == "adjacent" and r["extent"] != "full"}
        _aligned = {(r["a"], r["b"]) for r in _rels if r["type"] == "align"}
        _aligned |= {(b, a) for (a, b) in _aligned}
        _missing = [p for p in _partial if p not in _aligned]
        print("[EXTRACT] kept %d relations | partial adjacencies without an "
              "align: %s" % (len(_rels), _missing or "none"), file=sys.stderr)
        state["relational_spec"] = state["parsed_building_data"].get("layout") or {}
        return state

    except json.JSONDecodeError as e:
        # Preserve prior behavior: return parse error with raw content if available
        return {"errors": [f"JSON parsing error: {str(e)}"]}
    except Exception as e:
        return {"errors": [str(e)]}
