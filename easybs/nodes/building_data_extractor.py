# -*- coding: utf-8 -*-
"""
Created on Mon Sep 22 14:08:39 2025

@author: Xiguan Liang  @SKKU
"""

# building_data_extractor.py
# ./nodes/building_data_extractor.py  (SINGLE-ZONE VERSION)
import os, json, re
from typing import Any, Dict
from openai import OpenAI
from state_schema import SimulationState

# ---------- utilities ----------
def _client():
    #key = os.getenv("OPENAI_API_KEY")
    key = "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"
    
    if not key:
        raise RuntimeError("OPENAI_API_KEY not set in environment.")
    return OpenAI(api_key=key)

def _to_float(x, d=None):
    try: return float(x)
    except: return d

def _to_int(x, d=None):
    try: return int(x)
    except: return d

def _norm_single(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize to the exact dict the single-zone generator expects."""
    out: Dict[str, Any] = {}
    out["length"] = _to_float(parsed.get("length"))
    out["width"] = _to_float(parsed.get("width"))
    out["floor_height"] = _to_float(parsed.get("floor_height"))
    out["floors"] = _to_int(parsed.get("floors"))
    out["orientation"] = _to_float(parsed.get("orientation"), 0.0) or 0.0
    out["location"] = parsed.get("location") or "Seoul, South Korea"

    # windows block
    wins = parsed.get("windows") or {}
    def _ww(side):
        w = wins.get(side) or {}
        return {
            "count": _to_int(w.get("count"), 0) or 0,
            "width": _to_float(w.get("width"), 1.5) or 1.5,
            "height": _to_float(w.get("height"), 1.5) or 1.5,
        }
    out["windows"] = {
        "north": _ww("north"),
        "east":  _ww("east"),
        "south": _ww("south"),
        "west":  _ww("west"),
    }
    return out

# very small regex fallback for single-zone prompts
_single_re = {
    "length": re.compile(r"\blength\s+is\s+([\d.]+)\s*m|\bis\s+([\d.]+)\s*meters\s+long", re.I),
    "width":  re.compile(r"\bwidth\s+is\s+([\d.]+)\s*m|\bis\s+([\d.]+)\s*meters\s+wide", re.I),
    "floors": re.compile(r"\b(\d+)\s*(?:storey|story|stories|floors?)\b", re.I),
    "floor_height": re.compile(r"\b(each\s+floor\s+is\s+)?([\d.]+)\s*m(?:eters)?\s+high", re.I),
    "orientation": re.compile(r"\borientation\s+is\s+(-?[\d.]+)\s*°?|north\s+axis\s+(-?[\d.]+)", re.I),
    "location": re.compile(r"\blocated\s+in\s+([A-Za-z ,'\-]+)", re.I),
    "win_all": re.compile(r"\b(\d+)\s+(north|east|south|west)[-\s]?facing\s+windows?\s*\(([\d.]+)\s*m?\s*x\s*([\d.]+)\s*m?\)", re.I),
}

def _fallback_parse(txt: str) -> Dict[str, Any]:
    d: Dict[str, Any] = {"windows": {}}
    def _first(m): 
        if not m: return None
        g = [g for g in m.groups() if g]
        return g[0] if g else None

    d["length"] = _to_float(_first(_single_re["length"].search(txt)))
    d["width"] = _to_float(_first(_single_re["width"].search(txt)))
    d["floors"] = _to_int(_first(_single_re["floors"].search(txt)))
    fh = _single_re["floor_height"].search(txt)
    d["floor_height"] = _to_float(_first(fh)) if fh else None
    ori = _single_re["orientation"].search(txt)
    d["orientation"] = _to_float(_first(ori), 0.0) or 0.0
    loc = _single_re["location"].search(txt)
    d["location"] = (loc.group(1).strip() if loc else "Seoul, South Korea")

    wins = {}
    for m in _single_re["win_all"].finditer(txt):
        count = _to_int(m.group(1), 0) or 0
        side = m.group(2).lower()
        w = _to_float(m.group(3), 1.5) or 1.5
        h = _to_float(m.group(4), 1.5) or 1.5
        wins[side] = {"count": count, "width": w, "height": h}
    # ensure all sides exist
    for side in ("north", "east", "south", "west"):
        wins.setdefault(side, {"count": 0, "width": 1.5, "height": 1.5})
    d["windows"] = wins
    return d

# ---------------- main node ----------------
PROMPT = """You are an EnergyPlus/ASHRAE assistant.
Extract SINGLE-ZONE building parameters from the user's text and return STRICT JSON only:

{
  "length": 20.8,
  "width": 14.2,
  "floor_height": 3.3,
  "floors": 3,
  "windows": {
    "north": {"count": 6, "width": 1.5, "height": 1.5},
    "east":  {"count": 5, "width": 1.5, "height": 1.5},
    "south": {"count": 7, "width": 1.5, "height": 1.5},
    "west":  {"count": 2, "width": 1.5, "height": 1.5}
  },
  "orientation": 45,
  "location": "Seoul, South Korea"
}
Return only JSON (no markdown, no code fences, no comments)."""

def extract_building_geometry(state: SimulationState) -> SimulationState:
    user_input = state.get("user_input") or ""
    if not user_input.strip():
        return {"errors": ["No user input available for geometry extraction."]}

    # fast path: allow UI to pass pre-parsed JSON (testing without LLM)
    override = state.get("parsed_building_data")
    if isinstance(override, dict) and ("length" in override and "width" in override):
        state["parsed_building_data"] = _norm_single(override)
        return state

    # try LLM path if key present
    try:
        client = _client()
        msg = f"{PROMPT}\n\nUser text:\n\"\"\"{user_input}\"\"\""
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": msg}],
            temperature=0.0,
        )
        content = (resp.choices[0].message.content or "").strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.S)

        parsed = json.loads(content)
        state["parsed_building_data"] = _norm_single(parsed)
        return state
    except Exception as e:
        # fallback parser (no LLM needed)
        fb = _fallback_parse(user_input)
        # if we still didn't get length/width/floors/floor_height, surface an error
        if not (fb.get("length") and fb.get("width") and fb.get("floors") and fb.get("floor_height")):
            return {"errors": [f"Extractor failed (LLM + fallback): {e.__class__.__name__}: {e}"]}
        state["parsed_building_data"] = _norm_single(fb)
        return state
